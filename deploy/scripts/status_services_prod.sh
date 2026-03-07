#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="/root/inchenmml"
LOG_DIR="${BASE_DIR}/logs"
BACKEND_PID_FILE="${LOG_DIR}/backend-prod.pid"
FRONTEND_PID_FILE="${LOG_DIR}/frontend-prod.pid"

echo "[backend]"
if [ -f "${BACKEND_PID_FILE}" ] && kill -0 "$(cat "${BACKEND_PID_FILE}")" 2>/dev/null; then
  echo "  status=RUNNING pid=$(cat "${BACKEND_PID_FILE}")"
else
  LIVE_BACKEND_PID="$(pgrep -f "uvicorn app.main:app --host" | head -n 1 || true)"
  if [ -n "${LIVE_BACKEND_PID}" ]; then
    echo "  status=RUNNING pid=${LIVE_BACKEND_PID} (fallback)"
  else
    echo "  status=STOPPED"
  fi
fi

echo "[frontend]"
if [ -f "${FRONTEND_PID_FILE}" ] && kill -0 "$(cat "${FRONTEND_PID_FILE}")" 2>/dev/null; then
  echo "  status=RUNNING pid=$(cat "${FRONTEND_PID_FILE}")"
else
  LIVE_FRONTEND_PID="$(pgrep -f "next-server" | head -n 1 || true)"
  if [ -n "${LIVE_FRONTEND_PID}" ]; then
    echo "  status=RUNNING pid=${LIVE_FRONTEND_PID} (fallback)"
  else
    echo "  status=STOPPED"
  fi
fi

echo "[ports]"
ss -ltnp | rg ":3000|:8000|:5432|:6379|:7687" || true

echo "[health]"
check_health() {
  local name="$1"
  local url="$2"
  local attempts=10
  for attempt in $(seq 1 "${attempts}"); do
    if result="$(curl -sS -o "/tmp/status_health_${name}.txt" -w "%{http_code}" "${url}" 2>/dev/null)"; then
      echo "${name}:${result}"
      return 0
    fi
    sleep 1
  done
  echo "${name}:000"
}

check_health "backend" "http://127.0.0.1:8000/health"
check_health "frontend" "http://127.0.0.1:3000"
