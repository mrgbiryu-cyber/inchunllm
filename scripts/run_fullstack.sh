#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

run_log() {
  echo "[$(date '+%F %T')] $*"
}

run_prod_stack() {
  run_log "운영 모드 run: deploy 재기동 스크립트 사용"
  exec "$BASE_DIR/deploy/scripts/restart_services_prod.sh"
}

run_dev_stack() {
  local run_dir="$BASE_DIR/.run"
  local backend_dir="$BASE_DIR/backend"
  local frontend_dir="$BASE_DIR/frontend"
  local docker_dir="$BASE_DIR/docker"
  local backend_log="$run_dir/backend.log"
  local frontend_log="$run_dir/frontend.log"
  local backend_pid="$run_dir/backend.pid"
  local frontend_pid="$run_dir/frontend.pid"

  mkdir -p "$run_dir"

  ensure_env() {
    if [ -f "$BASE_DIR/.env" ]; then
      set -a
      # shellcheck source=/dev/null
      . "$BASE_DIR/.env"
      set +a
    fi
    if [ -f "$backend_dir/.env" ]; then
      set -a
      # shellcheck source=/dev/null
      . "$backend_dir/.env"
      set +a
    fi
  }

  backend_port() {
    local port="${PORT:-8000}"
    if [ -z "${port}" ]; then
      port=8000
    fi
    echo "${port}"
  }

  wait_url() {
    local url="$1"
    local retries=40
    local delay=1
    local i=0

    until curl -fsS "$url" >/tmp/buja-api-health.tmp 2>/tmp/buja-api-health.err; do
      i=$((i+1))
      if [ "$i" -ge "$retries" ]; then
        run_log "TIMEOUT: $url"
        cat /tmp/buja-api-health.err || true
        return 1
      fi
      sleep "$delay"
    done
    return 0
  }

  start_infra() {
    run_log "인프라 시작 (redis/neo4j)"
    if ! command -v docker >/dev/null 2>&1; then
      run_log "SKIP: docker 미설치"
      return 0
    fi
    (cd "$docker_dir" && docker compose up -d redis neo4j)
    (cd "$docker_dir" && docker compose up -d postgres >/dev/null 2>&1 || true)
  }

  start_backend() {
    local port
    port="$(backend_port)"
    run_log "백엔드 시작: http://0.0.0.0:${port}"
    if [ ! -f "$backend_dir/.venv/bin/activate" ]; then
      run_log "백엔드 가상환경 없음: $backend_dir/.venv/bin/activate"
      return 1
    fi

    rm -f "$backend_pid"
    (
      cd "$backend_dir"
      source .venv/bin/activate
      STARTUP_WITHOUT_REDIS="${STARTUP_WITHOUT_REDIS:-true}" \
      nohup env uvicorn app.main:app --host 0.0.0.0 --port "${port}" \
        >"$backend_log" 2>&1 &
      echo $! >"$backend_pid"
    )
  }

  start_frontend() {
    run_log "프론트엔드 시작 (dev): http://localhost:3000"
    if [ ! -d "$frontend_dir/node_modules" ]; then
      run_log "frontend/node_modules 없음. npm install 필요"
      return 1
    fi

    rm -f "$frontend_pid"
    (
      cd "$frontend_dir"
      nohup npm run dev -- --hostname 0.0.0.0 --port 3000 \
        >"$frontend_log" 2>&1 &
      echo $! >"$frontend_pid"
    )
  }

  ensure_env
  start_infra
  run_log "백엔드 및 프론트엔드 시작"
  start_backend
  start_frontend

  run_log "백엔드 헬스체크"
  if wait_url "http://127.0.0.1:$(backend_port)/api/v1/health"; then
    run_log "백엔드 정상: http://127.0.0.1:$(backend_port)"
  else
    run_log "백엔드 준비 실패. 로그: $backend_log"
    exit 1
  fi

  run_log "프론트엔드 헬스체크"
  if wait_url "http://127.0.0.1:3000/"; then
    run_log "프론트엔드 정상: http://127.0.0.1:3000"
  else
    run_log "프론트엔드 준비 실패. 로그: $frontend_log"
    exit 1
  fi

  run_log "스택 실행 중"
  run_log "Backend log: $backend_log"
  run_log "Frontend log: $frontend_log"
  run_log "PID: $backend_pid, $frontend_pid"
  run_log "중지: bash scripts/stop_fullstack.sh"
}

mode="${1:-${MODE:-prod}}"
if [[ "${mode}" == "dev" ]]; then
  run_dev_stack
else
  run_prod_stack
fi
