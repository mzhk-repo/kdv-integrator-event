#!/usr/bin/env bash
set -euo pipefail
umask 077

log() {
  printf '[init-volume] %s\n' "$*" >&2
}

fail() {
  log "ERROR: $*"
  exit 1
}

validate_path() {
  local path="$1" normalized resolved
  [[ "${path}" == /*/* && "${path}" != *$'\n'* ]] || fail "Expected an absolute storage directory below /: ${path}"
  normalized="$(realpath -ms -- "${path}")"
  resolved="$(realpath -m -- "${path}")"
  [[ "${normalized}" == /*/* ]] || fail "Storage path must be below a top-level directory: ${path}"
  [[ "${normalized}" == "${resolved}" ]] || fail "Storage path must not contain symlinks: ${path}"
  [[ ! -e "${resolved}" || -d "${resolved}" ]] || fail "Storage path is not a directory: ${path}"
  printf '%s' "${resolved}"
}

[[ -n "${COVERS_STORAGE_HOST_PATH:-}" ]] || fail "COVERS_STORAGE_HOST_PATH is required"
[[ -n "${COVER_STATE_HOST_PATH:-}" ]] || fail "COVER_STATE_HOST_PATH is required"

covers_path="$(validate_path "${COVERS_STORAGE_HOST_PATH}")"
state_path="$(validate_path "${COVER_STATE_HOST_PATH}")"
[[ "${covers_path}" != "${state_path}" && "${covers_path}" != "${state_path}/"* && "${state_path}" != "${covers_path}/"* ]] \
  || fail "Cover storage and state directories must not overlap"

# Validate every managed directory before making any filesystem changes.
validate_path "${covers_path}/assets" >/dev/null
validate_path "${covers_path}/.incoming" >/dev/null

# Keep existing ownership and file contents. New directories belong to the caller;
# the current Integrator runs as container root, while nginx only reads assets.
if ! install -d -m 0755 -- "${covers_path}" "${covers_path}/assets"; then
  fail "Cannot initialize cover directories; run with an account allowed to create and chmod these paths"
fi
if ! install -d -m 0700 -- "${covers_path}/.incoming" "${state_path}"; then
  fail "Cannot initialize private directories; run with an account allowed to create and chmod these paths"
fi

log "Ready: ${covers_path} and assets (0755); .incoming and ${state_path} (0700)"
