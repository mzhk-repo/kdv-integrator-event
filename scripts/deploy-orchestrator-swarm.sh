#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

MODE="${ORCHESTRATOR_MODE:-noop}"
STACK_NAME="${STACK_NAME:-kdv_integrator_event}"
ENV_FILE="${ORCHESTRATOR_ENV_FILE:-/tmp/env.decrypted}"
SWARM_SERVICE_NAME="${SWARM_SERVICE_NAME:-${STACK_NAME}_kdv-api}"
SWARM_VERIFY_TIMEOUT="${SWARM_VERIFY_TIMEOUT:-180}"
SWARM_VERIFY_INTERVAL="${SWARM_VERIFY_INTERVAL:-5}"
ORCHESTRATOR_IMAGE_MODE="${ORCHESTRATOR_IMAGE_MODE:-local}"
LOCAL_IMAGE="${LOCAL_IMAGE:-kdv-integrator-event:local}"
RAW_MANIFEST=""
DEPLOY_MANIFEST=""
RUNTIME_ENV_FILE=""

log() {
  printf '[deploy-orchestrator] %s\n' "$*"
}

cleanup() {
  local manifest

  rm -f \
    "${RAW_MANIFEST:-}" \
    "${DEPLOY_MANIFEST:-}" \
    "${RUNTIME_ENV_FILE:-}"

  for manifest in \
    "${PROJECT_ROOT}/.${STACK_NAME}.stack.raw."*.yml \
    "${PROJECT_ROOT}/.${STACK_NAME}.stack.deploy."*.yml; do
    [[ -e "${manifest}" ]] || continue
    rm -f "${manifest}"
  done
}

trap cleanup EXIT

detect_compose_file() {
  if [[ -f "docker-compose.yaml" ]]; then
    echo "docker-compose.yaml"
  elif [[ -f "docker-compose.yml" ]]; then
    echo "docker-compose.yml"
  else
    echo ""
  fi
}

run_validation_scripts() {
  local healthcheck_script
  healthcheck_script="${SCRIPT_DIR}/healthcheck.sh"

  if [[ ! -f "${healthcheck_script}" ]]; then
    log "ERROR: validation script not found: ${healthcheck_script}"
    exit 1
  fi

  if [[ ! -x "${healthcheck_script}" ]]; then
    log "Running validation script via bash: ${healthcheck_script}"
    bash "${healthcheck_script}"
    return 0
  fi

  log "Running validation script: ${healthcheck_script}"
  "${healthcheck_script}"

  run_python_config_validation
}

run_python_config_validation() {
  local python_bin server_env_for_check

  if command -v python3 >/dev/null 2>&1; then
    python_bin="python3"
  elif command -v python >/dev/null 2>&1; then
    python_bin="python"
  else
    log "WARNING: python interpreter not found; skipping src.config validation"
    return 0
  fi

  server_env_for_check="${SERVER_ENV:-${ENVIRONMENT_NAME:-}}"
  log "Running Python config validation: import src.config (SERVER_ENV=${server_env_for_check:-unset})"
  ORCHESTRATOR_ENV_FILE="${ENV_FILE}" SERVER_ENV="${server_env_for_check}" \
    "${python_bin}" -c "import src.config"
}

run_deploy_adjacent_scripts() {
  log "No deploy-adjacent scripts configured for this repository; skipping Category 1b phase"
}

run_ansible_secrets_if_configured() {
  local infra_repo_path environment inventory_env inventory_path playbook_path

  infra_repo_path="${INFRA_REPO_PATH:-}"
  environment="${ENVIRONMENT_NAME:-}"

  if [[ -z "${infra_repo_path}" ]]; then
    log "INFRA_REPO_PATH is not set; skip ansible secrets refresh"
    return 0
  fi

  if [[ ! -d "${infra_repo_path}" ]]; then
    log "ERROR: INFRA_REPO_PATH does not exist: ${infra_repo_path}"
    exit 1
  fi

  if ! command -v ansible-playbook >/dev/null 2>&1; then
    log "ERROR: ansible-playbook not found on host"
    exit 1
  fi

  case "${environment}" in
    development|dev)
      inventory_env="dev"
      ;;
    production|prod)
      inventory_env="prod"
      ;;
    *)
      log "ERROR: unsupported ENVIRONMENT_NAME=${environment} (expected: development|production)"
      exit 1
      ;;
  esac

  inventory_path="${infra_repo_path}/ansible/inventories/${inventory_env}/hosts.yml"
  playbook_path="${infra_repo_path}/ansible/playbooks/swarm.yml"

  if [[ ! -f "${inventory_path}" ]]; then
    log "ERROR: inventory file not found: ${inventory_path}"
    exit 1
  fi
  if [[ ! -f "${playbook_path}" ]]; then
    log "ERROR: playbook file not found: ${playbook_path}"
    exit 1
  fi

  log "Refreshing Swarm secrets via Ansible (inventory=${inventory_env})"
  ANSIBLE_CONFIG="${infra_repo_path}/ansible/ansible.cfg" \
    ansible-playbook \
    -i "${inventory_path}" \
    "${playbook_path}" \
    --tags secrets
}

prepare_deploy_image() {
  case "${ORCHESTRATOR_IMAGE_MODE}" in
    local)
      log "Building local Docker image: ${LOCAL_IMAGE}"
      docker build -t "${LOCAL_IMAGE}" "${PROJECT_ROOT}"
      export KDV_IMAGE="${LOCAL_IMAGE}"
      log "Using local Docker image for Swarm deploy: ${KDV_IMAGE}"
      ;;
    registry)
      log "Using registry image from env/compose configuration"
      ;;
    *)
      log "ERROR: unsupported ORCHESTRATOR_IMAGE_MODE=${ORCHESTRATOR_IMAGE_MODE} (expected: local|registry)"
      exit 1
      ;;
  esac
}

verify_swarm_service() {
  local elapsed replicas running desired

  log "Verifying Swarm service ${SWARM_SERVICE_NAME} (timeout=${SWARM_VERIFY_TIMEOUT}s)"

  elapsed=0
  while (( elapsed <= SWARM_VERIFY_TIMEOUT )); do
    if ! docker service inspect "${SWARM_SERVICE_NAME}" >/dev/null 2>&1; then
      log "Waiting for service to appear: ${SWARM_SERVICE_NAME}"
    else
      replicas="$(docker service ls --filter "name=${SWARM_SERVICE_NAME}" --format '{{.Replicas}}' | head -n 1)"
      running="${replicas%%/*}"
      desired="${replicas##*/}"

      if [[ -n "${replicas}" && "${running}" == "${desired}" && "${desired}" != "0" ]]; then
        log "Swarm service is healthy: ${SWARM_SERVICE_NAME} ${replicas}"
        return 0
      fi

      log "Waiting for service replicas: ${SWARM_SERVICE_NAME} ${replicas:-unknown}"
    fi

    sleep "${SWARM_VERIFY_INTERVAL}"
    elapsed=$((elapsed + SWARM_VERIFY_INTERVAL))
  done

  log "ERROR: Swarm service did not reach desired replicas: ${SWARM_SERVICE_NAME}"
  docker service ls --filter "name=${SWARM_SERVICE_NAME}"
  docker service ps "${SWARM_SERVICE_NAME}" --no-trunc || true
  exit 1
}

deploy_swarm() {
  local compose_file swarm_file deploy_args

  compose_file="$(detect_compose_file)"
  swarm_file="docker-compose.swarm.yml"
  RAW_MANIFEST="$(mktemp "${PROJECT_ROOT}/.${STACK_NAME}.stack.raw.XXXXXX.yml")"
  DEPLOY_MANIFEST="$(mktemp "${PROJECT_ROOT}/.${STACK_NAME}.stack.deploy.XXXXXX.yml")"

  if [[ -z "${compose_file}" ]]; then
    log "ERROR: compose file not found (expected docker-compose.yaml|yml)"
    exit 1
  fi
  if [[ ! -f "${swarm_file}" ]]; then
    log "ERROR: ${swarm_file} not found"
    exit 1
  fi

  if [[ ! -f "${ENV_FILE}" ]]; then
    if [[ -f ".env" ]]; then
      ENV_FILE=".env"
      log "WARNING: env.*.enc не знайдено або ORCHESTRATOR_ENV_FILE не передано. Fallback на локальний .env — тільки для dev-середовища."
    else
      log "ERROR: env file not found (${ORCHESTRATOR_ENV_FILE:-/tmp/env.decrypted}) and .env missing"
      exit 1
    fi
  fi

  run_ansible_secrets_if_configured
  run_validation_scripts
  run_deploy_adjacent_scripts
  prepare_deploy_image

  log "Rendering Swarm manifest (stack=${STACK_NAME}, env_file=${ENV_FILE})"
  docker compose --env-file "${ENV_FILE}" \
    -f "${compose_file}" \
    -f "${swarm_file}" \
    config > "${RAW_MANIFEST}"

  awk 'NR==1 && $1=="name:" {next} {print}' "${RAW_MANIFEST}" \
    | sed -E 's/^([[:space:]]*published:[[:space:]]*)"([0-9]+)"$/\1\2/' \
    > "${DEPLOY_MANIFEST}"

  log "Deploying stack ${STACK_NAME}"
  deploy_args=(docker stack deploy -c "${DEPLOY_MANIFEST}")
  if [[ "${ORCHESTRATOR_IMAGE_MODE}" == "local" ]]; then
    deploy_args+=(--resolve-image never)
  fi
  deploy_args+=("${STACK_NAME}")
  "${deploy_args[@]}"

  verify_swarm_service

  log "Swarm deploy completed"
}

cd "${PROJECT_ROOT}"

case "${MODE}" in
  noop)
    log "No-op mode. Set ORCHESTRATOR_MODE=swarm to enable Phase 8 Swarm deploy path."
    ;;
  swarm)
    deploy_swarm
    ;;
  *)
    log "ERROR: unknown ORCHESTRATOR_MODE=${MODE}. Supported: noop, swarm"
    exit 1
    ;;
esac
