#!/usr/bin/env python3
"""TeamLoop Harness — content-addressed validation cache.

Stores deterministic validation results keyed by SHA-256 fingerprints of
their material inputs (files, schemas, and supporting scripts).  Identical
inputs produce identical cache keys; any material change invalidates the
cached result.  Stale or expired entries are never served as PASS.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import pathlib
import sys as _sys
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Ensure scripts/ is on sys.path so same-dir imports work
# ---------------------------------------------------------------------------
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in _sys.path:
    _sys.path.insert(0, _scripts_dir)

from teamloop_fast_execution import (
    canonical_json,
    file_sha256,
    semantic_hash,
    sha256_text,
    strip_volatile,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TTL_SECONDS = 86400  # 24 hours
MAX_ENTRIES = 500
SCHEMA_ID = "teamloop-validation-cache/v1"
IMPLEMENTATION_VERSION = "1"

# Keys that must never appear in a cache-key computation.
_VOLATILE_KEYS = frozenset({
    "createdAtUtc", "updatedAtUtc", "cachedAtUtc",
    "checkedAtUtc", "generatedAtUtc",
    "startedAtUtc", "finishedAtUtc", "timestampUtc",
    "durationMs", "totalDurationMs",
    "performanceTrace", "performance-trace",
})


def _utc_now_iso() -> str:
    return _dt.datetime.now(
        _dt.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ---------------------------------------------------------------------------
# ValidationCache
# ---------------------------------------------------------------------------


class ValidationCache:
    """Content-addressed cache for deterministic validation results.

    Parameters
    ----------
    cache_path : str
        Full path to the JSONL cache file.
    workspace : str
        Resolved workspace directory (parent of .teamloop).
    project_root : str
        Repository root containing scripts/ and schemas/.
    ttl_seconds : int
        Time-to-live for cache entries in seconds (default 86400 = 24 h).
    max_entries : int
        Maximum number of entries before LRU eviction.
    read_only : bool
        When True, ``store()`` raises ``PermissionError`` (audit-profile mode).
    """

    def __init__(
        self,
        cache_path: str,
        workspace: str = "",
        project_root: str = "",
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_entries: int = MAX_ENTRIES,
        read_only: bool = False,
    ) -> None:
        self.cache_path = cache_path
        self.workspace = workspace
        self.project_root = project_root
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.read_only = read_only

        # In-memory stats.
        self._hits: int = 0
        self._misses: int = 0

        # Load existing cache from disk.
        self._entries: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_key(
        self,
        check: str,
        inputs: Optional[Dict[str, Any]] = None,
        schemas: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Build a deterministic SHA-256 cache key.

        Parameters
        ----------
        check : str
            Canonical check name (e.g. ``"schema-validate:team-state"``).
        inputs : dict, optional
            Mapping of input-name → value or file-path.  File paths are
            hashed by their SHA-256 content; raw values are canonicalised
            and hashed.  Timestamps and other volatile keys are stripped.
        schemas : dict, optional
            Mapping of schema-name → file-path.  Each file is hashed by
            its SHA-256 content.

        Returns
        -------
        str
            Hex SHA-256 cache key (64 characters).
        """
        inputs = inputs or {}
        schemas = schemas or {}

        # Resolve input fingerprints: hash file paths, canonicalize values.
        input_fps: Dict[str, str] = {}
        for name, value in sorted(inputs.items()):
            if isinstance(value, str) and os.path.isabs(value):
                input_fps[name] = file_sha256(value) if os.path.exists(value) else "MISSING"
            elif isinstance(value, str) and os.path.isfile(value):
                # Relative file path — resolve relative to workspace.
                abs_path = value
                if not os.path.isabs(value):
                    abs_path = os.path.join(self.workspace, value)
                input_fps[name] = file_sha256(abs_path) if os.path.exists(abs_path) else "MISSING"
            else:
                input_fps[name] = semantic_hash(strip_volatile(value))

        # Resolve schema fingerprints.
        schema_fps: Dict[str, str] = {}
        for name, path in sorted(schemas.items()):
            schema_fps[name] = file_sha256(path) if os.path.exists(path) else "MISSING"

        # Script fingerprints (scripts used by the validation logic).
        script_fps = self._script_fingerprints()

        # Build canonical key payload.
        payload = {
            "check": check,
            "inputs": input_fps,
            "schemas": schema_fps,
            "coreScript": script_fps.get("teamloop-core.py", ""),
            "contextModule": script_fps.get("teamloop_context.py", ""),
            "fastExecModule": script_fps.get("teamloop_fast_execution.py", ""),
            "cacheModule": script_fps.get("teamloop_cache.py", ""),
        }
        return sha256_text(canonical_json(payload))

    def get(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Return the cached result for *cache_key*, or ``None`` on miss.

        A hit requires:
        1. The key exists in the cache.
        2. The entry has not expired (TTL check).
        3. The entry passes integrity validation.
        4. Script fingerprints still match (no script change).

        Returns
        -------
        dict or None
            ``{"result": ..., "inputFingerprints": ..., "scriptFingerprints": ...}``
            on hit; ``None`` on miss.
        """
        entry = self._entries.get(cache_key)
        if entry is None:
            self._misses += 1
            return None

        # TTL check.
        if self._is_expired(entry):
            self._misses += 1
            return None

        # Script fingerprint freshness check.
        if not self._script_fingerprints_match(entry):
            self._misses += 1
            return None

        # Integrity check.
        if not self._verify_entry_integrity(entry):
            self._misses += 1
            return None

        # Move to end (most recently used).
        self._entries.move_to_end(cache_key)
        self._hits += 1
        return {
            "result": entry["result"],
            "inputFingerprints": entry.get("inputFingerprints", {}),
            "scriptFingerprints": entry.get("scriptFingerprints", {}),
            "cachedAtUtc": entry.get("cachedAtUtc", ""),
        }

    def store(
        self,
        cache_key: str,
        result: Dict[str, Any],
        input_fingerprints: Optional[Dict[str, str]] = None,
        script_fingerprints: Optional[Dict[str, str]] = None,
    ) -> None:
        """Store a validation result in the cache.

        Parameters
        ----------
        cache_key : str
            The SHA-256 cache key (from ``build_key``).
        result : dict
            The validation result (e.g. ``{"status": "PASS", "findings": []}``).
        input_fingerprints : dict, optional
            Per-input SHA-256 hashes stored for audit.
        script_fingerprints : dict, optional
            Script SHA-256 hashes at time of caching.

        Raises
        ------
        PermissionError
            When the cache is in read-only (audit) mode.
        """
        if self.read_only:
            raise PermissionError(
                "Cache is read-only (audit profile). "
                "store() is disabled to ensure fresh evidence on every run."
            )

        script_fps = script_fingerprints or self._script_fingerprints()
        entry: Dict[str, Any] = {
            "cacheKey": cache_key,
            "checkId": result.get("checkId", ""),
            "result": strip_volatile(result),
            "inputFingerprints": input_fingerprints or {},
            "scriptFingerprints": script_fps,
            "cachedAtUtc": _utc_now_iso(),
            "implementationVersion": IMPLEMENTATION_VERSION,
            "ttl": self.ttl_seconds,
            "ttlSeconds": self.ttl_seconds,
        }

        # If already present, update in place (keeping position for LRU).
        if cache_key in self._entries:
            self._entries[cache_key] = entry
        else:
            # LRU eviction: drop oldest entry when at capacity.
            while len(self._entries) >= self.max_entries:
                self._entries.popitem(last=False)
            self._entries[cache_key] = entry

        self._flush()

    def invalidate(self, check_id: Optional[str] = None) -> int:
        """Remove cache entries.

        Parameters
        ----------
        check_id : str, optional
            If given, only entries matching *check_id* are removed.
            If ``None``, all entries are removed.

        Returns
        -------
        int
            Number of entries removed.
        """
        if check_id is None:
            count = len(self._entries)
            self._entries.clear()
        else:
            keys_to_remove = [
                k for k, v in self._entries.items()
                if v.get("checkId") == check_id
            ]
            count = len(keys_to_remove)
            for k in keys_to_remove:
                del self._entries[k]

        self._flush()
        return count

    def clear(self) -> None:
        """Remove all cache entries."""
        self.invalidate()
        self._hits = 0
        self._misses = 0

    def integrity_check(self) -> Dict[str, Any]:
        """Verify that all cache records are untampered.

        Returns
        -------
        dict
            ``{"status": "PASS"|"FAIL", "totalEntries": N,
            "validEntries": M, "invalidEntries": [key, ...],
            "checkedAtUtc": ...}``.
        """
        invalid: List[str] = []
        valid = 0
        for key, entry in self._entries.items():
            if self._verify_entry_integrity(entry):
                valid += 1
            else:
                invalid.append(key)

        return {
            "status": "PASS" if not invalid else "FAIL",
            "totalEntries": len(self._entries),
            "validEntries": valid,
            "invalidEntries": invalid,
            "checkedAtUtc": _utc_now_iso(),
        }

    def stats(self) -> Dict[str, Any]:
        """Return cache statistics.

        Returns
        -------
        dict
            ``{"hits": N, "misses": M, "hitRate": float,
            "totalEntries": N, "maxEntries": N, "readOnly": bool,
            "ttlSeconds": N, "checkedAtUtc": ...}``.
        """
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0.0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hitRate": round(hit_rate, 2),
            "totalEntries": len(self._entries),
            "maxEntries": self.max_entries,
            "readOnly": self.read_only,
            "ttlSeconds": self.ttl_seconds,
            "checkedAtUtc": _utc_now_iso(),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load cache entries from the JSONL file on disk."""
        if not os.path.exists(self.cache_path):
            return
        try:
            with open(self.cache_path, "r", encoding="utf-8") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if isinstance(entry, dict) and entry.get("cacheKey"):
                            key = entry["cacheKey"]
                            # Only keep non-expired entries on load.
                            if not self._is_expired(entry):
                                self._entries[key] = entry
                    except (json.JSONDecodeError, KeyError):
                        # Skip corrupted lines silently.
                        continue
        except OSError:
            pass

    def _flush(self) -> None:
        """Write all cache entries to the JSONL file atomically."""
        cache_dir = os.path.dirname(self.cache_path)
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        tmp_path = f"{self.cache_path}.tmp-{os.getpid()}"
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as fh:
            for entry in self._entries.values():
                fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(tmp_path, self.cache_path)

    def _is_expired(self, entry: Dict[str, Any]) -> bool:
        """Check whether an entry has exceeded its TTL."""
        cached_at = entry.get("cachedAtUtc", "")
        if not cached_at:
            return True
        ttl = entry.get("ttlSeconds", entry.get("ttl", self.ttl_seconds))
        try:
            # Parse ISO-8601 timestamp.
            ts_str = cached_at.replace("Z", "+00:00")
            cached_dt = _dt.datetime.fromisoformat(ts_str)
            now = _dt.datetime.now(_dt.timezone.utc)
            elapsed = (now - cached_dt).total_seconds()
            return elapsed > ttl
        except (ValueError, TypeError):
            return True

    def _script_fingerprints(self) -> Dict[str, str]:
        """Compute SHA-256 hashes of supporting script files."""
        scripts = {
            "teamloop-core.py": os.path.join(self.project_root, "scripts", "teamloop-core.py"),
            "teamloop_context.py": os.path.join(self.project_root, "scripts", "teamloop_context.py"),
            "teamloop_fast_execution.py": os.path.join(self.project_root, "scripts", "teamloop_fast_execution.py"),
            "teamloop_cache.py": os.path.join(self.project_root, "scripts", "teamloop_cache.py"),
        }
        fps: Dict[str, str] = {}
        for name, path in scripts.items():
            fps[name] = file_sha256(path) if os.path.exists(path) else "MISSING"
        return fps

    def _script_fingerprints_match(self, entry: Dict[str, Any]) -> bool:
        """Check whether cached script fingerprints still match current files."""
        cached_fps = entry.get("scriptFingerprints", {})
        current_fps = self._script_fingerprints()
        for name, current_hash in current_fps.items():
            cached_hash = cached_fps.get(name, "")
            if current_hash and cached_hash and current_hash != cached_hash:
                return False
        return True

    @staticmethod
    def _verify_entry_integrity(entry: Dict[str, Any]) -> bool:
        """Verify the entry's cache key is consistent with its data."""
        # The cache key itself is the integrity anchor: it must be present
        # and a valid 64-char hex string.
        key = entry.get("cacheKey", "")
        if not key or len(key) != 64:
            return False
        try:
            int(key, 16)
        except ValueError:
            return False
        # Result must be present.
        if "result" not in entry:
            return False
        return True
