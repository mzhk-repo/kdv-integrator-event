#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SERVICE_NAME="${KDV_HEALTH_SERVICE:-kdv-api}"
HEALTH_URL="${KDV_HEALTH_URL:-http://localhost:5000/kdv/api/health}"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker не знайдено у PATH"
  exit 1
fi

# First deploy safe:
# - if service container does not exist yet, skip pre-deploy check.
# - if service container exists but is not running, fail.
if docker compose ps --status running --services | grep -Fxq "$SERVICE_NAME"; then
  :
elif docker compose ps -a --services | grep -Fxq "$SERVICE_NAME"; then
  echo "ERROR: сервіс '$SERVICE_NAME' існує, але не у статусі running"
  docker compose ps
  exit 1
else
  echo "INFO: first deploy detected, сервіс '$SERVICE_NAME' ще не створений."
  echo "INFO: pre-deploy healthcheck пропущено."
  exit 0
fi

if ! RESPONSE="$(docker exec "$SERVICE_NAME" curl -fsS "$HEALTH_URL")"; then
  echo "ERROR: health endpoint недоступний: $HEALTH_URL"
  exit 1
fi

if [[ "$RESPONSE" != *'"status":"ok"'* ]]; then
  echo "ERROR: некоректна health-відповідь: $RESPONSE"
  exit 1
fi

echo "OK: healthcheck пройдено ($SERVICE_NAME -> $HEALTH_URL)"
echo "$RESPONSE"
