#!/usr/bin/env bash
# release-package.sh — Create a distributable ZIP archive
# Usage: bash scripts/release-package.sh [output-dir]
#
# Creates a ZIP archive of the harness with install.sh included.
# Note: ZIP does not preserve Unix executable bits. Users must run
# 'bash scripts/install.sh' after extraction on Linux/WSL.

set -euo pipefail

OUTPUT_DIR="${1:-.}"
HARNESS_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Determine version from git tag or fallback
VERSION=""
if command -v git &>/dev/null; then
  VERSION=$(git -C "$HARNESS_DIR" describe --tags --always 2>/dev/null || echo "dev")
fi

ARCHIVE_NAME="teamloop-harness-${VERSION}.zip"
ARCHIVE_PATH="$OUTPUT_DIR/$ARCHIVE_NAME"

echo "Creating $ARCHIVE_PATH..."

# Create ZIP (exclude common unwanted files)
cd "$HARNESS_DIR"
zip -r "$ARCHIVE_PATH" . \
  -x "*.git*" \
  -x "*.pyc" \
  -x "__pycache__/*" \
  -x ".teamloop/*" \
  -x "*.egg-info/*" \
  -x "*.log" \
  -x "*.cache" \
  -x "node_modules/*" \
  2>/dev/null || {
    # zip not available — try Python alternative
    python3 -c "
import zipfile, os, sys
archive = zipfile.ZipFile('$ARCHIVE_PATH', 'w', zipfile.ZIP_DEFLATED)
for root, dirs, files in os.walk('$HARNESS_DIR'):
    # Skip unwanted directories
    dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__', '.teamloop', 'node_modules', '*.egg-info')]
    for f in files:
        if f.endswith(('.pyc', '.log', '.cache')):
            continue
        path = os.path.join(root, f)
        arcname = os.path.relpath(path, '$HARNESS_DIR')
        archive.write(path, arcname)
archive.close()
"
  }

echo "Archive created: $ARCHIVE_PATH"
echo ""
echo "IMPORTANT: After extraction on Linux/WSL, run:"
echo "  bash scripts/install.sh"
echo "This restores executable permissions that ZIP cannot preserve."
