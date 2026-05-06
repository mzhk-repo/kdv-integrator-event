#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${ORCHESTRATOR_ENV_FILE:-/tmp/env.decrypted}"
RUNTIME_ENV_SECRET_BASE="${RUNTIME_ENV_SECRET_BASE:-}"

log() {
  printf '[versioned-env-secret] %s\n' "$*" >&2
}

read_env_value() {
  local key line value
  key="$1"

  [[ -f "${ENV_FILE}" ]] || return 0

  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -n "${line}" && "${line}" != \#* && "${line}" == *=* ]] || continue
    [[ "${line%%=*}" == "${key}" ]] || continue

    value="${line#*=}"
    value="${value%"${value##*[![:space:]]}"}"
    if [[ "${value}" == \"*\" && "${value}" == *\" ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "${value}" == \'*\' && "${value}" == *\' ]]; then
      value="${value:1:${#value}-2}"
    fi
    printf '%s' "${value}"
    return 0
  done < "${ENV_FILE}"
}

if [[ ! -f "${ENV_FILE}" ]]; then
  log "ERROR: env file not found: ${ENV_FILE}"
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  log "ERROR: docker not found"
  exit 1
fi

if ! command -v sha256sum >/dev/null 2>&1; then
  log "ERROR: sha256sum not found"
  exit 1
fi

RUNTIME_ENV_SECRET_BASE="${RUNTIME_ENV_SECRET_BASE:-$(read_env_value RUNTIME_ENV_SECRET_BASE)}"
RUNTIME_ENV_SECRET_BASE="${RUNTIME_ENV_SECRET_BASE:-$(read_env_value KDV_APP_ENV_PAYLOAD_SECRET_NAME)}"
RUNTIME_ENV_SECRET_BASE="${RUNTIME_ENV_SECRET_BASE:-app_env_payload}"

secret_hash="$(sha256sum "${ENV_FILE}" | awk '{print substr($1, 1, 12)}')"
secret_name="${RUNTIME_ENV_SECRET_BASE}_${secret_hash}"

if docker secret inspect "${secret_name}" >/dev/null 2>&1; then
  log "Runtime env secret already exists: ${secret_name}"
else
  log "Creating runtime env secret: ${secret_name}"
  docker secret create "${secret_name}" "${ENV_FILE}" >/dev/null
fi

log "Using runtime env secret: ${secret_name}"
printf 'export KDV_APP_ENV_PAYLOAD_SECRET_NAME=%q\n' "${secret_name}"
