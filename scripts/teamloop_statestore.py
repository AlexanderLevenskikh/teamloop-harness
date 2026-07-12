#!/usr/bin/env python3
"""TeamLoop Harness — StateStore abstraction layer.

Provides an abstract `StateStore` interface and a `FileSystemStateStore`
implementation.  Enables deterministic content-addressed state loading
and testable file I/O without touching the real filesystem.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
import json
import os
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class StateStore(ABC):
    """Abstract state store for TeamLoop workspace data."""

    @abstractmethod
    def load(self, path: str) -> Dict[str, Any]:
        """Load a JSON file and return the parsed dict.

        Raises if the file is missing, empty, or not valid JSON.
        """
        ...

    @abstractmethod
    def load_safe(self, path: str) -> Optional[Dict[str, Any]]:
        """Load a JSON file, returning *None* when missing/empty/unparseable."""
        ...

    @abstractmethod
    def save(self, path: str, data: Any) -> None:
        """Write *data* as JSON to *path*, creating parent directories if needed."""
        ...

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Return ``True`` when the file at *path* exists."""
        ...

    @abstractmethod
    def append_jsonl(self, path: str, record: Dict[str, Any]) -> None:
        """Append a single JSON object as a line to a JSONL file."""
        ...

    @abstractmethod
    def read_jsonl(self, path: str) -> List[Dict[str, Any]]:
        """Read all JSON objects from a JSONL file.  Returns ``[]`` when missing."""
        ...


# ---------------------------------------------------------------------------
# Default filesystem implementation
# ---------------------------------------------------------------------------


class FileSystemStateStore(StateStore):
    """Concrete ``StateStore`` backed by the local filesystem.

    Mirrors the encoding fallback logic in ``teamloop_context.py``
    (utf-8-sig → utf-16 → utf-16-le → utf-16-be) so that replacing
    direct file I/O with the store produces zero behavioural change.
    """

    _ENCODINGS = ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be")

    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(root)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve(self, path: str) -> str:
        """Resolve a workspace-relative *path* to an absolute path."""
        if os.path.isabs(path):
            return path
        return os.path.join(self.root, path)

    def _read_json_file(self, path: str) -> Dict[str, Any]:
        """Read a JSON file with encoding fallback; raises on failure."""
        abs_path = self._resolve(path)
        for enc in self._ENCODINGS:
            try:
                with open(abs_path, "r", encoding=enc) as f:
                    return json.load(f)
            except (UnicodeDecodeError, ValueError):
                continue
        raise ValueError(f"Cannot decode JSON file: {abs_path}")

    def _read_json_file_safe(self, path: str) -> Optional[Dict[str, Any]]:
        """Read a JSON file or return ``None`` when missing/empty/unparseable."""
        abs_path = self._resolve(path)
        if not os.path.exists(abs_path) or os.path.getsize(abs_path) == 0:
            return None
        for enc in self._ENCODINGS:
            try:
                with open(abs_path, "r", encoding=enc) as f:
                    return json.load(f)
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                continue
        return None

    # ------------------------------------------------------------------
    # StateStore interface
    # ------------------------------------------------------------------

    def load(self, path: str) -> Dict[str, Any]:
        return self._read_json_file(path)

    def load_safe(self, path: str) -> Optional[Dict[str, Any]]:
        return self._read_json_file_safe(path)

    def save(self, path: str, data: Any) -> None:
        abs_path = self._resolve(path)
        parent = os.path.dirname(abs_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def exists(self, path: str) -> bool:
        return os.path.isfile(self._resolve(path))

    def append_jsonl(self, path: str, record: Dict[str, Any]) -> None:
        abs_path = self._resolve(path)
        parent = os.path.dirname(abs_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(abs_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read_jsonl(self, path: str) -> List[Dict[str, Any]]:
        abs_path = self._resolve(path)
        if not os.path.exists(abs_path):
            return []
        entries: List[Dict[str, Any]] = []
        for enc in self._ENCODINGS:
            try:
                with open(abs_path, "r", encoding=enc) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            entries.append(json.loads(line))
                    return entries
            except (UnicodeDecodeError, ValueError):
                continue
        raise ValueError(f"Cannot decode JSONL file: {abs_path}")
