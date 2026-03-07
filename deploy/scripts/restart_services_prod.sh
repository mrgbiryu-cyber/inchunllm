#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_URL="http://127.0.0.1:8000/health"
FRONTEND_URL="http://127.0.0.1:3000"

wait_http_status() {
  local name="$1"
  local url="$2"
  local expected="$3"
  local attempts=30

  echo "[wait] ${name}"
  for attempt in $(seq 1 "${attempts}"); do
    if status="$(curl -sS -o /tmp/restart_check_body.txt -w "%{http_code}" "${url}" 2>/dev/null)"; then
      if [[ "${status}" == "${expected}" ]]; then
        echo "[wait] ${name} ok status=${status} attempt=${attempt}"
        return 0
      fi
    fi
    sleep 1
  done

  echo "[wait] ${name} failed (expected ${expected})" >&2
  return 1
}

wait_frontend_ok() {
  local name="$1"
  local url="$2"
  local attempts=40

  echo "[wait] ${name}"
  for attempt in $(seq 1 "${attempts}"); do
    if status="$(curl -sS -o /tmp/restart_check_body.txt -w "%{http_code}" "${url}" 2>/dev/null)"; then
      if [[ "${status}" == "200" || "${status}" == "307" || "${status}" == "308" ]]; then
        echo "[wait] ${name} ok status=${status} attempt=${attempt}"
        return 0
      fi
    fi
    sleep 1
  done

  echo "[wait] ${name} failed (expected 200/307/308)" >&2
  return 1
}

${SCRIPT_DIR}/stop_services_prod.sh
sleep 1
${SCRIPT_DIR}/start_backend_prod.sh
sleep 1
${SCRIPT_DIR}/start_frontend_prod.sh

sleep 2

echo "[status] process list"
ps -ef | rg -n "uvicorn app.main:app|next-server \\(v16|npm run start -- --hostname|node_modules/.bin/next" || true

echo "[status] listening ports"
ss -ltnp | rg ":3000|:8000" || true

echo "[status] check endpoints"
wait_http_status "backend" "${BACKEND_URL}" "200"
wait_frontend_ok "frontend" "${FRONTEND_URL}"
