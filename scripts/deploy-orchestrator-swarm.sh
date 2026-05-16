#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

MODE="${ORCHESTRATOR_MODE:-noop}"
STACK_NAME="${STACK_NAME:-kdv_integrator_event}"
ENV_FILE="${ORCHESTRATOR_ENV_FILE:-/tmp/env.decrypted}"
SWARM_SERVICE_NAME="${SWARM_SERVICE_NAME:-${STACK_NAME}_kdv-api}"
SWARM_VERIFY_TIMEOUT="${SWARM_VERIFY_TIMEOUT:-}"
SWARM_VERIFY_INTERVAL="${SWARM_VERIFY_INTERVAL:-}"
ORCHESTRATOR_IMAGE_MODE="${ORCHESTRATOR_IMAGE_MODE:-}"
LOCAL_IMAGE="${LOCAL_IMAGE:-}"
LOCAL_IMAGE_TAG="${LOCAL_IMAGE_TAG:-}"
RUNTIME_ENV_SECRET_BASE="${RUNTIME_ENV_SECRET_BASE:-}"
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

default_image_tag() {
  local git_sha

  git_sha="$(git -C "${PROJECT_ROOT}" rev-parse --short=12 HEAD 2>/dev/null || true)"
  if [[ -z "${git_sha}" ]]; then
    git_sha="$(date -u +%Y%m%d%H%M%S)"
  fi

  printf '%s' "${git_sha}"
}

default_local_image() {
  printf 'kdv-integrator-event:%s' "${LOCAL_IMAGE_TAG}"
}

default_local_optimizer_image() {
  printf 'kdv-optimizer:%s' "${LOCAL_IMAGE_TAG}"
}

load_orchestrator_settings() {
  ORCHESTRATOR_IMAGE_MODE="${ORCHESTRATOR_IMAGE_MODE:-$(read_env_value ORCHESTRATOR_IMAGE_MODE)}"
  ORCHESTRATOR_IMAGE_MODE="${ORCHESTRATOR_IMAGE_MODE:-local}"

  LOCAL_IMAGE_TAG="${LOCAL_IMAGE_TAG:-$(read_env_value LOCAL_IMAGE_TAG)}"
  LOCAL_IMAGE_TAG="${LOCAL_IMAGE_TAG:-$(default_image_tag)}"

  LOCAL_IMAGE="${LOCAL_IMAGE:-$(read_env_value LOCAL_IMAGE)}"
  if [[ -z "${LOCAL_IMAGE}" || "${LOCAL_IMAGE}" == "auto" ]]; then
    LOCAL_IMAGE="$(default_local_image)"
  fi

  SWARM_VERIFY_TIMEOUT="${SWARM_VERIFY_TIMEOUT:-$(read_env_value SWARM_VERIFY_TIMEOUT)}"
  SWARM_VERIFY_TIMEOUT="${SWARM_VERIFY_TIMEOUT:-180}"

  SWARM_VERIFY_INTERVAL="${SWARM_VERIFY_INTERVAL:-$(read_env_value SWARM_VERIFY_INTERVAL)}"
  SWARM_VERIFY_INTERVAL="${SWARM_VERIFY_INTERVAL:-5}"

  RUNTIME_ENV_SECRET_BASE="${RUNTIME_ENV_SECRET_BASE:-$(read_env_value RUNTIME_ENV_SECRET_BASE)}"
  RUNTIME_ENV_SECRET_BASE="${RUNTIME_ENV_SECRET_BASE:-$(read_env_value KDV_APP_ENV_PAYLOAD_SECRET_NAME)}"
  RUNTIME_ENV_SECRET_BASE="${RUNTIME_ENV_SECRET_BASE:-kdv_app_env_payload}"
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
  local optimizer_image

  case "${ORCHESTRATOR_IMAGE_MODE}" in
    local)
      optimizer_image="$(default_local_optimizer_image)"

      log "Building local Docker image for kdv-api: ${LOCAL_IMAGE}"
      docker build -t "${LOCAL_IMAGE}" "${PROJECT_ROOT}"
      export KDV_IMAGE="${LOCAL_IMAGE}"

      log "Building local Docker image for kdv-optimizer: ${optimizer_image}"
      docker build -t "${optimizer_image}" "${PROJECT_ROOT}/kdv-optimizer"
      export KDV_OPTIMIZER_IMAGE="${optimizer_image}"

      log "Using local Docker image for Swarm deploy: KDV_IMAGE=${KDV_IMAGE}"
      log "Using local Docker image for Swarm deploy: KDV_OPTIMIZER_IMAGE=${KDV_OPTIMIZER_IMAGE}"
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

run_versioned_env_secret_script() {
  local script_path export_env

  script_path="${SCRIPT_DIR}/render-versioned-env-secret.sh"
  if [[ ! -f "${script_path}" ]]; then
    log "ERROR: versioned env secret script not found: ${script_path}"
    exit 1
  fi

  log "Rendering versioned runtime env secret: ${script_path}"
  export_env="$(
    ORCHESTRATOR_ENV_FILE="${ENV_FILE}" \
    RUNTIME_ENV_SECRET_BASE="${RUNTIME_ENV_SECRET_BASE}" \
    "${script_path}"
  )"
  eval "${export_env}"
  log "Runtime env secret export applied: KDV_APP_ENV_PAYLOAD_SECRET_NAME=${KDV_APP_ENV_PAYLOAD_SECRET_NAME}"
}

verify_swarm_service() {
  local service_name elapsed replicas running desired
  service_name="$1"

  log "Verifying Swarm service ${service_name} (timeout=${SWARM_VERIFY_TIMEOUT}s)"

  elapsed=0
  while (( elapsed <= SWARM_VERIFY_TIMEOUT )); do
    if ! docker service inspect "${service_name}" >/dev/null 2>&1; then
      log "Waiting for service to appear: ${service_name}"
    else
      replicas="$(docker service ls --filter "name=${service_name}" --format '{{.Replicas}}' | head -n 1)"
      running="${replicas%%/*}"
      desired="${replicas##*/}"

      if [[ -n "${replicas}" && "${running}" == "${desired}" && "${desired}" != "0" ]]; then
        log "Swarm service is healthy: ${service_name} ${replicas}"
        return 0
      fi

      log "Waiting for service replicas: ${service_name} ${replicas:-unknown}"
    fi

    sleep "${SWARM_VERIFY_INTERVAL}"
    elapsed=$((elapsed + SWARM_VERIFY_INTERVAL))
  done

  log "ERROR: Swarm service did not reach desired replicas: ${service_name}"
  docker service ls --filter "name=${service_name}"
  docker service ps "${service_name}" --no-trunc || true
  exit 1
}

# ROLLBACK ІНСТРУКЦІЯ:
# 1. Вимкнути оптимізацію без відкату коду:
#    docker service update --env-add OPTIMIZER_URL=disabled ${STACK_NAME}_kdv-api
# 2. Або відкотити compose + передеплоїти попередній GIT_SHA.
# 3. DSpace/Koha дані не зачіпаються при будь-якому варіанті.

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

  load_orchestrator_settings
  run_ansible_secrets_if_configured
  run_validation_scripts
  run_deploy_adjacent_scripts
  run_versioned_env_secret_script
  prepare_deploy_image

  log "Rendering Swarm manifest (stack=${STACK_NAME}, env_file=${ENV_FILE})"
  docker compose --env-file "${ENV_FILE}" \
    -f "${compose_file}" \
    -f "${swarm_file}" \
    config > "${RAW_MANIFEST}"

  awk 'NR==1 && $1=="name:" {next} {print}' "${RAW_MANIFEST}" \
    | sed -E 's/^([[:space:]]*published:[[:space:]]*)"([0-9]+)"$/\1\2/' \
    | sed -E 's/^([[:space:]]*cpus:[[:space:]]*)([0-9]+(\.[0-9]+)?)$/\1"\2"/' \
    > "${DEPLOY_MANIFEST}"

  log "Deploying stack ${STACK_NAME}"
  deploy_args=(docker stack deploy -c "${DEPLOY_MANIFEST}")
  if [[ "${ORCHESTRATOR_IMAGE_MODE}" == "local" ]]; then
    deploy_args+=(--resolve-image never)
  fi
  deploy_args+=("${STACK_NAME}")
  "${deploy_args[@]}"

  verify_swarm_service "${SWARM_SERVICE_NAME}"
  verify_swarm_service "${STACK_NAME}_kdv-optimizer"

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
