#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-$(command -v python3 || command -v python)}"
exec "$PY" "$SCRIPT_DIR/codex_support.py" --live-smoke "$@"
