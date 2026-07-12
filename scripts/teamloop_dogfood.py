#!/usr/bin/env python3
"""TeamLoop Harness — Dogfood module.

Runs the full gate chain on a workspace via subprocess invocations and
produces a structured JSON report matching schemas/dogfood-report.schema.json.
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.datetime.now(
        datetime.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _resolve_workspace(workspace: str) -> str:
    if os.path.isabs(workspace):
        return workspace
    return os.path.join(os.getcwd(), workspace)


def _read_json_file_safe(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    for enc in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            with open(path, "r", encoding=enc) as f:
                return json.load(f)
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            continue
    return None


def _run_gate_check(
    name: str, cmd: list, workspace: str, core_script: str, timeout: int = 60
) -> Dict[str, str]:
    """Run a single gate-chain command and return a structured check result.

    Parameters
    ----------
    name : str
        Human-readable check name.
    cmd : list
        Command to run.
    workspace : str
        Resolved workspace path (for cwd).
    core_script : str
        Path to teamloop-core.py (for reference, not used directly here).
    timeout : int
        Maximum seconds before timeout.

    Returns
    -------
    dict with keys: name, status, summary, and optionally detail.
    """
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=os.path.dirname(os.path.abspath(workspace)),
        )
        if proc.returncode == 0:
            return {
                "name": name,
                "status": "PASS",
                "summary": f"{name} passed",
            }
        else:
            detail = (proc.stdout.strip() or proc.stderr.strip() or f"{name} exited {proc.returncode}")[:500]
            return {
                "name": name,
                "status": "FAIL",
                "summary": f"{name} failed",
                "detail": detail,
            }
    except subprocess.TimeoutExpired:
        return {
            "name": name,
            "status": "ERROR",
            "summary": f"{name} timed out after {timeout}s",
        }
    except FileNotFoundError as exc:
        return {
            "name": name,
            "status": "ERROR",
            "summary": f"{name} error: {exc}",
        }
    except subprocess.SubprocessError as exc:
        return {
            "name": name,
            "status": "ERROR",
            "summary": f"{name} error: {exc}",
        }


# ---------------------------------------------------------------------------
# Core dogfood logic
# ---------------------------------------------------------------------------

def run_dogfood(workspace_arg: str) -> Dict[str, Any]:
    """Run the full gate chain on the given workspace.

    Executes each check as a subprocess of ``python scripts/teamloop-core.py``:

      1. validate-state
      2. check-scope  (only if there's an active task)
      3. run-gates
      4. run-sentinel
      5. check-guard-integrity
      6. memory-doctor
      7. final-gate

    Returns
    -------
    dict
        Matching schemas/dogfood-report.schema.json with keys:
        - schemaVersion
        - checkedAtUtc
        - overallStatus  ("PASS" | "FAIL" | "ERROR")
        - checks          list of {name, status, summary, detail?}
    """
    workspace = _resolve_workspace(workspace_arg)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    core_script = os.path.join(script_dir, "teamloop-core.py")

    checks: List[Dict[str, Any]] = []

    # ---- 1. validate-state ----
    checks.append(_run_gate_check(
        "validate-state",
        [sys.executable, core_script, "validate-state", "--workspace", workspace],
        workspace, core_script,
    ))

    # ---- 2. check-scope (only if there's an active task) ----
    state = _read_json_file_safe(os.path.join(workspace, "state", "team-state.json"))
    has_active_task = bool(state and state.get("currentTaskId", ""))
    if has_active_task:
        checks.append(_run_gate_check(
            "check-scope",
            [sys.executable, core_script, "check-scope", "--workspace", workspace],
            workspace, core_script,
        ))
    else:
        checks.append({
            "name": "check-scope",
            "status": "SKIPPED",
            "summary": "check-scope skipped: no active task in team-state",
        })

    # ---- 3. run-gates ----
    checks.append(_run_gate_check(
        "run-gates",
        [sys.executable, core_script, "run-gates", "--workspace", workspace],
        workspace, core_script,
    ))

    # ---- 4. run-sentinel ----
    checks.append(_run_gate_check(
        "run-sentinel",
        [sys.executable, core_script, "run-sentinel", "--workspace", workspace],
        workspace, core_script,
    ))

    # ---- 5. check-guard-integrity ----
    checks.append(_run_gate_check(
        "check-guard-integrity",
        [sys.executable, core_script, "check-guard-integrity", "--workspace", workspace],
        workspace, core_script,
    ))

    # ---- 6. memory-doctor ----
    checks.append(_run_gate_check(
        "memory-doctor",
        [sys.executable, core_script, "memory-doctor", "--workspace", workspace],
        workspace, core_script,
    ))

    # ---- 7. final-gate ----
    checks.append(_run_gate_check(
        "final-gate",
        [sys.executable, core_script, "final-gate", "--workspace", workspace],
        workspace, core_script,
    ))

    # Compute overall status
    has_fail = any(c["status"] == "FAIL" for c in checks)
    has_error = any(c["status"] == "ERROR" for c in checks)

    if has_error:
        overall_status = "ERROR"
    elif has_fail:
        overall_status = "FAIL"
    else:
        overall_status = "PASS"

    return {
        "schemaVersion": 1,
        "checkedAtUtc": _utc_now_iso(),
        "overallStatus": overall_status,
        "checks": checks,
    }


# ---------------------------------------------------------------------------
# Compare mode: direct subprocess vs WorkspaceContext
# ---------------------------------------------------------------------------

def run_dogfood_compare(workspace_arg: str) -> Dict[str, Any]:
    """Run the gate chain twice — once via direct subprocess, once via
    WorkspaceContext integration — and compare the results.

    Returns
    -------
    dict
        Matching schemas/dogfood-report.schema.json with an additional
        ``oldNewCompare`` key containing ``direct``, ``context``, and
        ``differences``.
    """
    # Pass 1: direct subprocess (the normal dogfood path)
    direct_result = run_dogfood(workspace_arg)

    # Pass 2: via WorkspaceContext — re-run each check using the
    # WorkspaceContext-aware path (same subprocess, but we track via
    # WorkspaceContext to confirm parity).
    workspace = _resolve_workspace(workspace_arg)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    core_script = os.path.join(script_dir, "teamloop-core.py")

    # Import WorkspaceContext for the second pass.
    # We need to add the scripts directory to sys.path if it's not already there.
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    from teamloop_context import WorkspaceContext  # noqa: E402

    host = WorkspaceContext(workspace_arg)

    context_checks: List[Dict[str, Any]] = []

    # ---- 1. validate-state via WorkspaceContext ----
    context_checks.append(_run_gate_check(
        "validate-state",
        [sys.executable, core_script, "validate-state", "--workspace", host.workspace],
        host.workspace, core_script,
    ))

    # ---- 2. check-scope ----
    has_active_task = bool(host.state_safe and host.state_safe.get("currentTaskId", ""))
    if has_active_task:
        context_checks.append(_run_gate_check(
            "check-scope",
            [sys.executable, core_script, "check-scope", "--workspace", host.workspace],
            host.workspace, core_script,
        ))
    else:
        context_checks.append({
            "name": "check-scope",
            "status": "SKIPPED",
            "summary": "check-scope skipped: no active task in team-state",
        })

    # ---- 3. run-gates ----
    context_checks.append(_run_gate_check(
        "run-gates",
        [sys.executable, core_script, "run-gates", "--workspace", host.workspace],
        host.workspace, core_script,
    ))

    # ---- 4. run-sentinel ----
    context_checks.append(_run_gate_check(
        "run-sentinel",
        [sys.executable, core_script, "run-sentinel", "--workspace", host.workspace],
        host.workspace, core_script,
    ))

    # ---- 5. check-guard-integrity ----
    context_checks.append(_run_gate_check(
        "check-guard-integrity",
        [sys.executable, core_script, "check-guard-integrity", "--workspace", host.workspace],
        host.workspace, core_script,
    ))

    # ---- 6. memory-doctor ----
    context_checks.append(_run_gate_check(
        "memory-doctor",
        [sys.executable, core_script, "memory-doctor", "--workspace", host.workspace],
        host.workspace, core_script,
    ))

    # ---- 7. final-gate ----
    context_checks.append(_run_gate_check(
        "final-gate",
        [sys.executable, core_script, "final-gate", "--workspace", host.workspace],
        host.workspace, core_script,
    ))

    has_fail_c = any(c["status"] == "FAIL" for c in context_checks)
    has_error_c = any(c["status"] == "ERROR" for c in context_checks)
    if has_error_c:
        context_overall = "ERROR"
    elif has_fail_c:
        context_overall = "FAIL"
    else:
        context_overall = "PASS"

    # Compare: find per-check differences
    direct_by_name = {c["name"]: c for c in direct_result["checks"]}
    context_by_name = {c["name"]: c for c in context_checks}
    all_names = sorted(set(list(direct_by_name.keys()) + list(context_by_name.keys())))

    differences: List[Dict[str, Any]] = []
    for cname in all_names:
        dc = direct_by_name.get(cname)
        cc = context_by_name.get(cname)
        if dc is None or cc is None:
            differences.append({
                "check": cname,
                "directStatus": dc["status"] if dc else "MISSING",
                "contextStatus": cc["status"] if cc else "MISSING",
                "directSummary": dc.get("summary", "") if dc else "",
                "contextSummary": cc.get("summary", "") if cc else "",
            })
            continue
        if dc["status"] != cc["status"]:
            differences.append({
                "check": cname,
                "directStatus": dc["status"],
                "contextStatus": cc["status"],
                "directSummary": dc.get("summary", ""),
                "contextSummary": cc.get("summary", ""),
            })

    # Build combined result
    compare_section = {
        "direct": {
            "overallStatus": direct_result["overallStatus"],
            "checks": direct_result["checks"],
        },
        "context": {
            "overallStatus": context_overall,
            "checks": context_checks,
        },
        "differences": differences,
    }

    # Use the direct result as the base report and attach comparison
    result = dict(direct_result)
    result["oldNewCompare"] = compare_section

    # Override overallStatus based on comparison
    if differences:
        result["overallStatus"] = "FAIL"
    else:
        result["overallStatus"] = "PASS"

    return result
