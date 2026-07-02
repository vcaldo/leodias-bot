# Audio Transcription Service

Two decoupled services:

- **`transcribe-api/`** — Python/FastAPI service exposing `POST /v1/transcribe`
  (plus `/v1/health` and `/v1/info`). Backends: `faster-whisper` (default) or
  `whisper.cpp` via `TRANSCRIBE_BACKEND=whisper-cpp`.
- **`leodias-bot/`** — Go Telegram bot. Send it an audio/voice message (or reply
  to one with `/transcribe`) and it replies with the transcription, as a `.txt`
  file when too long for a Telegram message.

Both are instrumented with the official New Relic agents (distributed tracing
across bot → API), fully configured via `NEW_RELIC_*` env vars and disabled
automatically when no license key is set.

## Run

```sh
cp .env.example .env   # fill in TELEGRAM_BOT_TOKEN (and API_KEY / NR key)
docker compose up --build
```

The Whisper model is baked into the API image at build time
(`WHISPER_MODEL` build arg, default `small`).

### whisper.cpp backend (e.g. Raspberry Pi)

The code supports it out of the box; the default image only ships
faster-whisper. To use whisper.cpp, make the `whisper-cli` binary and a ggml
model available (native install or custom image) and set:

```sh
TRANSCRIBE_BACKEND=whisper-cpp
WHISPER_CPP_BIN=/usr/local/bin/whisper-cli
WHISPER_CPP_MODEL_PATH=/models/ggml-small.bin
```

## Pointing the bot elsewhere

`TRANSCRIBE_API_URL` (and optional `TRANSCRIBE_FALLBACK_URL`) can target any
deploy — Pi, Linode, etc — without rebuilding the bot.
