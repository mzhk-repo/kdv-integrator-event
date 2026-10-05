#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

if [[ "${1:-}" == "--apply" ]]; then
    exec .venv/bin/python -m src.cover_state.gc --apply
fi
exec .venv/bin/python -m src.cover_state.gc
