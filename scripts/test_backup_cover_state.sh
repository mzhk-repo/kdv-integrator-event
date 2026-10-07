#!/usr/bin/env bash
# Restore-check the latest or specified cover state backup and publish textfile metrics.
set -euo pipefail
umask 027

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
METRICS_DIR="${NODE_EXPORTER_TEXTFILE_DIR:-/data/node-exporter-textfile}"
METRICS_FILE="${COVER_STATE_RESTORE_METRICS_FILE:-cover_state_restore_check.prom}"
ENV_LABEL="${COVER_STATE_RESTORE_ENV_LABEL:-${SERVER_ENV:-unknown}}"
SERVICE_LABEL="${COVER_STATE_RESTORE_SERVICE_LABEL:-kdv-integrator}"
RUN_TIMESTAMP="$(date +%s)"
SUCCESS_TIMESTAMP=0
STATUS=0
EXIT_CODE=0

[[ "${ENV_LABEL}" =~ ^[A-Za-z0-9_.-]+$ ]] || ENV_LABEL=unknown
[[ "${SERVICE_LABEL}" =~ ^[A-Za-z0-9_.-]+$ ]] || SERVICE_LABEL=kdv-integrator

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'USAGE'
Usage: scripts/test_backup_cover_state.sh [backup.sqlite3] [--age-key-file PATH]

Restores the selected (or latest) cover state backup into a temporary SQLite
database, then writes Prometheus textfile metrics.
USAGE
  exit 0
fi

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
warn() { printf '[%s] WARNING: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }

if python3 "${SCRIPT_DIR}/backup_cover_state.py" verify "$@"; then
  STATUS=1
  SUCCESS_TIMESTAMP="${RUN_TIMESTAMP}"
else
  EXIT_CODE=$?
fi

if [[ ! "${METRICS_FILE}" =~ ^[A-Za-z0-9_.-]+\.prom$ ]]; then
  warn "Invalid COVER_STATE_RESTORE_METRICS_FILE: ${METRICS_FILE}"
  [[ "${EXIT_CODE}" -ne 0 ]] || EXIT_CODE=1
  exit "${EXIT_CODE}"
fi

if mkdir -p "${METRICS_DIR}"; then
  metrics_tmp="$(mktemp "${METRICS_DIR}/.cover-state-restore.XXXXXX")" || exit 1
  if cat >"${metrics_tmp}" <<EOF
# HELP kdv_cover_state_restore_last_run_timestamp_seconds Unix timestamp of the last restore check attempt.
# TYPE kdv_cover_state_restore_last_run_timestamp_seconds gauge
kdv_cover_state_restore_last_run_timestamp_seconds{env="${ENV_LABEL}",service="${SERVICE_LABEL}"} ${RUN_TIMESTAMP}
# HELP kdv_cover_state_restore_last_success_timestamp_seconds Unix timestamp of the last successful restore check.
# TYPE kdv_cover_state_restore_last_success_timestamp_seconds gauge
kdv_cover_state_restore_last_success_timestamp_seconds{env="${ENV_LABEL}",service="${SERVICE_LABEL}"} ${SUCCESS_TIMESTAMP}
# HELP kdv_cover_state_restore_last_status Last restore check status (1=success, 0=failure).
# TYPE kdv_cover_state_restore_last_status gauge
kdv_cover_state_restore_last_status{env="${ENV_LABEL}",service="${SERVICE_LABEL}"} ${STATUS}
EOF
  then
    chmod 0644 "${metrics_tmp}"
    mv -f -- "${metrics_tmp}" "${METRICS_DIR%/}/${METRICS_FILE}"
  else
    rm -f -- "${metrics_tmp}"
    warn "Could not write restore-check metrics"
    [[ "${EXIT_CODE}" -ne 0 ]] || EXIT_CODE=1
  fi
else
  warn "Could not create metrics directory: ${METRICS_DIR}"
  [[ "${EXIT_CODE}" -ne 0 ]] || EXIT_CODE=1
fi

if [[ "${STATUS}" -eq 1 ]]; then
  log "Restore check passed; metrics written to ${METRICS_DIR%/}/${METRICS_FILE}"
else
  log "Restore check failed; metrics written to ${METRICS_DIR%/}/${METRICS_FILE}"
fi
exit "${EXIT_CODE}"
