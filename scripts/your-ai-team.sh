#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 1 ]]; then
  echo "Usage: your-ai-team.sh <propose|negotiate|accept|materialize|codex-doctor|codex-smoke> [args...]" >&2
  exit 2
fi
ACTION="$1"; shift
case "$ACTION" in propose|negotiate|accept|materialize|codex-doctor|codex-smoke) ;; *) echo "Unknown action: $ACTION" >&2; exit 2;; esac
PY="${PY:-$(command -v python3 2>/dev/null || command -v python 2>/dev/null)}"
"$PY" "$(dirname "$0")/teamloop-core.py" "team-$ACTION" "$@"
