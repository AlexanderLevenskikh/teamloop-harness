#!/usr/bin/env bash
set -euo pipefail
PY="${PY:-$(command -v python3 2>/dev/null || command -v python 2>/dev/null)}"
exec "$PY" "$(dirname "$0")/validate_scripts.py" "$@"
