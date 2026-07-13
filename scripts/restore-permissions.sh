#!/usr/bin/env bash
# Restore executable bits on shell scripts after ZIP extraction.
#
# When a project is packaged as a ZIP on Windows (where NTFS does not
# store Unix permission bits), the extracted .sh files will lose their
# executable flag on Linux.  Running this script from inside the
# extracted tree (or from any parent directory) restores +x on every
# .sh file it finds.
#
# Usage:
#   bash scripts/restore-permissions.sh          # restores in this project root
#   bash path/to/restore-permissions.sh /target  # restores inside /target
#
set -euo pipefail

TARGET="${1:-.}"

find "$TARGET" -name '*.sh' -type f ! -executable -exec chmod +x {} +
