"""Configuration for transcribe-api, sourced entirely from environment variables."""

import os


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"invalid integer for {name}: {raw!r}")


class Settings:
    def __init__(self) -> None:
        # transcription backend: faster-whisper (default) or whisper-cpp
        self.backend = os.environ.get("TRANSCRIBE_BACKEND", "faster-whisper")

        self.model = os.environ.get("WHISPER_MODEL", "small")
        self.device = os.environ.get("WHISPER_DEVICE", "cpu")
        self.compute_type = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
        self.language_default = os.environ.get("WHISPER_LANGUAGE_DEFAULT", "pt")

        # whisper.cpp backend only
        self.whisper_cpp_bin = os.environ.get("WHISPER_CPP_BIN", "whisper-cli")
        self.whisper_cpp_model_path = os.environ.get("WHISPER_CPP_MODEL_PATH", "")

        self.max_upload_mb = _env_int("MAX_UPLOAD_MB", 25)
        self.max_audio_seconds = _env_int("MAX_AUDIO_SECONDS", 1800)
        self.api_key = os.environ.get("API_KEY", "")
        self.port = _env_int("PORT", 8000)
        self.log_level = os.environ.get("LOG_LEVEL", "info")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


settings = Settings()
