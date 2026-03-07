#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="/root/inchenmml"
FRONTEND_DIR="${BASE_DIR}/frontend"
LOG_DIR="${BASE_DIR}/logs"
PID_FILE="${LOG_DIR}/frontend-prod.pid"
LOG_FILE="${LOG_DIR}/frontend-prod.log"

mkdir -p "${LOG_DIR}"

FRONTEND_HOST="${FRONTEND_HOST:-0.0.0.0}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
REBUILD="${FRONTEND_REBUILD:-0}"
FRONTEND_DAEMON="${FRONTEND_DAEMON:-1}"

cd "${FRONTEND_DIR}"

if [ "${REBUILD}" = "1" ] || [ ! -d ".next" ]; then
  echo "[frontend] rebuild production bundle"
  rm -rf .next
  npm run build
fi

required_files=(
  ".next/server/middleware-manifest.json"
)
optional_files=(
  ".next/required-server-files.json"
)
for required_file in "${required_files[@]}"; do
  if [ ! -f "${required_file}" ]; then
    if [ -f ".next/server/middleware/middleware-manifest.json" ]; then
      mkdir -p ".next/server"
      cp ".next/server/middleware/middleware-manifest.json" "${required_file}"
      echo "[frontend] copied fallback middleware manifest from .next/server/middleware/middleware-manifest.json"
    else
      echo "[frontend] missing required build artifact: ${required_file}"
      exit 1
    fi
  fi
done
for optional_file in "${optional_files[@]}"; do
  if [ ! -f "${optional_file}" ]; then
    echo "[frontend] warning: optional build artifact missing: ${optional_file}"
  fi
done

# Ensure next-server compatibility with Turbopack builds that may omit BUILD_ID in export flow.
if [ ! -f ".next/BUILD_ID" ]; then
  if [ -f ".next/server/middleware-manifest.json" ]; then
    BUILD_ID="$(node -e 'const fs=require("fs"); const m=JSON.parse(fs.readFileSync(".next/server/middleware-manifest.json","utf8")); const id=m.__NEXT_BUILD_ID||m.middleware?.__NEXT_BUILD_ID; if(!id){process.exit(1)}; process.stdout.write(String(id));')"
    if [ -n "${BUILD_ID}" ]; then
      echo "${BUILD_ID}" > ".next/BUILD_ID"
      echo "[frontend] generated missing .next/BUILD_ID from middleware manifest"
    else
      echo "[frontend] missing .next/BUILD_ID and failed to read middleware manifest"
      exit 1
    fi
  else
    echo "[frontend] missing .next/BUILD_ID and middleware manifest for fallback"
    exit 1
  fi
fi

echo "[frontend] build artifact check passed"

for _ in 1 2 3 4 5 6 7 8 9 10; do
  if ! ss -ltn | grep -qE ":${FRONTEND_PORT}\\b"; then
    break
  fi
  sleep 1
done

echo "[frontend] stop existing process"
if [ -f "${PID_FILE}" ]; then
  OLD_PID="$(cat "${PID_FILE}")"
  if kill -0 "${OLD_PID}" 2>/dev/null; then
    kill "${OLD_PID}" || true
    for _ in 1 2 3 4 5; do
      if kill -0 "${OLD_PID}" 2>/dev/null; then
        sleep 1
      else
        break
      fi
    done
  fi
  rm -f "${PID_FILE}"
fi
pkill -f "next-server" || true
pkill -f "node .*next" || true
pkill -f "npm run start" || true
pkill -f "node_modules/.bin/next start" || true

echo "[frontend] start next start production mode"
if [ "${FRONTEND_DAEMON}" = "1" ]; then
  echo "[frontend] start next start production mode (daemon)"
  nohup "${FRONTEND_DIR}/node_modules/.bin/next" start --hostname "${FRONTEND_HOST}" --port "${FRONTEND_PORT}" \
    > "${LOG_FILE}" 2>&1 \
    &
  LAUNCH_PID=$!
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    LIVE_PID="$(pgrep -f "next-server" | head -n 1 || true)"
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
  echo "[frontend] started pid=${RECORD_PID}"
  echo "[frontend] host=${FRONTEND_HOST}, port=${FRONTEND_PORT}"
else
  echo "[frontend] start next start production mode (foreground)"
  exec "${FRONTEND_DIR}/node_modules/.bin/next" start --hostname "${FRONTEND_HOST}" --port "${FRONTEND_PORT}"
fi
