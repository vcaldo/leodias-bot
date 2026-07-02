FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Download the model at build time so cold starts don't hit the network.
ARG WHISPER_MODEL=small
ENV WHISPER_MODEL=${WHISPER_MODEL}
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('${WHISPER_MODEL}', device='cpu', compute_type='int8')"

COPY newrelic.ini entrypoint.sh ./
COPY app ./app
RUN chmod +x entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT:-8000}/v1/health" || exit 1

CMD ["./entrypoint.sh"]
