#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

ENV_TMP_FILE=""
ENV_FILE=""

log() {
  printf '[run-robot-swarm] %s\n' "$*" >&2
}

die() {
  log "ERROR: $*"
  exit 1
}

cleanup() {
  if [[ -n "${ENV_TMP_FILE}" && -f "${ENV_TMP_FILE}" ]]; then
    command -v shred >/dev/null 2>&1 && shred -u "${ENV_TMP_FILE}" 2>/dev/null || rm -f "${ENV_TMP_FILE}"
  fi
}
trap cleanup EXIT

usage() {
  cat <<'USAGE'
Usage:
  scripts/run-robot-swarm.sh [candidates_file] [robot.py args...]

Examples:
  scripts/run-robot-swarm.sh candidates.txt
  scripts/run-robot-swarm.sh candidates.txt --skip-optimization
  scripts/run-robot-swarm.sh candidates.txt --parallelism 2 --max-wait 1200
  scripts/run-robot-swarm.sh --dry-run candidates.txt

Wrapper options:
  --dry-run   Resolve env/container, copy and parse candidates, but do not start batch.
  --help      Show this help.

Environment:
  ORCHESTRATOR_ENV_FILE  Plain dotenv file prepared by orchestrator.
  SERVER_ENV             dev|prod fallback selector for env.dev.enc/env.prod.enc.
  ENVIRONMENT_NAME       development|production fallback selector.
  STACK_NAME             Swarm stack name; default kdv_integrator_event.
  SWARM_SERVICE_NAME     API service name; default ${STACK_NAME}_kdv-api.
USAGE
}

normalize_env_name() {
  local raw
  raw="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
  case "${raw}" in
    dev|development) printf 'dev' ;;
    prod|production) printf 'prod' ;;
    *) printf '' ;;
  esac
}

decrypt_sops_env() {
  local enc_path="$1"
  local age_key_file="${SOPS_AGE_KEY_FILE:-${HOME}/.config/age/keys.txt}"
  local sops_args=()

  command -v sops >/dev/null 2>&1 || return 1

  ENV_TMP_FILE="$(mktemp /dev/shm/kdv-robot-env.XXXXXX)"
  chmod 600 "${ENV_TMP_FILE}"

  sops_args=(--decrypt --input-type dotenv --output-type dotenv)
  if [[ -f "${age_key_file}" ]]; then
    sops_args+=(--age-key-file "${age_key_file}")
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
    plain_file="${PROJECT_ROOT}/env.${normalized_env}"
    if [[ -f "${plain_file}" ]]; then
      printf '%s' "${plain_file}"
      return 0
    fi

    enc_file="${PROJECT_ROOT}/env.${normalized_env}.enc"
    if [[ -f "${enc_file}" ]]; then
      if decrypted_file="$(decrypt_sops_env "${enc_file}")"; then
        printf '%s' "${decrypted_file}"
        return 0
      fi
    fi
  fi

  if [[ -f "${PROJECT_ROOT}/.env" ]]; then
    printf '%s' "${PROJECT_ROOT}/.env"
    return 0
  fi

  return 1
}

read_env_value() {
  local key="$1" file="${2:-}" line value
  [[ -n "${file}" && -f "${file}" ]] || return 0

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
  done < "${file}"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "$1 не знайдено у PATH"
}

DRY_RUN="false"
CANDIDATES_FILE="candidates.txt"
ROBOT_ARGS=()
ROBOT_EXIT_CODE=0
HOST_LOG_PATH="${ROBOT_HOST_LOG_PATH:-${PROJECT_ROOT}/logs/robot_batch.log}"
CONTAINER_LOG_PATH="${ROBOT_CONTAINER_LOG_PATH:-/app/logs/robot_batch.log}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      usage
      exit 0
      ;;
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    --)
      shift
      ROBOT_ARGS+=("$@")
      break
      ;;
    *)
      CANDIDATES_FILE="$1"
      shift
      ROBOT_ARGS+=("$@")
      break
      ;;
  esac
done

require_command docker

[[ -f "${CANDIDATES_FILE}" ]] || die "candidates file not found on host: ${CANDIDATES_FILE}"

if ENV_FILE="$(resolve_env_file)"; then
  log "Env context: ${ENV_FILE}"
else
  log "WARNING: env context not found; using defaults and current shell environment"
  ENV_FILE=""
fi

STACK_NAME="${STACK_NAME:-$(read_env_value STACK_NAME "${ENV_FILE}")}"
STACK_NAME="${STACK_NAME:-kdv_integrator_event}"
SWARM_SERVICE_NAME="${SWARM_SERVICE_NAME:-$(read_env_value SWARM_SERVICE_NAME "${ENV_FILE}")}"
SWARM_SERVICE_NAME="${SWARM_SERVICE_NAME:-${STACK_NAME}_kdv-api}"
CONTAINER_CANDIDATES_PATH="${ROBOT_CONTAINER_CANDIDATES_PATH:-/tmp/kdv-candidates.txt}"
HEALTH_URL="${KDV_ROBOT_HEALTH_URL:-http://localhost:5000/kdv/api/health}"
EXEC_ENV_FILE_ARGS=()
if [[ -n "${ENV_FILE}" ]]; then
  EXEC_ENV_FILE_ARGS=(--env-file "${ENV_FILE}")
fi

[[ "${CONTAINER_CANDIDATES_PATH}" == /* ]] || die "ROBOT_CONTAINER_CANDIDATES_PATH must be absolute"

if ! docker service inspect "${SWARM_SERVICE_NAME}" >/dev/null 2>&1; then
  docker service ls --filter "name=${STACK_NAME}" >&2 || true
  die "Swarm service not found: ${SWARM_SERVICE_NAME}"
fi

replicas="$(docker service ls --filter "name=${SWARM_SERVICE_NAME}" --format '{{.Replicas}}' | head -n 1)"
log "Swarm service: ${SWARM_SERVICE_NAME} replicas=${replicas:-unknown}"

KDV_API_CID="$(docker ps -q --filter "label=com.docker.swarm.service.name=${SWARM_SERVICE_NAME}" | head -n 1)"
if [[ -z "${KDV_API_CID}" ]]; then
  docker service ps "${SWARM_SERVICE_NAME}" --no-trunc >&2 || true
  die "container for ${SWARM_SERVICE_NAME} not found on this node"
fi
log "Container: ${KDV_API_CID}"

log "Checking health: ${HEALTH_URL}"
docker exec "${KDV_API_CID}" curl -fsS "${HEALTH_URL}" >/dev/null

log "Copying candidates: ${CANDIDATES_FILE} -> ${CONTAINER_CANDIDATES_PATH}"
docker exec -i "${KDV_API_CID}" sh -c 'cat > "$1"' sh "${CONTAINER_CANDIDATES_PATH}" < "${CANDIDATES_FILE}"

log "Validating candidates parse"
docker exec "${KDV_API_CID}" python3 -c 'import sys; from scripts.robot import parse_candidates; ids=parse_candidates(sys.argv[1]); print(f"candidates={len(ids)} list={ids[:20]}")' "${CONTAINER_CANDIDATES_PATH}"

log "Validating robot auth env"
docker exec "${EXEC_ENV_FILE_ARGS[@]}" "${KDV_API_CID}" python3 -c 'import os, sys; sys.exit(0 if os.getenv("KDV_API_TOKEN") else 1)'   || die "KDV_API_TOKEN is missing for docker exec process; check env context"

if [[ "${DRY_RUN}" == "true" ]]; then
  log "Dry-run completed; robot.py batch was not started"
  exit 0
fi

EXEC_ENV_ARGS=()
for env_name in ROBOT_BATCH_DELAY ROBOT_POLL_INTERVAL ROBOT_MAX_WAIT ROBOT_PARALLELISM; do
  if [[ -n "${!env_name:-}" ]]; then
    EXEC_ENV_ARGS+=(-e "${env_name}=${!env_name}")
  fi
done

log "Starting robot.py"
set +e
docker exec "${EXEC_ENV_FILE_ARGS[@]}" "${EXEC_ENV_ARGS[@]}" "${KDV_API_CID}" python3 scripts/robot.py "${CONTAINER_CANDIDATES_PATH}" "${ROBOT_ARGS[@]}"
ROBOT_EXIT_CODE=$?
set -e

log "Syncing robot log: ${CONTAINER_LOG_PATH} -> ${HOST_LOG_PATH}"
mkdir -p "$(dirname "${HOST_LOG_PATH}")"
if ! docker exec "${KDV_API_CID}" sh -c 'test -f "$1" && cat "$1"' sh "${CONTAINER_LOG_PATH}" > "${HOST_LOG_PATH}"; then
  log "WARNING: failed to sync robot log from container"
fi

exit "${ROBOT_EXIT_CODE}"
