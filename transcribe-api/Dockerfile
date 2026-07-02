# whisper.cpp builder — same base as the runtime stage so glibc matches.
# BUILD_SHARED_LIBS=OFF folds libwhisper/libggml into the binary, so the
# runtime stage only needs the single whisper-cli file (plus libgomp1).
FROM python:3.14-slim AS whispercpp
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ARG WHISPER_CPP_VERSION=v1.9.1
RUN git clone --depth 1 --branch ${WHISPER_CPP_VERSION} https://github.com/ggml-org/whisper.cpp /src \
    && cmake -S /src -B /src/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_SHARED_LIBS=OFF \
        -DWHISPER_BUILD_TESTS=OFF \
        -DWHISPER_BUILD_SERVER=OFF \
    && cmake --build /src/build -j"$(nproc)" --target whisper-cli

FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Download the model at build time so cold starts don't hit the network.
ARG WHISPER_MODEL=small
ENV WHISPER_MODEL=${WHISPER_MODEL}
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('${WHISPER_MODEL}', device='cpu', compute_type='int8')"

# whisper.cpp backend: CLI binary + ggml weights of the same size as
# WHISPER_MODEL, so switching TRANSCRIBE_BACKEND compares backends only.
# The model path is derived at runtime from WHISPER_MODEL (config.py), so a
# runtime model that doesn't match the baked weights fails at startup
# instead of silently using the wrong model.
COPY --from=whispercpp /src/build/bin/whisper-cli /usr/local/bin/whisper-cli
RUN mkdir -p /models \
    && curl -fL --retry 3 -o "/models/ggml-${WHISPER_MODEL}.bin" \
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-${WHISPER_MODEL}.bin"

COPY newrelic.ini entrypoint.sh ./
COPY app ./app
RUN chmod +x entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT:-8000}/v1/health" || exit 1

CMD ["./entrypoint.sh"]
