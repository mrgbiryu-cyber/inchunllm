#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

run_prod_stop() {
  exec "$BASE_DIR/deploy/scripts/stop_services_prod.sh"
}

run_dev_stop() {
  RUN_DIR="$BASE_DIR/.run"
  BACKEND_PID="$RUN_DIR/backend.pid"
  FRONTEND_PID="$RUN_DIR/frontend.pid"

  kill_pid() {
    local pid_file="$1"
    local label="$2"
    if [ -f "$pid_file" ]; then
      local pid
      pid="$(cat "$pid_file")"
      if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
        echo "Stopping ${label} (pid ${pid})"
        kill "${pid}" || true
      fi
      rm -f "$pid_file"
    fi
  }

  kill_pid "$BACKEND_PID" "backend"
  kill_pid "$FRONTEND_PID" "frontend"

  pkill -f "uvicorn app.main:app" || true
  pkill -f "next dev" || true

  if [ -d "$BASE_DIR/docker" ]; then
    if command -v docker >/dev/null 2>&1; then
      (cd "$BASE_DIR/docker" && docker compose stop redis neo4j postgres || true)
    else
      echo "Skip docker stop (docker not installed)"
    fi
  fi
}

mode="${1:-${MODE:-prod}}"
if [[ "${mode}" == "dev" ]]; then
  run_dev_stop
else
  run_prod_stop
fi
