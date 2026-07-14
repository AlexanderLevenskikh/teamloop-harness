#!/usr/bin/env python3
"""YourAITeam — Product Director L0 Advisory Checks.

Advisory checks flag risky task patterns as WARNING only.
They never block execution (exit 0 always, severity is WARNING never CRITICAL).
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Inline helpers (no circular import of teamloop-core)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Individual advisory checks — each returns list of finding dicts
# ---------------------------------------------------------------------------


def check_scope_too_broad(task: Dict[str, Any]) -> List[Dict[str, str]]:
    """Flag tasks with more than 10 scope paths."""
    findings: List[Dict[str, str]] = []
    scope = task.get("scope", [])
    if not isinstance(scope, list):
        return findings
    if len(scope) > 10:
        findings.append({
            "check": "scope-too-broad",
            "severity": "WARNING",
            "detail": (
                f"Task has {len(scope)} scope paths (threshold: 10). "
                "Consider splitting into smaller tasks."
            ),
        })
    return findings


def check_task_too_vague(task: Dict[str, Any]) -> List[Dict[str, str]]:
    """Flag tasks where title < 20 chars or description is missing."""
    findings: List[Dict[str, str]] = []
    title = task.get("title", "")
    description = task.get("description", "")

    if len(title) < 20:
        findings.append({
            "check": "task-too-vague",
            "severity": "WARNING",
            "detail": (
                f"Task title '{title}' is only {len(title)} characters "
                "(threshold: 20). Provide a more descriptive title."
            ),
        })

    if not description:
        findings.append({
            "check": "task-too-vague",
            "severity": "WARNING",
            "detail": "Task has no description field. Add context for reviewers.",
        })

    return findings


def check_missing_success_criteria(task: Dict[str, Any]) -> List[Dict[str, str]]:
    """Flag tasks with empty successCriteria."""
    findings: List[Dict[str, str]] = []
    criteria = task.get("successCriteria", [])
    if not isinstance(criteria, list) or len(criteria) == 0:
        findings.append({
            "check": "missing-success-criteria",
            "severity": "WARNING",
            "detail": "Task has no successCriteria. Define measurable acceptance criteria.",
        })
    return findings


def check_forbidden_actions_empty(task: Dict[str, Any]) -> List[Dict[str, str]]:
    """Flag tasks with no forbiddenActions."""
    findings: List[Dict[str, str]] = []
    forbidden = task.get("forbiddenActions", [])
    if not isinstance(forbidden, list) or len(forbidden) == 0:
        findings.append({
            "check": "forbidden-actions-empty",
            "severity": "WARNING",
            "detail": "Task has no forbiddenActions. Specify boundaries to prevent scope creep.",
        })
    return findings


def check_no_evidence(task: Dict[str, Any]) -> List[Dict[str, str]]:
    """Flag tasks with no requiredEvidence."""
    findings: List[Dict[str, str]] = []
    evidence = task.get("requiredEvidence", [])
    if not isinstance(evidence, list) or len(evidence) == 0:
        findings.append({
            "check": "no-evidence",
            "severity": "WARNING",
            "detail": "Task has no requiredEvidence. Specify what proof is needed for completion.",
        })
    return findings


# ---------------------------------------------------------------------------
# Aggregate runner
# ---------------------------------------------------------------------------


def advisory_check(task: Dict[str, Any]) -> Dict[str, Any]:
    """Run all advisory checks on a single task.

    Returns:
        {
            "schemaVersion": 1,
            "status": "WARNING" | "PASS",
            "taskId": "...",
            "findings": [
                {"check": "...", "severity": "WARNING", "detail": "..."},
                ...
            ]
        }
    """
    findings: List[Dict[str, str]] = []
    checkers = [
        check_scope_too_broad,
        check_task_too_vague,
        check_missing_success_criteria,
        check_forbidden_actions_empty,
        check_no_evidence,
    ]
    for checker in checkers:
        findings.extend(checker(task))

    task_id = task.get("taskId", "unknown")
    return {
        "schemaVersion": 1,
        "status": "WARNING" if findings else "PASS",
        "taskId": task_id,
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Workspace-level entry point
# ---------------------------------------------------------------------------


def run_advisory(workspace: str) -> Dict[str, Any]:
    """Run all advisory checks on the current task in the given workspace.

    Args:
        workspace: Path to the .teamloop workspace directory.

    Returns:
        Advisory report dict (same shape as advisory_check return).
    """
    task_path = os.path.join(workspace, "state", "current-task.json")
    task = _read_json_file_safe(task_path)

    if task is None:
        return {
            "schemaVersion": 1,
            "status": "PASS",
            "taskId": "none",
            "findings": [],
        }

    return advisory_check(task)
