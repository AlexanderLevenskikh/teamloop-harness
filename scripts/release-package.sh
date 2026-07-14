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
COMMIT_SHA=""
if command -v git &>/dev/null; then
  VERSION=$(git -C "$HARNESS_DIR" describe --tags --always 2>/dev/null || echo "dev")
  COMMIT_SHA=$(git -C "$HARNESS_DIR" rev-parse HEAD 2>/dev/null || echo "unknown")
fi

ARCHIVE_NAME="your-ai-team-${VERSION}.zip"
ARCHIVE_PATH="$OUTPUT_DIR/$ARCHIVE_NAME"
MANIFEST_PATH="$OUTPUT_DIR/package-manifest.json"

echo "Creating $ARCHIVE_PATH..."

# Use Python for reliable ZIP creation with proper exclusions and manifest
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)}"
if [ -z "$PYTHON_BIN" ]; then
  echo "Error: Python is required to create a release package." >&2
  exit 1
fi

"$PYTHON_BIN" - "$HARNESS_DIR" "$ARCHIVE_PATH" "$MANIFEST_PATH" "$VERSION" "$COMMIT_SHA" <<'PYEOF'
import zipfile
import os
import sys
import json
import hashlib
import datetime

harness_dir = sys.argv[1]
archive_path = sys.argv[2]
manifest_path = sys.argv[3]
version = sys.argv[4]
commit_sha = sys.argv[5]

# Directories/patterns to exclude from the archive.
EXCLUDED_DIRS = {
    '.git',
    '.teamloop',
    '.pytest_cache',
    '__pycache__',
    'node_modules',
    '.idea',
    '.vscode',
    'dist',
    'build',
    'tmp',
    'temp',
    'coverage',
    'test-results',
    '.mypy_cache',
    '.ruff_cache',
    '.tox',
    '.venv',
    'venv',
}

# Glob-like prefixes for excluded directories (e.g. .teamloop-*)
EXCLUDED_PREFIXES = (
    '.teamloop-',
)

# File extensions/patterns to exclude
EXCLUDED_SUFFIXES = (
    '.pyc', '.pyo', '.log',
    '.cache',
)

def is_excluded(relpath):
    """Return True if the relative path should be excluded from the archive."""
    parts = relpath.replace('\\', '/').split('/')
    
    # Check each component for excluded directories
    for part in parts:
        if part in EXCLUDED_DIRS:
            return True
        for prefix in EXCLUDED_PREFIXES:
            if part.startswith(prefix):
                return True
    
    basename = parts[-1]
    
    # Exclude file suffixes
    for suffix in EXCLUDED_SUFFIXES:
        if basename.endswith(suffix):
            return True
    
    # Exclude diagnostic bundles and release archives
    if basename.startswith('diagnostic-') and basename.endswith('.zip'):
        return True
    if (basename.startswith('your-ai-team-') or basename.startswith('teamloop-harness-')) and basename.endswith('.zip'):
        return True
    
    return False

# Collect files to include
included_files = []
excluded_files = {}

def record_excluded(category, path):
    excluded_files.setdefault(category, []).append(path.replace('\\', '/'))

for root, dirs, files in os.walk(harness_dir):
    # Filter directories in-place to skip unwanted ones
    retained_dirs = []
    for d in dirs:
        rel_dir = os.path.relpath(os.path.join(root, d), harness_dir).replace('\\', '/')
        if d in EXCLUDED_DIRS or any(d.startswith(p) for p in EXCLUDED_PREFIXES):
            record_excluded('excluded_directory', rel_dir + '/')
        else:
            retained_dirs.append(d)
    dirs[:] = retained_dirs
    
    for f in files:
        full_path = os.path.join(root, f)
        relpath = os.path.relpath(full_path, harness_dir)
        relpath_normalized = relpath.replace('\\', '/')
        
        if is_excluded(relpath_normalized):
            cat = "excluded_file"
            if f.endswith(('.pyc', '.pyo')):
                cat = "python_cache"
            elif any(relpath_normalized.startswith(d + '/') or relpath_normalized == d for d in EXCLUDED_DIRS):
                cat = "runtime_debris"
            elif any(relpath_normalized.startswith(p) for p in EXCLUDED_PREFIXES):
                cat = "runtime_debris"
            record_excluded(cat, relpath_normalized)
            continue
        
        included_files.append((full_path, relpath_normalized))

# Compute per-file checksums before creating the archive.
file_checksums = {}
for full_path, relpath in included_files:
    digest = hashlib.sha256()
    with open(full_path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(65536), b''):
            digest.update(chunk)
    file_checksums[relpath] = 'sha256:' + digest.hexdigest()

# Create ZIP archive.  Store all regular files as 0644 deliberately: this
# makes the ZIP contract deterministic and proves install.sh restores Unix
# executable permissions rather than relying on producer-specific ZIP attrs.
with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zf:
    for full_path, relpath in sorted(included_files, key=lambda x: x[1]):
        with open(full_path, 'rb') as fh:
            data = fh.read()
        info = zipfile.ZipInfo(relpath)
        info.date_time = (2026, 1, 1, 0, 0, 0)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3
        info.external_attr = (0o100644 & 0xFFFF) << 16
        zf.writestr(info, data)

# Compute archive checksum
sha256 = hashlib.sha256()
with open(archive_path, 'rb') as fh:
    for chunk in iter(lambda: fh.read(65536), b''):
        sha256.update(chunk)
archive_checksum = sha256.hexdigest()

# Generate package manifest
manifest = {
    "schemaVersion": 1,
    "packageVersion": version,
    "sourceCommit": commit_sha,
    "archiveName": os.path.basename(archive_path),
    "archiveChecksum": f"sha256:{archive_checksum}",
    "filesIncluded": len(included_files),
    "filesIncludedList": sorted([rf for _, rf in included_files]),
    "filesExcludedByCategory": {
        key: sorted(set(value)) for key, value in sorted(excluded_files.items())
    },
    "fileChecksums": dict(sorted(file_checksums.items())),
    "packageFormat": "zip",
    "installCommand": "bash scripts/install.sh",
    "createdAtUtc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    "platformNotes": "ZIP does not preserve Unix executable bits. Run 'bash scripts/install.sh' after extraction on Linux/WSL.",
}

with open(manifest_path, 'w', encoding='utf-8', newline='\n') as mf:
    json.dump(manifest, mf, indent=2, ensure_ascii=False)
    mf.write('\n')

PYEOF

echo "Archive created: $ARCHIVE_PATH"
echo "Manifest created: $MANIFEST_PATH"
echo ""
echo "IMPORTANT: After extraction on Linux/WSL, run:"
echo "  bash scripts/install.sh"
echo "This restores executable permissions that ZIP cannot preserve."
