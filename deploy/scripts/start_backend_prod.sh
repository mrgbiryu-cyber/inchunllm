#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="/root/inchenmml"
BACKEND_DIR="${BASE_DIR}/backend"
VENV_DIR="${BACKEND_DIR}/.venv"
LOG_DIR="${BASE_DIR}/logs"
PID_FILE="${LOG_DIR}/backend-prod.pid"
LOG_FILE="${LOG_DIR}/backend-prod.log"

mkdir -p "${LOG_DIR}"

# Source backend environment
if [ -f "${BACKEND_DIR}/.env" ]; then
  set -a
  # shellcheck source=/dev/null
  source "${BACKEND_DIR}/.env"
  set +a
fi

APP_HOST="${APP_HOST:-${HOST:-0.0.0.0}}"
APP_PORT="${APP_PORT:-${PORT:-8000}}"
WORKERS="${UVICORN_WORKERS:-1}"
RELOAD="${UVICORN_RELOAD:-false}"
LOG_LEVEL="${LOG_LEVEL:-info}"
LOG_LEVEL_LOWER="$(printf '%s' "${LOG_LEVEL}" | tr '[:upper:]' '[:lower:]')"

if [ "${RELOAD}" = "true" ] || [ "${RELOAD}" = "1" ]; then
  RELOAD_FLAG="--reload"
else
  RELOAD_FLAG=""
fi

echo "[backend] stop existing process"
if [ -f "${PID_FILE}" ]; then
  OLD_PID="$(cat "${PID_FILE}")"
  if kill -0 "${OLD_PID}" 2>/dev/null; then
    kill "${OLD_PID}" || true
    sleep 1
  fi
  rm -f "${PID_FILE}"
fi
pkill -f "uvicorn app.main:app" || true

echo "[backend] start backend production mode"
cd "${BACKEND_DIR}"

if [ ! -d "${VENV_DIR}" ]; then
  echo "[backend] ERROR: virtualenv not found: ${VENV_DIR}" >&2
  exit 1
fi

nohup "${VENV_DIR}/bin/uvicorn" \
  app.main:app \
  --host "${APP_HOST}" \
  --port "${APP_PORT}" \
  --workers "${WORKERS}" \
  --no-access-log \
  --log-level "${LOG_LEVEL_LOWER}" \
  ${RELOAD_FLAG} \
  > "${LOG_FILE}" 2>&1 \
  &
LAUNCH_PID=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do
  LIVE_PID="$(pgrep -f "uvicorn app.main:app --host ${APP_HOST} --port ${APP_PORT}" | head -n 1 || true)"
  if [ -n "${LIVE_PID}" ]; then
    echo "${LIVE_PID}" > "${PID_FILE}"
    break
  fi
  sleep 1
done

if [ ! -s "${PID_FILE}" ]; then
  echo "${LAUNCH_PID}" > "${PID_FILE}"
fi

RECORD_PID="$(cat "${PID_FILE}")"
echo "[backend] started pid=${RECORD_PID}"
echo "[backend] host=${APP_HOST}, port=${APP_PORT}, workers=${WORKERS}"
