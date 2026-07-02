#!/bin/sh
set -e

PORT="${PORT:-8000}"
LOG_LEVEL="${LOG_LEVEL:-info}"

# Run wrapped in the New Relic agent only when enabled and a license key is
# present; otherwise start uvicorn directly (NEW_RELIC_ENABLED toggle).
if [ "${NEW_RELIC_ENABLED:-true}" = "true" ] && [ -n "${NEW_RELIC_LICENSE_KEY:-}" ]; then
    export NEW_RELIC_CONFIG_FILE="${NEW_RELIC_CONFIG_FILE:-/app/newrelic.ini}"
    exec newrelic-admin run-program \
        uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --log-level "$LOG_LEVEL"
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --log-level "$LOG_LEVEL"
