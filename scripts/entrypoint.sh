#!/bin/bash
set -euo pipefail

RUNTIME_ENV_PAYLOAD="${RUNTIME_ENV_PAYLOAD:-/run/secrets/app_env_payload}"
SECRETS_DIR="${SECRETS_DIR:-/run/secrets}"

# Розгортаємо versioned dotenv payload у runtime ENV для основного процесу.
if [[ -f "${RUNTIME_ENV_PAYLOAD}" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "${RUNTIME_ENV_PAYLOAD}"
    set +a
fi

# Backward compatibility: старий контракт one-secret-per-env.
if [[ -d "${SECRETS_DIR}" ]]; then
    for secret in "${SECRETS_DIR}"/*; do
        [[ -f "${secret}" ]] || continue
        secret_name="$(basename "${secret}")"
        [[ "${secret_name}" != "app_env_payload" ]] || continue
        [[ "${secret_name}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        printf -v "${secret_name}" '%s' "$(cat "${secret}")"
        export "${secret_name}"
    done
fi

exec "$@"
