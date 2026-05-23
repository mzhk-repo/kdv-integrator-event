#!/usr/bin/env bash
set -euo pipefail

GDRIVE_SECRET_BASE="${GDRIVE_SECRET_BASE:-gdrive_service_account_json}"
GDRIVE_VAULT_KEY="${GDRIVE_VAULT_KEY:-vault_rclone_service_account_json}"
GDRIVE_VAULT_FILE="${GDRIVE_VAULT_FILE:-}"
ANSIBLE_CONFIG_PATH="${ANSIBLE_CONFIG_PATH:-}"
INFRA_REPO_PATH="${INFRA_REPO_PATH:-}"
ENVIRONMENT_NAME="${ENVIRONMENT_NAME:-}"
SERVER_ENV="${SERVER_ENV:-}"
SECRET_TMP_FILE=""

log() {
  printf '[versioned-gdrive-secret] %s\n' "$*" >&2
}

cleanup() {
  if [[ -n "${SECRET_TMP_FILE}" && -f "${SECRET_TMP_FILE}" ]]; then
    rm -f "${SECRET_TMP_FILE}"
  fi
}

trap cleanup EXIT

normalize_env() {
  local raw
  raw="${1:-}"
  case "${raw}" in
    dev|development) printf 'dev' ;;
    prod|production) printf 'prod' ;;
    *) printf '' ;;
  esac
}

resolve_ansible_root() {
  if [[ -n "${INFRA_REPO_PATH}" ]]; then
    if [[ -f "${INFRA_REPO_PATH}/ansible.cfg" ]]; then
      printf '%s' "${INFRA_REPO_PATH}"
      return 0
    fi
    if [[ -f "${INFRA_REPO_PATH}/ansible/ansible.cfg" ]]; then
      printf '%s' "${INFRA_REPO_PATH}/ansible"
      return 0
    fi
  fi

  if [[ -f "/opt/Ansible/ansible/ansible.cfg" ]]; then
    printf '%s' "/opt/Ansible/ansible"
    return 0
  fi

  printf ''
}

resolve_vault_file() {
  local ansible_root env_name
  ansible_root="$1"
  env_name="$(normalize_env "${ENVIRONMENT_NAME:-${SERVER_ENV:-}}")"

  if [[ -n "${GDRIVE_VAULT_FILE}" ]]; then
    printf '%s' "${GDRIVE_VAULT_FILE}"
    return 0
  fi

  if [[ -z "${env_name}" ]]; then
    log "ERROR: ENVIRONMENT_NAME/SERVER_ENV is required to resolve Google Drive Vault file"
    exit 1
  fi

  printf '%s/inventories/%s/group_vars/all/rclone.vault.yml' "${ansible_root}" "${env_name}"
}

if ! command -v ansible-vault >/dev/null 2>&1; then
  log "ERROR: ansible-vault not found"
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

if ! command -v python3 >/dev/null 2>&1; then
  log "ERROR: python3 not found"
  exit 1
fi

ANSIBLE_ROOT="$(resolve_ansible_root)"
if [[ -z "${ANSIBLE_ROOT}" ]]; then
  log "ERROR: cannot resolve Ansible root; set INFRA_REPO_PATH or GDRIVE_VAULT_FILE"
  exit 1
fi

ANSIBLE_CONFIG_PATH="${ANSIBLE_CONFIG_PATH:-${ANSIBLE_ROOT}/ansible.cfg}"
if [[ ! -f "${ANSIBLE_CONFIG_PATH}" ]]; then
  log "ERROR: Ansible config not found: ${ANSIBLE_CONFIG_PATH}"
  exit 1
fi

GDRIVE_VAULT_FILE="$(resolve_vault_file "${ANSIBLE_ROOT}")"
if [[ ! -f "${GDRIVE_VAULT_FILE}" ]]; then
  log "ERROR: Google Drive Vault file not found: ${GDRIVE_VAULT_FILE}"
  exit 1
fi

SECRET_TMP_FILE="$(mktemp -t gdrive-service-account.XXXXXX.json)"
chmod 0600 "${SECRET_TMP_FILE}"

log "Rendering Google Drive service account secret from Ansible Vault (values hidden)"
ANSIBLE_CONFIG="${ANSIBLE_CONFIG_PATH}" ansible-vault view "${GDRIVE_VAULT_FILE}" \
  | GDRIVE_VAULT_KEY="${GDRIVE_VAULT_KEY}" python3 -c '
import json
import os
import sys

try:
    import yaml
except ImportError as exc:
    raise SystemExit(f"PyYAML is required to parse Vault YAML: {exc}")

key = os.environ["GDRIVE_VAULT_KEY"]
data = yaml.safe_load(sys.stdin.read()) or {}
raw = data.get(key)
if not isinstance(raw, str) or not raw.strip():
    raise SystemExit(f"Vault key {key!r} is missing or empty")
json.loads(raw)
sys.stdout.write(raw.strip() + "\n")
' > "${SECRET_TMP_FILE}"

secret_hash="$(sha256sum "${SECRET_TMP_FILE}" | awk '{print substr($1, 1, 12)}')"
secret_name="${GDRIVE_SECRET_BASE}_${secret_hash}"

if docker secret inspect "${secret_name}" >/dev/null 2>&1; then
  log "Google Drive service account secret already exists: ${secret_name}"
else
  log "Creating Google Drive service account secret: ${secret_name}"
  docker secret create "${secret_name}" "${SECRET_TMP_FILE}" >/dev/null
fi

log "Using Google Drive service account secret: ${secret_name}"
printf 'export GDRIVE_SERVICE_ACCOUNT_SECRET_NAME=%q\n' "${secret_name}"
