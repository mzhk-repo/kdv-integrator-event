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

if ! docker compose ps --status running --services | grep -Fxq "$SERVICE_NAME"; then
  echo "ERROR: сервіс '$SERVICE_NAME' не у статусі running"
  docker compose ps
  exit 1
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
