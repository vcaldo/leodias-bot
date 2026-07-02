"""Pydantic response models for the public API."""

from pydantic import BaseModel


class SegmentOut(BaseModel):
    start: float
    end: float
    text: str


class TranscribeResponse(BaseModel):
    text: str
    language: str
    language_probability: float | None
    duration_seconds: float
    segments: list[SegmentOut]
    model: str
    processing_time_seconds: float


class HealthResponse(BaseModel):
    status: str
    model: str
    device: str
    uptime_seconds: float


class InfoResponse(BaseModel):
    model: str
    device: str
    compute_type: str
    backend: str
    max_upload_mb: int
    max_audio_seconds: int
