#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-${MODE:-prod}}"

echo "=== BUJA Fullstack Check ==="

echo "[01] service processes"
if [[ "$MODE" == "dev" ]]; then
  pgrep -af "uvicorn app.main:app" || true
  pgrep -af "next dev" || true
else
  pgrep -af "uvicorn app.main:app" || true
  pgrep -af "next-server" || true
fi

echo "[02] docker infra"
if ! command -v docker >/dev/null 2>&1; then
  echo "SKIP: docker command not installed in this environment"
elif [ -d "$BASE_DIR/docker" ]; then
  (cd "$BASE_DIR/docker" && docker compose ps)
else
  echo "SKIP: docker directory missing"
fi

echo "[03] backend health"
for url in \
  "http://127.0.0.1:8000/" \
  "http://127.0.0.1:8000/api/v1/health"; do
  if curl -fsS "$url" >/tmp/buja-health.out; then
    echo "OK: $url"
  else
    echo "FAIL: $url"
    exit 1
  fi
done

echo "[04] frontend"
if curl -fsS "http://127.0.0.1:3000/" >/dev/null; then
  echo "OK: http://127.0.0.1:3000/"
else
  echo "FAIL: http://127.0.0.1:3000/"
  exit 1
fi

echo "=== check passed ==="
