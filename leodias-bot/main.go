package main

import (
	"context"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	tgbotapi "github.com/go-telegram-bot-api/telegram-bot-api/v5"
	"github.com/newrelic/go-agent/v3/newrelic"
)

const telegramMaxMessageLen = 4096

type config struct {
	BotToken       string
	APIURL         string
	APIKey         string
	FallbackURL    string
	TimeoutSeconds int
	MaxFileSizeMB  int
}

func loadConfig() (*config, error) {
	cfg := &config{
		BotToken:       os.Getenv("TELEGRAM_BOT_TOKEN"),
		APIURL:         os.Getenv("TRANSCRIBE_API_URL"),
		APIKey:         os.Getenv("TRANSCRIBE_API_KEY"),
		FallbackURL:    os.Getenv("TRANSCRIBE_FALLBACK_URL"),
		TimeoutSeconds: envInt("TRANSCRIBE_TIMEOUT_SECONDS", 120),
		MaxFileSizeMB:  envInt("MAX_FILE_SIZE_MB", 25),
	}
	if cfg.BotToken == "" {
		return nil, fmt.Errorf("TELEGRAM_BOT_TOKEN is required")
	}
	if cfg.APIURL == "" {
		return nil, fmt.Errorf("TRANSCRIBE_API_URL is required")
	}
	return cfg, nil
}

func envInt(name string, def int) int {
	raw := strings.TrimSpace(os.Getenv(name))
	if raw == "" {
		return def
	}
	v, err := strconv.Atoi(raw)
	if err != nil {
		log.Fatalf("invalid integer for %s: %q", name, raw)
	}
	return v
}

// newRelicApp initializes the agent from NEW_RELIC_* env vars. Returns nil
// (all agent calls become no-ops) when disabled or no license key is set.
func newRelicApp() *newrelic.Application {
	if strings.EqualFold(os.Getenv("NEW_RELIC_ENABLED"), "false") ||
		os.Getenv("NEW_RELIC_LICENSE_KEY") == "" {
		log.Println("new relic disabled (NEW_RELIC_ENABLED=false or no license key)")
		return nil
	}
	app, err := newrelic.NewApplication(
		newrelic.ConfigAppName("leodias-bot"), // default; NEW_RELIC_APP_NAME overrides
		newrelic.ConfigFromEnvironment(),
	)
	if err != nil {
		log.Printf("new relic init failed, continuing without it: %v", err)
		return nil
	}
	return app
}

func main() {
	cfg, err := loadConfig()
	if err != nil {
		log.Fatal(err)
	}

	nrApp := newRelicApp()

	bot, err := tgbotapi.NewBotAPI(cfg.BotToken)
	if err != nil {
		log.Fatalf("telegram init failed: %v", err)
	}
	log.Printf("authorized as @%s", bot.Self.UserName)

	httpClient := &http.Client{Timeout: time.Duration(cfg.TimeoutSeconds) * time.Second}
	primary := &TranscribeClient{BaseURL: cfg.APIURL, APIKey: cfg.APIKey, Client: httpClient}
	var fallback *TranscribeClient
	if cfg.FallbackURL != "" {
		fallback = &TranscribeClient{BaseURL: cfg.FallbackURL, APIKey: cfg.APIKey, Client: httpClient}
	}

	u := tgbotapi.NewUpdate(0)
	u.Timeout = 30
	for update := range bot.GetUpdatesChan(u) {
		if update.Message == nil {
			continue
		}
		go handleMessage(nrApp, bot, cfg, primary, fallback, update.Message)
	}
}

// audioRef points at an audio attachment on a Telegram message.
type audioRef struct {
	FileID   string
	FileName string
	FileSize int
}

var allowedExtensions = map[string]bool{
	".mp3": true, ".wav": true, ".ogg": true, ".oga": true, ".opus": true,
	".m4a": true, ".aac": true, ".flac": true, ".wma": true, ".amr": true,
}

func extractAudio(msg *tgbotapi.Message) *audioRef {
	if msg == nil {
		return nil
	}
	if msg.Voice != nil {
		return &audioRef{FileID: msg.Voice.FileID, FileName: "voice.ogg", FileSize: msg.Voice.FileSize}
	}
	if msg.Audio != nil {
		name := msg.Audio.FileName
		if name == "" {
			name = "audio.mp3"
		}
		return &audioRef{FileID: msg.Audio.FileID, FileName: name, FileSize: msg.Audio.FileSize}
	}
	if msg.Document != nil {
		name := msg.Document.FileName
		if strings.HasPrefix(msg.Document.MimeType, "audio/") || allowedExtensions[strings.ToLower(filepath.Ext(name))] {
			return &audioRef{FileID: msg.Document.FileID, FileName: name, FileSize: msg.Document.FileSize}
		}
	}
	return nil
}

func handleMessage(nrApp *newrelic.Application, bot *tgbotapi.BotAPI, cfg *config, primary, fallback *TranscribeClient, msg *tgbotapi.Message) {
	// audio attached directly, or /transcribe replying to an audio message
	audio := extractAudio(msg)
	if audio == nil && msg.IsCommand() && msg.Command() == "transcribe" {
		audio = extractAudio(msg.ReplyToMessage)
		if audio == nil {
			reply(bot, msg, "Reply to an audio message with /transcribe, or just send me the audio.")
			return
		}
	}
	if audio == nil {
		if msg.IsCommand() && (msg.Command() == "start" || msg.Command() == "help") {
			reply(bot, msg, "Send me an audio message (voice note or file) and I'll transcribe it. You can also reply to an audio message with /transcribe.")
		}
		return
	}

	txn := nrApp.StartTransaction("transcribe_message")
	defer txn.End()
	ctx := newrelic.NewContext(context.Background(), txn)
	txn.AddAttribute("chat_id", msg.Chat.ID)
	txn.AddAttribute("file_size_bytes", audio.FileSize)

	// validate size locally before spending bandwidth
	validateSeg := txn.StartSegment("telegram/validate")
	maxBytes := cfg.MaxFileSizeMB * 1024 * 1024
	tooBig := audio.FileSize > maxBytes
	validateSeg.End()
	if tooBig {
		reply(bot, msg, fmt.Sprintf("This file is too big — the limit is %dMB.", cfg.MaxFileSizeMB))
		return
	}

	downloadSeg := txn.StartSegment("telegram/download")
	data, err := downloadFile(ctx, bot, audio.FileID, maxBytes)
	downloadSeg.End()
	if err != nil {
		txn.NoticeError(err)
		log.Printf("chat %d: download failed: %v", msg.Chat.ID, err)
		reply(bot, msg, "I couldn't download that file from Telegram, please try again.")
		return
	}

	// primary API, then fallback if configured
	usedFallback := false
	endpointUsed := primary.BaseURL
	result, err := primary.Transcribe(ctx, audio.FileName, data)
	if err != nil && fallback != nil && shouldFallback(err) {
		txn.NoticeError(err)
		log.Printf("chat %d: primary failed (%v), trying fallback", msg.Chat.ID, err)
		fallbackSeg := txn.StartSegment("transcribe/fallback")
		result, err = fallback.Transcribe(ctx, audio.FileName, data)
		fallbackSeg.End()
		usedFallback = true
		endpointUsed = fallback.BaseURL
	}
	txn.AddAttribute("used_fallback", usedFallback)
	txn.AddAttribute("api_endpoint_used", endpointUsed)
	if err != nil {
		txn.NoticeError(err)
		log.Printf("chat %d: transcription failed: %v", msg.Chat.ID, err)
		reply(bot, msg, friendlyError(err))
		return
	}

	replySeg := txn.StartSegment("telegram/reply")
	err = sendTranscription(bot, msg, result.Text)
	replySeg.End()
	if err != nil {
		txn.NoticeError(err)
		log.Printf("chat %d: sending reply failed: %v", msg.Chat.ID, err)
	}
}

// shouldFallback reports whether the fallback API is worth trying: transport
// errors, timeouts and server-side failures — not client errors like 400/413,
// which would just fail again.
func shouldFallback(err error) bool {
	if apiErr, ok := err.(*APIError); ok {
		return apiErr.StatusCode >= 500
	}
	return true
}

func friendlyError(err error) string {
	if apiErr, ok := err.(*APIError); ok {
		switch apiErr.StatusCode {
		case http.StatusRequestEntityTooLarge:
			return "This audio is too large or too long for the transcription service."
		case http.StatusBadRequest:
			return "I couldn't decode that audio — is it a valid audio file?"
		case http.StatusUnauthorized:
			return "The transcription service rejected my credentials. Please check the bot configuration."
		case http.StatusServiceUnavailable:
			return "The transcription service is still starting up, try again in a minute."
		default:
			return "The transcription service had a problem, please try again later."
		}
	}
	return "I couldn't reach the transcription service, please try again later."
}

func downloadFile(ctx context.Context, bot *tgbotapi.BotAPI, fileID string, maxBytes int) ([]byte, error) {
	url, err := bot.GetFileDirectURL(fileID)
	if err != nil {
		return nil, fmt.Errorf("resolving file url: %w", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("downloading file: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("downloading file: status %d", resp.StatusCode)
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, int64(maxBytes)+1))
	if err != nil {
		return nil, fmt.Errorf("reading file: %w", err)
	}
	if len(data) > maxBytes {
		return nil, fmt.Errorf("file larger than %d bytes", maxBytes)
	}
	return data, nil
}

// sendTranscription replies with the text, or with a .txt attachment when it
// doesn't fit in a single Telegram message.
func sendTranscription(bot *tgbotapi.BotAPI, msg *tgbotapi.Message, text string) error {
	if text == "" {
		return reply(bot, msg, "I couldn't detect any speech in that audio.")
	}
	if len(text) <= telegramMaxMessageLen {
		return reply(bot, msg, text)
	}
	doc := tgbotapi.NewDocument(msg.Chat.ID, tgbotapi.FileBytes{
		Name:  "transcription.txt",
		Bytes: []byte(text),
	})
	doc.ReplyToMessageID = msg.MessageID
	doc.Caption = "The transcription was too long for a message, here it is as a file."
	_, err := bot.Send(doc)
	return err
}

func reply(bot *tgbotapi.BotAPI, msg *tgbotapi.Message, text string) error {
	m := tgbotapi.NewMessage(msg.Chat.ID, text)
	m.ReplyToMessageID = msg.MessageID
	_, err := bot.Send(m)
	return err
}
