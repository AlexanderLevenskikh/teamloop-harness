#!/usr/bin/env bash
# install.sh — Restore executable permissions after ZIP extraction
# Usage: bash scripts/install.sh [--workspace DIR]
#
# ZIP archives do not preserve Unix executable mode bits.
# This script restores them for all .sh files in the harness directory.
#
# This is the supported installation step for Linux/WSL users
# who extracted the harness from a ZIP archive.

set -euo pipefail

HARNESS_DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace|-w) HARNESS_DIR="$2"; shift 2 ;;
    --harness-dir)  HARNESS_DIR="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# Find the harness root (parent of scripts/)
if [ -z "$HARNESS_DIR" ]; then
  HARNESS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
fi

SCRIPTS_DIR="$HARNESS_DIR/scripts"

if [ ! -d "$SCRIPTS_DIR" ]; then
  echo "Error: scripts/ directory not found in $HARNESS_DIR" >&2
  exit 1
fi

echo "Restoring executable permissions in $SCRIPTS_DIR..."
find "$SCRIPTS_DIR" -name '*.sh' -exec chmod +x {} +
# Extensionless command shims (for example scripts/validate-state) are also
# part of the supported CLI surface and lose mode bits in ZIP archives.
find "$SCRIPTS_DIR" -maxdepth 1 -type f ! -name '*.*' -exec chmod +x {} +
# Test launchers are shipped as part of the supported verification surface and
# also lose mode bits in ZIP archives.
if [ -d "$HARNESS_DIR/tests" ]; then
  find "$HARNESS_DIR/tests" -maxdepth 1 -name '*.sh' -exec chmod +x {} +
fi
echo "Done. Shell wrappers, command shims, and test launchers are executable."

# Optional: verify python is available
if command -v python3 &>/dev/null; then
  echo ""
  echo "Validating installed script surfaces..."
  python3 "$SCRIPTS_DIR/validate_scripts.py" --root "$HARNESS_DIR" --require-executable
  echo ""
  echo "Verifying runtime..."
  python3 "$SCRIPTS_DIR/teamloop-core.py" --help >/dev/null 2>&1 && echo "Runtime OK." || echo "Runtime check skipped."
else
  echo ""
  echo "Note: python3 not found. Install Python 3.8+ to use the harness."
fi
