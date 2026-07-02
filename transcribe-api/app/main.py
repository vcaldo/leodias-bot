"""transcribe-api: stateless HTTP service wrapping a Whisper backend."""

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from .config import settings
from .models import HealthResponse, InfoResponse, SegmentOut, TranscribeResponse
from .observability import (
    add_transaction_attributes,
    background_task,
    notice_error,
    trace_segment,
)
from .transcriber import create_backend

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("transcribe-api")

TMP_DIR = Path("/tmp")
UPLOAD_CHUNK_SIZE = 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.started_at = time.monotonic()
    app.state.ready = False
    app.state.transcribe_lock = asyncio.Lock()
    app.state.backend = create_backend(settings)

    async def load_model() -> None:
        t0 = time.monotonic()
        logger.info(
            "loading model=%s backend=%s device=%s compute_type=%s",
            settings.model, settings.backend, settings.device, settings.compute_type,
        )
        try:
            with background_task("model_load"):
                await asyncio.to_thread(app.state.backend.load)
        except Exception:
            logger.exception("model load failed")
            raise
        app.state.ready = True
        logger.info("model loaded in %.1fs", time.monotonic() - t0)

    load_task = asyncio.create_task(load_model())
    yield
    load_task.cancel()


app = FastAPI(title="transcribe-api", lifespan=lifespan)


def check_api_key(x_api_key: str | None) -> None:
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="missing or invalid API key")


async def run_command(*cmd: str) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout, stderr


async def probe_duration(path: Path) -> float:
    code, stdout, _ = await run_command(
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    )
    if code != 0:
        raise HTTPException(status_code=400, detail="could not read audio duration")
    try:
        return float(stdout.decode().strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="could not read audio duration")


@app.get("/v1/health")
async def health(request: Request):
    payload = HealthResponse(
        status="ok" if request.app.state.ready else "loading",
        model=settings.model,
        device=settings.device,
        uptime_seconds=round(time.monotonic() - request.app.state.started_at, 1),
    )
    status_code = 200 if request.app.state.ready else 503
    return JSONResponse(status_code=status_code, content=payload.model_dump())


@app.get("/v1/info", response_model=InfoResponse)
async def info(request: Request):
    return InfoResponse(
        model=settings.model,
        device=settings.device,
        compute_type=settings.compute_type,
        backend=request.app.state.backend.name,
        max_upload_mb=settings.max_upload_mb,
        max_audio_seconds=settings.max_audio_seconds,
    )


@app.post("/v1/transcribe", response_model=TranscribeResponse)
async def transcribe(
    request: Request,
    file: UploadFile = File(...),
    language: str | None = Form(None),
    task: str = Form("transcribe"),
    x_api_key: str | None = Header(None),
):
    check_api_key(x_api_key)

    if not request.app.state.ready:
        raise HTTPException(status_code=503, detail="model is still loading, retry shortly")
    if task not in ("transcribe", "translate"):
        raise HTTPException(status_code=400, detail="task must be 'transcribe' or 'translate'")

    started = time.monotonic()
    language = language or settings.language_default
    job_id = uuid.uuid4().hex
    suffix = Path(file.filename or "audio").suffix or ".bin"
    input_path = TMP_DIR / f"transcribe-{job_id}-in{suffix}"
    wav_path = TMP_DIR / f"transcribe-{job_id}-norm.wav"

    add_transaction_attributes({
        "model_size": settings.model,
        "device": settings.device,
        "compute_type": settings.compute_type,
        "language": language,
    })

    try:
        # 1. save upload to /tmp, enforcing the size limit while streaming
        size = 0
        with trace_segment("file_io/save_upload"):
            with open(input_path, "wb") as out:
                while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=f"file exceeds {settings.max_upload_mb}MB limit",
                        )
                    out.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="empty file")

        # 2. normalize to WAV 16kHz mono
        with trace_segment("ffmpeg/normalize"):
            code, _, stderr = await run_command(
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(input_path), "-ar", "16000", "-ac", "1", "-f", "wav",
                str(wav_path),
            )
        if code != 0:
            logger.warning("ffmpeg failed for %s: %s", file.filename, stderr.decode()[-500:])
            raise HTTPException(status_code=400, detail="invalid or undecodable audio file")

        duration = await probe_duration(wav_path)
        if duration > settings.max_audio_seconds:
            raise HTTPException(
                status_code=413,
                detail=f"audio exceeds {settings.max_audio_seconds}s limit",
            )
        add_transaction_attributes({"audio_duration_seconds": round(duration, 1)})

        # 3. run the model (serialized: one inference at a time)
        with trace_segment("model/transcribe"):
            async with request.app.state.transcribe_lock:
                result = await asyncio.to_thread(
                    request.app.state.backend.transcribe, str(wav_path), language, task
                )

        processing_time = time.monotonic() - started
        logger.info(
            "transcribed %s: audio=%.1fs processing=%.1fs language=%s",
            file.filename, duration, processing_time, result.language,
        )
        return TranscribeResponse(
            text=result.text,
            language=result.language,
            language_probability=result.language_probability,
            duration_seconds=round(duration, 1),
            segments=[SegmentOut(start=s.start, end=s.end, text=s.text) for s in result.segments],
            model=settings.model,
            processing_time_seconds=round(processing_time, 1),
        )
    except HTTPException:
        raise
    except Exception:
        notice_error()
        logger.exception("transcription failed for %s", file.filename)
        raise HTTPException(status_code=500, detail="internal transcription error")
    finally:
        # 4. always clean up temp files
        with trace_segment("file_io/cleanup"):
            input_path.unlink(missing_ok=True)
            wav_path.unlink(missing_ok=True)
