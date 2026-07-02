"""Transcription backends: faster-whisper (default) and whisper.cpp."""

import json
import logging
import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger("transcribe-api")


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptionResult:
    text: str
    language: str
    language_probability: float | None
    segments: list[Segment] = field(default_factory=list)


class Backend(ABC):
    name: str

    @abstractmethod
    def load(self) -> None:
        """Blocking load/validation of the model. Called once at startup."""

    @abstractmethod
    def transcribe(self, wav_path: str, language: str, task: str) -> TranscriptionResult:
        """Blocking transcription of a 16kHz mono WAV file."""


class FasterWhisperBackend(Backend):
    name = "faster-whisper"

    def __init__(self, model_size: str, device: str, compute_type: str) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def load(self) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(
            self.model_size, device=self.device, compute_type=self.compute_type
        )

    def transcribe(self, wav_path: str, language: str, task: str) -> TranscriptionResult:
        segments_iter, info = self._model.transcribe(
            wav_path, language=language or None, task=task
        )
        segments = [
            Segment(start=round(s.start, 2), end=round(s.end, 2), text=s.text.strip())
            for s in segments_iter
        ]
        text = " ".join(s.text for s in segments).strip()
        return TranscriptionResult(
            text=text,
            language=info.language,
            language_probability=round(info.language_probability, 4),
            segments=segments,
        )


class WhisperCppBackend(Backend):
    """Runs the whisper.cpp CLI (whisper-cli) as a subprocess with JSON output."""

    name = "whisper-cpp"

    def __init__(self, bin_path: str, model_path: str) -> None:
        self.bin_path = bin_path
        self.model_path = model_path

    def load(self) -> None:
        if shutil.which(self.bin_path) is None:
            raise RuntimeError(f"whisper.cpp binary not found: {self.bin_path}")
        if not self.model_path or not os.path.isfile(self.model_path):
            raise RuntimeError(
                f"whisper.cpp model file not found: {self.model_path!r} "
                "(set WHISPER_CPP_MODEL_PATH)"
            )

    def transcribe(self, wav_path: str, language: str, task: str) -> TranscriptionResult:
        out_prefix = wav_path + ".out"
        cmd = [
            self.bin_path,
            "-m", self.model_path,
            "-f", wav_path,
            "-oj",          # write JSON output
            "-of", out_prefix,
            "-np",          # no progress prints
        ]
        if language:
            cmd += ["-l", language]
        if task == "translate":
            cmd += ["--translate"]

        json_path = out_prefix + ".json"
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                logger.error("whisper.cpp failed: %s", proc.stderr[-2000:])
                raise RuntimeError(f"whisper.cpp exited with code {proc.returncode}")
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
        finally:
            try:
                os.unlink(json_path)
            except FileNotFoundError:
                pass

        segments = [
            Segment(
                start=round(item["offsets"]["from"] / 1000.0, 2),
                end=round(item["offsets"]["to"] / 1000.0, 2),
                text=item["text"].strip(),
            )
            for item in data.get("transcription", [])
        ]
        text = " ".join(s.text for s in segments).strip()
        detected = data.get("result", {}).get("language", language or "")
        return TranscriptionResult(
            text=text,
            language=detected,
            language_probability=None,  # whisper.cpp does not report it
            segments=segments,
        )


def create_backend(settings) -> Backend:
    if settings.backend == "faster-whisper":
        return FasterWhisperBackend(settings.model, settings.device, settings.compute_type)
    if settings.backend == "whisper-cpp":
        return WhisperCppBackend(settings.whisper_cpp_bin, settings.whisper_cpp_model_path)
    raise RuntimeError(
        f"unknown TRANSCRIBE_BACKEND: {settings.backend!r} "
        "(expected 'faster-whisper' or 'whisper-cpp')"
    )
