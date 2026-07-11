#!/usr/bin/env python3
"""TeamLoop Harness — WorkspaceContext module.

Provides a single `WorkspaceContext` class that centralises lazy-loaded,
cached access to every workspace artifact (state, schemas, git status,
policies, profiles, ledgers, etc.).  Deliberately standalone: no import
of `teamloop-core` or `teamloop_fast_execution` to avoid circular
dependencies.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import subprocess
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Inline helpers (duplicates from teamloop-core.py to avoid circular imports)
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.datetime.now(
        datetime.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _read_json(path: str) -> Dict[str, Any]:
    """Read a JSON file, trying multiple encodings (same logic as teamloop-core.py)."""
    for enc in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            with open(path, "r", encoding=enc) as f:
                return json.load(f)
        except (UnicodeDecodeError, ValueError):
            continue
    raise ValueError(f"Cannot decode JSON file: {path}")


def _read_json_file_safe(path: str) -> Optional[Dict[str, Any]]:
    """Read JSON or return None when the file is missing / empty / unparseable."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    for enc in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            with open(path, "r", encoding=enc) as f:
                return json.load(f)
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            continue
    return None


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    """Read a JSONL file, trying multiple encodings."""
    if not os.path.exists(path):
        return []
    entries: List[Dict[str, Any]] = []
    for enc in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            with open(path, "r", encoding=enc) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
                return entries
        except (UnicodeDecodeError, ValueError):
            continue
    raise ValueError(f"Cannot decode JSONL file: {path}")


def _resolve_workspace(workspace: str) -> str:
    """Resolve a workspace path (identical to teamloop-core.resolve_workspace)."""
    if os.path.isabs(workspace):
        return workspace
    return os.path.join(os.getcwd(), workspace)


# ---------------------------------------------------------------------------
# WorkspaceContext
# ---------------------------------------------------------------------------


class WorkspaceContext:
    """Lazy-loaded, cached accessor for the entire TeamLoop workspace.

    Parameters
    ----------
    workspace_arg : str
        Workspace path (relative or absolute).  Resolved eagerly in
        ``__init__`` so every property works without re-resolving.

    Examples
    --------
    >>> ctx = WorkspaceContext(".teamloop")
    >>> print(ctx.workspace)
    /full/path/to/.teamloop
    >>> print(len(ctx.schemas))
    27
    """

    def __init__(self, workspace_arg: str) -> None:
        # Eager: resolve workspace and project_root immediately.
        self.workspace: str = _resolve_workspace(workspace_arg)
        self.project_root: str = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )

        # Private cache for lazy-loaded properties.
        self.__cache: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _cached(self, key: str, loader) -> Any:
        """Return cached value, computing once on first access."""
        if key not in self.__cache:
            self.__cache[key] = loader()
        return self.__cache[key]

    def _ws(self, *parts: str) -> str:
        """Path inside the workspace."""
        return os.path.join(self.workspace, *parts)

    # ------------------------------------------------------------------
    # Properties — lazy-loaded and cached
    # ------------------------------------------------------------------

    # Note: `workspace` and `project_root` are eager instance attributes
    # set in __init__ (not @property).

    @property
    def state(self) -> Dict[str, Any]:
        """team-state.json — raises if missing or invalid."""
        return self._cached(
            "state",
            lambda: _read_json(self._ws("state", "team-state.json")),
        )

    @property
    def state_safe(self) -> Optional[Dict[str, Any]]:
        """team-state.json — None if missing or unparseable."""
        return self._cached(
            "state_safe",
            lambda: _read_json_file_safe(self._ws("state", "team-state.json")),
        )

    @property
    def schemas(self) -> Dict[str, Dict[str, Any]]:
        """All schemas/*.schema.json mapped by basename (without .schema.json)."""
        return self._cached(
            "schemas",
            self.__load_schemas,
        )

    def schema(self, name: str) -> Dict[str, Any]:
        """Load a single schema by its basename (e.g. 'team-state')."""
        all_schemas = self.schemas
        if name not in all_schemas:
            raise KeyError(
                f"Schema '{name}' not found. "
                f"Available: {sorted(all_schemas.keys())}"
            )
        return all_schemas[name]

    @property
    def git_root(self) -> str:
        """Git repository root (git rev-parse --show-toplevel)."""
        return self._cached(
            "git_root",
            self.__git_root,
        )

    @property
    def git_status_entries(self) -> List[Dict[str, str]]:
        """Parsed git status --porcelain entries, each {status, path}."""
        return self._cached(
            "git_status_entries",
            self.__git_status_entries,
        )

    @property
    def backlog(self) -> List[Dict[str, Any]]:
        """All entries from state/backlog.jsonl."""
        return self._cached(
            "backlog",
            lambda: _read_jsonl(self._ws("state", "backlog.jsonl")),
        )

    @property
    def scope_policy(self) -> Dict[str, Any]:
        """policies/scope-policy.json — empty dict if missing."""
        return self._cached(
            "scope_policy",
            lambda: _read_json_file_safe(self._ws("policies", "scope-policy.json")) or {},
        )

    @property
    def gate_policy(self) -> Dict[str, Any]:
        """policies/gate-policy.json — empty dict if missing."""
        return self._cached(
            "gate_policy",
            lambda: _read_json_file_safe(self._ws("policies", "gate-policy.json")) or {},
        )

    @property
    def protected_paths(self) -> Dict[str, Any]:
        """policies/protected-paths.json — empty dict if missing."""
        return self._cached(
            "protected_paths",
            lambda: _read_json_file_safe(self._ws("policies", "protected-paths.json")) or {},
        )

    @property
    def active_profile(self) -> Dict[str, Any]:
        """profiles/active-profile.json — empty dict if missing."""
        return self._cached(
            "active_profile",
            lambda: _read_json_file_safe(self._ws("profiles", "active-profile.json")) or {},
        )

    @property
    def run_ledger(self) -> List[Dict[str, Any]]:
        """All entries from state/run-ledger.jsonl."""
        return self._cached(
            "run_ledger",
            lambda: _read_jsonl(self._ws("state", "run-ledger.jsonl")),
        )

    @property
    def blockers(self) -> List[Dict[str, Any]]:
        """All entries from state/blockers.jsonl."""
        return self._cached(
            "blockers",
            lambda: _read_jsonl(self._ws("state", "blockers.jsonl")),
        )

    @property
    def events(self) -> List[Dict[str, Any]]:
        """All entries from state/events.jsonl."""
        return self._cached(
            "events",
            lambda: _read_jsonl(self._ws("state", "events.jsonl")),
        )

    @property
    def current_run_id(self) -> str:
        """Current run id — from state or the last run-ledger entry."""
        return self._cached(
            "current_run_id",
            self.__current_run_id,
        )

    @property
    def current_task(self) -> Optional[Dict[str, Any]]:
        """state/current-task.json — None if missing or unparseable."""
        return self._cached(
            "current_task",
            lambda: _read_json_file_safe(self._ws("state", "current-task.json")),
        )

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def git_changed_paths(self) -> List[str]:
        """Return list of all changed file paths from git status."""
        return [entry["path"] for entry in self.git_status_entries]

    def git_staged_paths(self) -> List[str]:
        """Return list of staged file paths (index-side changes only).

        In porcelain v1, the first character of the status string
        corresponds to the index (staged area).
        """
        staged = []
        for entry in self.git_status_entries:
            status = entry["status"]
            if len(status) >= 1 and status[0] not in (" ", "?"):
                staged.append(entry["path"])
        return staged

    def find_run_dir(self, run_id: str) -> str:
        """Return the filesystem path for the given run directory."""
        if not run_id:
            raise ValueError("run_id must be non-empty")
        return os.path.join(self.workspace, "runs", run_id)

    def latest_sentinel_report(self) -> Optional[str]:
        """Return path to the most recent sentinel-inspection.json, or None."""
        runs_dir = os.path.join(self.workspace, "runs")
        if not os.path.isdir(runs_dir):
            return None
        for name in reversed(sorted(os.listdir(runs_dir))):
            candidate = os.path.join(runs_dir, name, "sentinel-inspection.json")
            if os.path.isfile(candidate):
                return candidate
        return None

    def latest_gate_result(self) -> Optional[str]:
        """Return path to the most recent gate-result.json, or None."""
        runs_dir = os.path.join(self.workspace, "runs")
        if not os.path.isdir(runs_dir):
            return None
        for name in reversed(sorted(os.listdir(runs_dir))):
            candidate = os.path.join(runs_dir, name, "gate-result.json")
            if os.path.isfile(candidate):
                return candidate
        return None

    # ------------------------------------------------------------------
    # Private loaders
    # ------------------------------------------------------------------

    def __load_schemas(self) -> Dict[str, Dict[str, Any]]:
        schemas_dir = os.path.join(self.project_root, "schemas")
        schema_map: Dict[str, Dict[str, Any]] = {}
        if not os.path.isdir(schemas_dir):
            return schema_map
        for filename in os.listdir(schemas_dir):
            if filename.endswith(".schema.json"):
                base = filename.replace(".schema.json", "")
                schema_map[base] = _read_json(os.path.join(schemas_dir, filename))
        return schema_map

    def __git_root(self) -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            pass
        return self.project_root

    def __git_status_entries(self) -> List[Dict[str, str]]:
        """Parse git status --porcelain into list of {status, path} dicts.

        Follows the same parsing logic as teamloop-core._get_git_status_entries
        but returns the simplified {status, path} structure (without raw).
        """
        entries: List[Dict[str, str]] = []
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain=v1"],
                capture_output=True, text=True, timeout=10,
            )
            git_root_result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=10,
            )
            git_root = git_root_result.stdout.strip() if git_root_result.returncode == 0 else ""
        except (subprocess.SubprocessError, FileNotFoundError):
            return entries

        for raw_line in result.stdout.splitlines():
            line = raw_line.rstrip("\r")
            if len(line) < 3:
                continue

            # Porcelain v1: "XY path" or "XY old -> new"
            status = line[:2]
            path_part = line[3:] if len(line) > 3 else ""

            if " -> " in path_part:
                arrow_idx = path_part.index(" -> ")
                path_part = path_part[arrow_idx + 4:]

            if not path_part:
                continue

            # Make relative if absolute
            if os.path.isabs(path_part) and git_root:
                try:
                    path_part = os.path.relpath(path_part, git_root)
                except ValueError:
                    pass

            entries.append({"status": status, "path": path_part})

        return entries

    def __current_run_id(self) -> str:
        """Determine current run id from state or run-ledger."""
        # Try state first
        state = _read_json_file_safe(self._ws("state", "team-state.json"))
        if state and state.get("currentRunId"):
            return str(state["currentRunId"])
        # Fall back to last run-ledger entry
        ledger = _read_jsonl(self._ws("state", "run-ledger.jsonl"))
        if ledger:
            return str(ledger[-1].get("runId", ""))
        return ""
