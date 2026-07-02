package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"

	"github.com/newrelic/go-agent/v3/newrelic"
)

// TranscribeClient talks to one transcribe-api deployment.
type TranscribeClient struct {
	BaseURL string
	APIKey  string
	Client  *http.Client
}

type TranscribeResponse struct {
	Text     string  `json:"text"`
	Language string  `json:"language"`
	Duration float64 `json:"duration_seconds"`
	Model    string  `json:"model"`
}

// APIError is a non-200 response from transcribe-api.
type APIError struct {
	StatusCode int
	Body       string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("transcribe-api returned %d: %s", e.StatusCode, e.Body)
}

// Transcribe uploads the audio as multipart/form-data and decodes the JSON
// response. The outbound call is wrapped in a New Relic external segment,
// which also injects distributed-tracing headers into the request.
func (c *TranscribeClient) Transcribe(ctx context.Context, filename string, data []byte) (*TranscribeResponse, error) {
	var buf bytes.Buffer
	writer := multipart.NewWriter(&buf)
	part, err := writer.CreateFormFile("file", filename)
	if err != nil {
		return nil, fmt.Errorf("building multipart body: %w", err)
	}
	if _, err := part.Write(data); err != nil {
		return nil, fmt.Errorf("building multipart body: %w", err)
	}
	if err := writer.Close(); err != nil {
		return nil, fmt.Errorf("building multipart body: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL, &buf)
	if err != nil {
		return nil, fmt.Errorf("building request: %w", err)
	}
	req.Header.Set("Content-Type", writer.FormDataContentType())
	if c.APIKey != "" {
		req.Header.Set("X-API-Key", c.APIKey)
	}

	txn := newrelic.FromContext(ctx)
	seg := newrelic.StartExternalSegment(txn, req)
	resp, err := c.Client.Do(req)
	seg.Response = resp
	seg.End()
	if err != nil {
		return nil, fmt.Errorf("calling transcribe-api: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return nil, &APIError{StatusCode: resp.StatusCode, Body: string(body)}
	}

	var out TranscribeResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, fmt.Errorf("decoding response: %w", err)
	}
	return &out, nil
}
