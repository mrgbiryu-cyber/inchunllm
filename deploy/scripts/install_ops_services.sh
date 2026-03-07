#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "[install] deploy scripts directory: ${SCRIPT_DIR}"
echo "[install] project root: ${PROJECT_ROOT}"

sudo cp "${PROJECT_ROOT}/systemd/aibizplan-backend.service" /etc/systemd/system/
sudo cp "${PROJECT_ROOT}/systemd/aibizplan-frontend.service" /etc/systemd/system/

if [ -f "${PROJECT_ROOT}/logrotate/aibizplan-apps" ]; then
  sudo cp "${PROJECT_ROOT}/logrotate/aibizplan-apps" /etc/logrotate.d/aibizplan-apps
  echo "[install] installed logrotate profile"
fi

sudo systemctl daemon-reload
sudo systemctl enable aibizplan-backend.service
sudo systemctl enable aibizplan-frontend.service
sudo systemctl restart aibizplan-backend.service
sudo systemctl restart aibizplan-frontend.service

echo "[install] status"
sudo systemctl status --no-pager aibizplan-backend.service aibizplan-frontend.service
