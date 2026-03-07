#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="/root/inchenmml"
LOG_DIR="${BASE_DIR}/logs"

BACKEND_PID_FILE="${LOG_DIR}/backend-prod.pid"
FRONTEND_PID_FILE="${LOG_DIR}/frontend-prod.pid"

if [ -f "${BACKEND_PID_FILE}" ]; then
  PID="$(cat "${BACKEND_PID_FILE}")"
  if kill -0 "${PID}" 2>/dev/null; then
    echo "[stop] backend pid=${PID}"
    kill "${PID}" || true
    sleep 1
  fi
  rm -f "${BACKEND_PID_FILE}"
fi

if [ -f "${FRONTEND_PID_FILE}" ]; then
  PID="$(cat "${FRONTEND_PID_FILE}")"
  if kill -0 "${PID}" 2>/dev/null; then
    echo "[stop] frontend pid=${PID}"
    kill "${PID}" || true
    sleep 1
  fi
  rm -f "${FRONTEND_PID_FILE}"
fi

pkill -f "uvicorn app.main:app --host" || true
pkill -f "next-server" || true
pkill -f "node .*next" || true
pkill -f "npm run start" || true

echo "[stop] done"
