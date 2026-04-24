#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SERVICE_NAME="${KDV_HEALTH_SERVICE:-kdv-api}"
HEALTH_URL="${KDV_HEALTH_URL:-http://localhost:5000/kdv/api/health}"
ENV_TMP_FILE=""

cleanup() {
  if [[ -n "${ENV_TMP_FILE}" ]]; then
    rm -f "${ENV_TMP_FILE}"
  fi
}
trap cleanup EXIT

normalize_env_name() {
  local raw
  raw="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"

  case "${raw}" in
    dev|development)
      printf 'dev'
      ;;
    prod|production)
      printf 'prod'
      ;;
    *)
      printf ''
      ;;
  esac
}

decrypt_sops_env() {
  local enc_path sops_args=()
  enc_path="$1"

  if ! command -v sops >/dev/null 2>&1; then
    return 1
  fi

  ENV_TMP_FILE="$(mktemp /tmp/env.healthcheck.XXXXXX)"
  chmod 600 "${ENV_TMP_FILE}"

  sops_args=(--decrypt --input-type dotenv --output-type dotenv)
  if [[ -n "${SOPS_AGE_KEY_FILE:-}" && -f "${SOPS_AGE_KEY_FILE}" ]]; then
    sops_args+=(--age-key-file "${SOPS_AGE_KEY_FILE}")
  fi
  sops_args+=("${enc_path}")

  if sops "${sops_args[@]}" > "${ENV_TMP_FILE}"; then
    printf '%s' "${ENV_TMP_FILE}"
    return 0
  fi

  rm -f "${ENV_TMP_FILE}"
  ENV_TMP_FILE=""
  return 1
}

resolve_env_file() {
  local normalized_env plain_file enc_file decrypted_file

  if [[ -n "${ORCHESTRATOR_ENV_FILE:-}" && -f "${ORCHESTRATOR_ENV_FILE}" ]]; then
    printf '%s' "${ORCHESTRATOR_ENV_FILE}"
    return 0
  fi

  normalized_env="$(normalize_env_name "${SERVER_ENV:-${ENVIRONMENT_NAME:-}}")"
  if [[ -n "${normalized_env}" ]]; then
    plain_file="${ROOT_DIR}/env.${normalized_env}"
    if [[ -f "${plain_file}" ]]; then
      printf '%s' "${plain_file}"
      return 0
    fi

    enc_file="${ROOT_DIR}/env.${normalized_env}.enc"
    if [[ -f "${enc_file}" ]]; then
      if decrypted_file="$(decrypt_sops_env "${enc_file}")"; then
        printf '%s' "${decrypted_file}"
        return 0
      fi
    fi
  fi

  if [[ -f "${ROOT_DIR}/.env" ]]; then
    printf '%s' "${ROOT_DIR}/.env"
    return 0
  fi

  return 1
}

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker не знайдено у PATH"
  exit 1
fi

COMPOSE_ARGS=(docker compose)
if ENV_FILE="$(resolve_env_file)"; then
  COMPOSE_ARGS+=(--env-file "${ENV_FILE}")
fi

# First deploy safe:
# - if service container does not exist yet, skip pre-deploy check.
# - if service container exists but is not running, fail.
if "${COMPOSE_ARGS[@]}" ps --status running --services | grep -Fxq "$SERVICE_NAME"; then
  :
elif "${COMPOSE_ARGS[@]}" ps -a --services | grep -Fxq "$SERVICE_NAME"; then
  echo "ERROR: сервіс '$SERVICE_NAME' існує, але не у статусі running"
  "${COMPOSE_ARGS[@]}" ps
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
