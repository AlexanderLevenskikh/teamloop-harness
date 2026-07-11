#!/usr/bin/env python3
"""Fast-execution runtime primitives for TeamLoopHarness.

This module owns deterministic execution-policy resolution, immutable execution
manifests, performance traces, progress snapshots, no-progress decisions, and
role-routing decisions.  It deliberately does not mutate TeamLoop lifecycle
state; teamloop-core.py remains the single lifecycle/state writer.
"""

from __future__ import annotations

import copy
import datetime as _dt
import fnmatch
import hashlib
import json
import os
import pathlib
import subprocess
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SCHEMA_POLICY = "teamloop-execution-policy/v1"
SCHEMA_MANIFEST = "teamloop-execution-manifest/v1"
SCHEMA_MANIFEST_VALIDATION = "teamloop-execution-manifest-validation/v1"
SCHEMA_TRACE = "teamloop-performance-trace/v1"
SCHEMA_PROGRESS = "teamloop-progress-snapshot/v1"
SCHEMA_NO_PROGRESS = "teamloop-no-progress-result/v1"
SCHEMA_ROLE_ROUTING = "teamloop-role-routing-decision/v1"

VOLATILE_KEYS = frozenset({
    "createdAtUtc", "updatedAtUtc", "checkedAtUtc", "generatedAtUtc",
    "startedAtUtc", "finishedAtUtc", "timestampUtc", "durationMs",
    "totalDurationMs", "performanceTrace", "performance-trace",
})


class FastExecutionError(RuntimeError):
    """Raised for deterministic contract or artifact failures."""


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise FastExecutionError(f"{path}: expected JSON object")
    return data


def read_json_optional(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path):
        return None
    return read_json(path)


def write_json_atomic(path: str, value: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)


def read_jsonl(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, 1):
            text = raw.strip()
            if not text:
                continue
            try:
                item = json.loads(text)
            except json.JSONDecodeError as exc:
                raise FastExecutionError(f"{path} line {line_no}: malformed JSON: {exc.msg}") from exc
            if not isinstance(item, dict):
                raise FastExecutionError(f"{path} line {line_no}: expected JSON object")
            rows.append(item)
    return rows


def append_jsonl(path: str, value: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def semantic_hash(value: Any) -> str:
    return sha256_text(canonical_json(strip_volatile(value)))


def strip_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: strip_volatile(item)
            for key, item in sorted(value.items())
            if key not in VOLATILE_KEYS and key != "integrity" and key != "semanticFingerprint"
        }
    if isinstance(value, list):
        return [strip_volatile(item) for item in value]
    return value


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repo_root(workspace: str) -> str:
    start = os.path.abspath(os.path.dirname(workspace))
    try:
        proc = subprocess.run(
            ["git", "-C", start, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return start


def _git_output(repo: str, args: Sequence[str]) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", repo, *args], capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FastExecutionError(f"git {' '.join(args)} failed: {exc}") from exc
    if proc.returncode != 0:
        raise FastExecutionError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def git_head(repo: str) -> str:
    try:
        return _git_output(repo, ["rev-parse", "HEAD"]).strip()
    except FastExecutionError:
        return ""


def git_changed_paths(repo: str) -> List[str]:
    try:
        raw = _git_output(repo, ["status", "--porcelain=v1", "--untracked-files=all"])
    except FastExecutionError:
        return []
    result: List[str] = []
    for line in raw.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        result.append(path.replace("\\", "/"))
    return sorted(set(result))


def _normalize_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def path_matches(path: str, pattern: str) -> bool:
    path = _normalize_relative_path(path)
    pattern = _normalize_relative_path(pattern)
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    return fnmatch.fnmatchcase(path, pattern)


def _pattern_prefix(pattern: str) -> str:
    normalized = _normalize_relative_path(pattern)
    parts = []
    for part in normalized.split("/"):
        if any(ch in part for ch in "*?["):
            break
        parts.append(part)
    return "/".join(parts)


def patterns_overlap(a: str, b: str) -> bool:
    pa, pb = _pattern_prefix(a), _pattern_prefix(b)
    if pa and pb and (pa == pb or pa.startswith(pb + "/") or pb.startswith(pa + "/")):
        return True
    # Literal-vs-pattern fallback.
    return path_matches(pa or a, b) or path_matches(pb or b, a)


def _state(workspace: str) -> Dict[str, Any]:
    return read_json(os.path.join(workspace, "state", "team-state.json"))


def _backlog(workspace: str) -> List[Dict[str, Any]]:
    return read_jsonl(os.path.join(workspace, "state", "backlog.jsonl"))


def load_task(workspace: str, task_id: str = "") -> Dict[str, Any]:
    state = _state(workspace)
    resolved_id = task_id or state.get("currentTaskId", "")
    current_path = os.path.join(workspace, "state", "current-task.json")
    current = read_json_optional(current_path)
    if current and (not resolved_id or current.get("taskId") == resolved_id):
        return current
    for task in _backlog(workspace):
        if task.get("taskId") == resolved_id:
            return task
    if not resolved_id:
        for task in _backlog(workspace):
            if task.get("status") in ("READY", "IN_PROGRESS", "NEEDS_REVIEW", "REVIEW_FAILED"):
                return task
    raise FastExecutionError(f"task '{resolved_id or '<none>'}' not found")


def resolve_run_id(workspace: str, explicit_run_id: str = "", task_id: str = "") -> str:
    if explicit_run_id:
        return explicit_run_id
    state = _state(workspace)
    if state.get("currentRunId"):
        return str(state["currentRunId"])
    resolved_task = task_id or state.get("currentTaskId", "")
    ledger = read_jsonl(os.path.join(workspace, "state", "run-ledger.jsonl"))
    for entry in reversed(ledger):
        if not resolved_task or entry.get("taskId") == resolved_task:
            return str(entry.get("runId", ""))
    return ""


def run_dir(workspace: str, run_id: str) -> str:
    if not run_id:
        raise FastExecutionError("run id is required")
    return os.path.join(workspace, "runs", run_id)


def _task_revision(task: Dict[str, Any]) -> str:
    immutable_fields = {
        key: task.get(key)
        for key in (
            "schemaVersion", "taskId", "title", "priority", "origin", "scope",
            "allowedWrites", "forbiddenWrites", "requiredEvidence", "successCriteria",
            "forbiddenActions", "humanRequired",
        )
        if key in task
    }
    return semantic_hash(immutable_fields)


def _policy_sources(workspace: str, task: Dict[str, Any]) -> Dict[str, str]:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paths = {
        "task": None,
        "scopePolicy": os.path.join(workspace, "policies", "scope-policy.json"),
        "rolePolicy": os.path.join(workspace, "policies", "role-policy.json"),
        "gatePolicy": os.path.join(workspace, "policies", "gate-policy.json"),
        "protectedPathPolicy": os.path.join(workspace, "policies", "protected-paths.json"),
        "activeProfile": os.path.join(workspace, "profiles", "active-profile.json"),
        "executionPolicySchema": os.path.join(project_root, "schemas", "execution-policy.schema.json"),
        "executionManifestSchema": os.path.join(project_root, "schemas", "execution-manifest.schema.json"),
        "roleRoutingSchema": os.path.join(project_root, "schemas", "role-routing-decision.schema.json"),
        "taskSchema": os.path.join(project_root, "schemas", "task.schema.json"),
        "fastExecutionRuntime": os.path.abspath(__file__),
    }
    result = {"task": _task_revision(task)}
    for name, path in paths.items():
        if name == "task":
            continue
        result[name] = file_sha256(path) if path and os.path.exists(path) else "NOT_CONFIGURED"
    return result


def _protected_scope(workspace: str, task: Dict[str, Any]) -> Tuple[str, List[str]]:
    policy_path = os.path.join(workspace, "policies", "protected-paths.json")
    if not os.path.exists(policy_path):
        return "NOT_CONFIGURED", []
    policy = read_json(policy_path)
    protected = policy.get("protectedPaths", [])
    task_patterns = list(task.get("scope", [])) + list(task.get("allowedWrites", []))
    matches = sorted({tp for tp in task_patterns for pp in protected if patterns_overlap(tp, pp)})
    if matches:
        return "PROTECTED_SCOPE", matches
    return "CLEAN", []


def _latest_sentinel_findings(workspace: str) -> List[Dict[str, Any]]:
    runs = os.path.join(workspace, "runs")
    if not os.path.isdir(runs):
        return []
    for name in reversed(sorted(os.listdir(runs))):
        path = os.path.join(runs, name, "sentinel-inspection.json")
        data = read_json_optional(path)
        if data is not None:
            return list(data.get("findings", []))
    return []


def _active_no_progress(workspace: str, run_id: str) -> Optional[Dict[str, Any]]:
    if not run_id:
        return None
    return read_json_optional(os.path.join(run_dir(workspace, run_id), "no-progress-result.json"))


def resolve_policy_data(
    workspace: str,
    run_id: str,
    task: Dict[str, Any],
    requested_profile: str = "",
    no_progress_threshold: int = 2,
) -> Dict[str, Any]:
    if requested_profile and requested_profile not in ("fast", "standard", "audit"):
        raise FastExecutionError("profile must be one of: fast, standard, audit")
    if no_progress_threshold < 2 or no_progress_threshold > 10:
        raise FastExecutionError("no-progress threshold must be between 2 and 10")

    protected_status, protected_patterns = _protected_scope(workspace, task)
    priority = str(task.get("priority", "P2"))
    risk = "high" if priority == "P0" else "medium" if priority == "P1" else "low"
    findings = _latest_sentinel_findings(workspace)
    high_findings = [
        finding for finding in findings
        if str(finding.get("severity", "")).upper() in ("HIGH", "CRITICAL")
        and str(finding.get("status", "OPEN")).upper() not in ("RESOLVED", "CLOSED", "VERIFIED")
    ]
    no_progress = _active_no_progress(workspace, run_id)
    no_progress_active = bool(no_progress and no_progress.get("status") == "NO_PROGRESS_DETECTED")

    reason_codes: List[str] = []
    selected = requested_profile or ("audit" if risk == "high" else "standard" if risk == "medium" else "fast")
    if requested_profile:
        reason_codes.append("EXPLICIT_OPERATOR_REQUEST")
    else:
        reason_codes.append(f"TASK_RISK_{risk.upper()}")

    unsafe_fast = protected_status == "PROTECTED_SCOPE" or no_progress_active or bool(high_findings)
    if protected_status == "PROTECTED_SCOPE":
        reason_codes.append("PROTECTED_PATH_SCOPE")
    if no_progress_active:
        reason_codes.append("PRIOR_NO_PROGRESS")
    if high_findings:
        reason_codes.append("UNRESOLVED_HIGH_OR_CRITICAL_FINDING")
    if unsafe_fast and selected != "audit":
        selected = "audit"
        reason_codes.append("PROFILE_ESCALATED_TO_AUDIT")

    role_sets = {
        "fast": (["executor"], ["change-reviewer", "watchdog", "sentinel"]),
        "standard": (["executor", "change-reviewer"], ["watchdog", "sentinel"]),
        "audit": (["executor", "change-reviewer", "watchdog", "sentinel"], []),
    }
    required_roles, conditional_roles = role_sets[selected]

    policy: Dict[str, Any] = {
        "schemaVersion": 1,
        "schemaId": SCHEMA_POLICY,
        "runId": run_id,
        "taskId": str(task.get("taskId", "")),
        "selectedProfile": selected,
        "selectionReason": "; ".join(reason_codes),
        "reasonCodes": reason_codes,
        "taskRisk": risk,
        "protectedPathStatus": protected_status,
        "protectedScopePatterns": protected_patterns,
        "requiredRoles": required_roles,
        "conditionalRoles": conditional_roles,
        "deterministicChecks": [
            "execution-contract-integrity", "scope-validation", "evidence-integrity",
            "runtime-state-integrity", "required-project-gates", "continuation-decision",
            "sentinel-policy", "final-gate",
        ],
        "triggers": {
            "reviewer": [
                "profile-requires-review", "protected-path-change", "semantic-or-policy-change",
                "required-gate-failure", "final-handoff", "evidence-disagreement",
            ],
            "watchdog": [
                "NO_PROGRESS_DETECTED", "repeated-gate-without-material-change",
                "scope-or-permission-violation", "contradictory-runtime-state",
                "two-failed-bounded-cycles", "unexpected-expansion",
            ],
            "sentinel": [
                "final-handoff-policy", "protected-runtime-change", "evidence-manipulation",
                "scope-bypass", "gate-weakening", "test-suppression", "manual-state-mutation",
                "audit-profile", "critical-review-or-watchdog-finding",
            ],
        },
        "invariants": {
            "scopeIntegrityCannotBeDisabled": True,
            "evidenceIntegrityCannotBeDisabled": True,
            "runtimeStateIntegrityCannotBeDisabled": True,
            "requiredProjectGatesCannotBeDisabled": True,
            "finalSentinelCannotBeBypassed": True,
            "finalGateCannotBeBypassed": True,
            "safeCheckpointIsNotDone": True,
        },
        "noProgressThreshold": no_progress_threshold,
        "sourceFingerprints": _policy_sources(workspace, task),
        "createdAtUtc": utc_now_iso(),
    }
    fingerprint = semantic_hash(policy)
    policy["semanticFingerprint"] = fingerprint
    policy["integrity"] = {"algorithm": "sha256", "semanticSha256": fingerprint}
    return policy


def materialize_policy(
    workspace: str,
    run_id: str,
    task_id: str = "",
    requested_profile: str = "",
    no_progress_threshold: int = 2,
) -> Tuple[Dict[str, Any], bool]:
    task = load_task(workspace, task_id)
    policy = resolve_policy_data(workspace, run_id, task, requested_profile, no_progress_threshold)
    path = os.path.join(run_dir(workspace, run_id), "execution-policy.json")
    existing = read_json_optional(path)
    if existing is not None:
        if not verify_integrity(existing):
            raise FastExecutionError("existing execution-policy.json failed integrity validation")
        if existing.get("semanticFingerprint") != policy.get("semanticFingerprint"):
            raise FastExecutionError(
                "execution policy inputs changed for the existing run; create a fresh run/task revision"
            )
        return existing, True
    write_json_atomic(path, policy)
    return policy, False


def _required_gates(workspace: str) -> List[str]:
    policy_path = os.path.join(workspace, "policies", "gate-policy.json")
    if not os.path.exists(policy_path):
        return []
    policy = read_json(policy_path)
    return sorted(
        str(item.get("name")) for item in policy.get("gates", [])
        if item.get("required", True) and item.get("name")
    )


def materialize_manifest(workspace: str, run_id: str, task_id: str = "") -> Tuple[Dict[str, Any], bool]:
    task = load_task(workspace, task_id)
    policy_path = os.path.join(run_dir(workspace, run_id), "execution-policy.json")
    policy = read_json_optional(policy_path)
    if policy is None:
        raise FastExecutionError("execution-policy.json is required before manifest materialization")
    if not verify_integrity(policy):
        raise FastExecutionError("execution-policy.json failed integrity validation")
    if policy.get("taskId") != task.get("taskId") or policy.get("runId") != run_id:
        raise FastExecutionError("execution policy task/run identity mismatch")

    protected_status, _ = _protected_scope(workspace, task)
    manifest: Dict[str, Any] = {
        "schemaVersion": 1,
        "schemaId": SCHEMA_MANIFEST,
        "runId": run_id,
        "taskId": str(task.get("taskId", "")),
        "taskRevision": _task_revision(task),
        "repositoryHead": git_head(_repo_root(workspace)),
        "executionProfile": str(policy.get("selectedProfile", "")),
        "allowedRoots": sorted(set(task.get("scope", []) + task.get("allowedWrites", []))),
        "allowedFiles": [],
        "forbiddenRoots": sorted(set(task.get("forbiddenWrites", []))),
        "forbiddenOperations": sorted(set(task.get("forbiddenActions", []))),
        "allowedCommandClasses": ["teamloop-runtime", "declared-project-gate", "read-only-inspection"],
        "requiredGates": _required_gates(workspace),
        "requiredEvidence": list(task.get("requiredEvidence", [])),
        "protectedPathStatus": protected_status,
        "policyFingerprint": policy.get("semanticFingerprint", ""),
        "sourceFingerprints": copy.deepcopy(policy.get("sourceFingerprints", {})),
        "createdAtUtc": utc_now_iso(),
    }
    fingerprint = semantic_hash(manifest)
    manifest["semanticFingerprint"] = fingerprint
    manifest["integrity"] = {"algorithm": "sha256", "semanticSha256": fingerprint}

    path = os.path.join(run_dir(workspace, run_id), "execution-manifest.json")
    existing = read_json_optional(path)
    if existing is not None:
        if not verify_integrity(existing):
            raise FastExecutionError("existing execution-manifest.json failed integrity validation")
        if existing.get("semanticFingerprint") != manifest.get("semanticFingerprint"):
            raise FastExecutionError(
                "execution manifest inputs changed for the existing run; create a fresh run/task revision"
            )
        return existing, True
    write_json_atomic(path, manifest)
    return manifest, False


def verify_integrity(artifact: Dict[str, Any]) -> bool:
    expected = artifact.get("semanticFingerprint", "")
    integrity = artifact.get("integrity", {})
    if not expected or integrity.get("algorithm") != "sha256" or integrity.get("semanticSha256") != expected:
        return False
    return semantic_hash(artifact) == expected


def _run_ledger_entry(workspace: str, run_id: str) -> Optional[Dict[str, Any]]:
    for entry in read_jsonl(os.path.join(workspace, "state", "run-ledger.jsonl")):
        if entry.get("runId") == run_id:
            return entry
    return None


def validate_contract(workspace: str, run_id: str, write_result: bool = True) -> Dict[str, Any]:
    errors: List[str] = []
    checks: List[Dict[str, Any]] = []
    run_path = run_dir(workspace, run_id)
    policy_path = os.path.join(run_path, "execution-policy.json")
    manifest_path = os.path.join(run_path, "execution-manifest.json")
    policy = read_json_optional(policy_path)
    manifest = read_json_optional(manifest_path)

    def add(name: str, ok: bool, summary: str) -> None:
        checks.append({"name": name, "status": "PASS" if ok else "FAIL", "summary": summary})
        if not ok:
            errors.append(summary)

    add("policy-present", policy is not None, "execution-policy.json is present" if policy else "execution-policy.json is missing")
    add("manifest-present", manifest is not None, "execution-manifest.json is present" if manifest else "execution-manifest.json is missing")
    if policy is not None:
        add("policy-integrity", verify_integrity(policy), "execution policy integrity matches" if verify_integrity(policy) else "execution policy integrity mismatch or manual mutation")
    if manifest is not None:
        add("manifest-integrity", verify_integrity(manifest), "execution manifest integrity matches" if verify_integrity(manifest) else "execution manifest integrity mismatch or manual mutation")

    if policy is not None and manifest is not None:
        add("run-identity", policy.get("runId") == run_id == manifest.get("runId"), "run identity matches" if policy.get("runId") == run_id == manifest.get("runId") else "run identity mismatch")
        add("task-identity", policy.get("taskId") == manifest.get("taskId"), "task identity matches" if policy.get("taskId") == manifest.get("taskId") else "task identity mismatch")
        add("profile-consistency", policy.get("selectedProfile") == manifest.get("executionProfile"), "execution profile matches" if policy.get("selectedProfile") == manifest.get("executionProfile") else "execution profile drift detected")
        add("policy-fingerprint", policy.get("semanticFingerprint") == manifest.get("policyFingerprint"), "manifest references current policy fingerprint" if policy.get("semanticFingerprint") == manifest.get("policyFingerprint") else "manifest policy fingerprint mismatch")
        try:
            task = load_task(workspace, str(manifest.get("taskId", "")))
            add("task-revision", _task_revision(task) == manifest.get("taskRevision"), "task revision matches" if _task_revision(task) == manifest.get("taskRevision") else "task revision/scope drift detected")
            sources = _policy_sources(workspace, task)
            add("source-fingerprints", sources == manifest.get("sourceFingerprints"), "policy/schema input fingerprints match" if sources == manifest.get("sourceFingerprints") else "policy/schema/profile drift detected")
        except FastExecutionError as exc:
            add("task-revision", False, str(exc))

    state = _state(workspace)
    ledger = _run_ledger_entry(workspace, run_id)
    active_ok = state.get("currentRunId") == run_id or bool(ledger and ledger.get("status") == "COMPLETED")
    add("run-state-reference", active_ok, "run is active or completed in ledger" if active_ok else "stale run reference: run is neither active nor completed")

    result: Dict[str, Any] = {
        "schemaVersion": 1,
        "schemaId": SCHEMA_MANIFEST_VALIDATION,
        "runId": run_id,
        "taskId": (manifest or policy or {}).get("taskId", ""),
        "status": "PASS" if not errors else "FAIL",
        "checks": checks,
        "errors": errors,
        "checkedAtUtc": utc_now_iso(),
    }
    if write_result:
        write_json_atomic(os.path.join(run_path, "execution-contract-validation.json"), result)
    return result


# ---------------------------------------------------------------------------
# Performance trace
# ---------------------------------------------------------------------------

_FAKE_CLOCK_VALUES: Optional[List[float]] = None
_FAKE_CLOCK_INDEX = 0


def clock_ms() -> float:
    global _FAKE_CLOCK_VALUES, _FAKE_CLOCK_INDEX
    raw = os.environ.get("TEAMLOOP_FAKE_CLOCK_MS", "").strip()
    if raw:
        if _FAKE_CLOCK_VALUES is None:
            try:
                values = json.loads(raw)
                if not isinstance(values, list) or not values:
                    raise ValueError("expected non-empty JSON array")
                _FAKE_CLOCK_VALUES = [float(v) for v in values]
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                raise FastExecutionError(f"TEAMLOOP_FAKE_CLOCK_MS is invalid: {exc}") from exc
        idx = min(_FAKE_CLOCK_INDEX, len(_FAKE_CLOCK_VALUES) - 1)
        _FAKE_CLOCK_INDEX += 1
        return _FAKE_CLOCK_VALUES[idx]
    return time.perf_counter() * 1000.0


def _empty_trace(run_id: str) -> Dict[str, Any]:
    return {
        "schemaVersion": 1,
        "schemaId": SCHEMA_TRACE,
        "runId": run_id,
        "phases": [],
        "totalDurationMs": 0.0,
        "processInvocationCount": 0,
        "roleInvocationCount": 0,
        "filesReadOrValidated": [],
        "createdAtUtc": utc_now_iso(),
        "updatedAtUtc": utc_now_iso(),
    }


def _trace_path(workspace: str, run_id: str) -> str:
    if run_id:
        return os.path.join(run_dir(workspace, run_id), "performance-trace.json")
    return os.path.join(workspace, "state", "pending-performance-trace.json")


def record_trace_phase(
    workspace: str,
    run_id: str,
    phase: str,
    duration_ms: float,
    process_count: int = 0,
    role_count: int = 0,
    files: Optional[Iterable[str]] = None,
    decision: str = "EXECUTED",
    details: str = "",
) -> None:
    """Best-effort trace update. Failures never mutate semantic state or abort callers."""
    try:
        path = _trace_path(workspace, run_id)
        trace = read_json_optional(path) or _empty_trace(run_id)
        entry: Dict[str, Any] = {
            "phase": phase,
            "durationMs": round(max(0.0, float(duration_ms)), 3),
            "processInvocationCount": int(process_count),
            "roleInvocationCount": int(role_count),
            "decision": decision,
        }
        normalized_files = sorted(set(str(item).replace("\\", "/") for item in (files or []) if item))
        if normalized_files:
            entry["filesReadOrValidated"] = normalized_files
        if details:
            entry["details"] = details
        trace.setdefault("phases", []).append(entry)
        trace["totalDurationMs"] = round(sum(float(p.get("durationMs", 0)) for p in trace["phases"]), 3)
        trace["processInvocationCount"] = sum(int(p.get("processInvocationCount", 0)) for p in trace["phases"])
        trace["roleInvocationCount"] = sum(int(p.get("roleInvocationCount", 0)) for p in trace["phases"])
        trace["filesReadOrValidated"] = sorted({
            item for p in trace["phases"] for item in p.get("filesReadOrValidated", [])
        })
        trace["updatedAtUtc"] = utc_now_iso()
        write_json_atomic(path, trace)
    except Exception:
        return


def merge_pending_trace(workspace: str, run_id: str) -> None:
    pending = _trace_path(workspace, "")
    data = read_json_optional(pending)
    if data is None:
        return
    for phase in data.get("phases", []):
        record_trace_phase(
            workspace, run_id, str(phase.get("phase", "unknown")),
            float(phase.get("durationMs", 0)),
            int(phase.get("processInvocationCount", 0)),
            int(phase.get("roleInvocationCount", 0)),
            phase.get("filesReadOrValidated", []),
            str(phase.get("decision", "EXECUTED")),
            str(phase.get("details", "")),
        )
    try:
        os.remove(pending)
    except OSError:
        pass


def performance_report(workspace: str, run_id: str) -> Dict[str, Any]:
    path = _trace_path(workspace, run_id)
    trace = read_json_optional(path)
    if trace is None:
        raise FastExecutionError(f"performance trace not found for run '{run_id}'")
    by_phase: Dict[str, Dict[str, Any]] = {}
    for item in trace.get("phases", []):
        name = str(item.get("phase", "unknown"))
        row = by_phase.setdefault(name, {"phase": name, "invocations": 0, "durationMs": 0.0, "processInvocations": 0, "roleInvocations": 0})
        row["invocations"] += 1
        row["durationMs"] = round(row["durationMs"] + float(item.get("durationMs", 0)), 3)
        row["processInvocations"] += int(item.get("processInvocationCount", 0))
        row["roleInvocations"] += int(item.get("roleInvocationCount", 0))
    policy = read_json_optional(os.path.join(run_dir(workspace, run_id), "execution-policy.json")) or {}
    required = set(str(role) for role in policy.get("requiredRoles", []))
    # Final sentinel remains mandatory for every profile, so include it in the
    # minimum optimized lifecycle even when it is conditional before handoff.
    required.add("sentinel")
    baseline_roles = ["executor", "change-reviewer", "watchdog", "sentinel"]
    optimized_roles = sorted(required)
    return {
        "schemaVersion": 1,
        "runId": run_id,
        "totalDurationMs": trace.get("totalDurationMs", 0),
        "processInvocationCount": trace.get("processInvocationCount", 0),
        "roleInvocationCount": trace.get("roleInvocationCount", 0),
        "filesReadOrValidatedCount": len(trace.get("filesReadOrValidated", [])),
        "phases": sorted(by_phase.values(), key=lambda row: row["phase"]),
        "deterministicRoutingComparison": {
            "measurementType": "policy-level-role-invocation-count",
            "scenario": "one low-risk bounded task with no review/watchdog trigger and mandatory final sentinel",
            "beforePolicy": "legacy-unconditional",
            "beforeRoles": baseline_roles,
            "beforeRoleInvocationCount": len(baseline_roles),
            "afterProfile": policy.get("selectedProfile", "unknown"),
            "afterMinimumRoles": optimized_roles,
            "afterRoleInvocationCount": len(optimized_roles),
            "avoidedUnconditionalRoleInvocations": max(0, len(baseline_roles) - len(optimized_roles)),
            "wallClockClaim": False,
        },
    }


# ---------------------------------------------------------------------------
# Progress and no-progress
# ---------------------------------------------------------------------------


_SUPPRESSION_MARKERS = ("TODO", "FIXME", "HACK", "WARNING", "WARN:")


def _scoped_repo_fingerprint(
    repo: str,
    allowed_patterns: Sequence[str],
    ignore_suppression_lines: bool = False,
) -> str:
    patterns = []
    for pattern in allowed_patterns:
        normalized = pattern.replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        if not normalized.startswith(".teamloop"):
            patterns.append(pattern)
    if not patterns:
        return semantic_hash({"files": []})
    try:
        tracked = _git_output(repo, ["ls-files", "-z"]).split("\0")
    except FastExecutionError:
        tracked = []
    paths = {p.replace("\\", "/") for p in tracked if p}
    paths.update(git_changed_paths(repo))
    entries: List[Tuple[str, str]] = []
    for rel in sorted(paths):
        if not any(path_matches(rel, pattern) for pattern in patterns):
            continue
        abs_path = os.path.join(repo, rel)
        if os.path.isfile(abs_path):
            if ignore_suppression_lines:
                try:
                    if os.path.getsize(abs_path) <= 1_000_000:
                        text = pathlib.Path(abs_path).read_text(encoding="utf-8", errors="ignore")
                        normalized = "\n".join(
                            line for line in text.splitlines()
                            if not any(marker in line.upper() for marker in _SUPPRESSION_MARKERS)
                        )
                        entries.append((rel, sha256_text(normalized)))
                        continue
                except OSError:
                    pass
            entries.append((rel, file_sha256(abs_path)))
        else:
            entries.append((rel, "DELETED"))
    return semantic_hash({"files": entries})


def scoped_changed_paths(workspace: str, manifest: Dict[str, Any]) -> List[str]:
    repo = _repo_root(workspace)
    patterns = [
        str(pattern) for pattern in manifest.get("allowedRoots", [])
        if not _normalize_relative_path(str(pattern)).startswith(".teamloop")
    ]
    return sorted(
        path for path in git_changed_paths(repo)
        if any(path_matches(path, pattern) for pattern in patterns)
    )


def scope_violations(workspace: str, manifest: Dict[str, Any]) -> List[Dict[str, str]]:
    """Validate current Git changes against the immutable manifest scope."""
    repo = _repo_root(workspace)
    allowed = [str(pattern) for pattern in manifest.get("allowedRoots", [])]
    forbidden = [str(pattern) for pattern in manifest.get("forbiddenRoots", [])]
    violations: List[Dict[str, str]] = []
    for path in git_changed_paths(repo):
        forbidden_match = next((p for p in forbidden if path_matches(path, p)), "")
        if forbidden_match:
            violations.append({"file": path, "reason": f"forbidden pattern: {forbidden_match}"})
            continue
        if not any(path_matches(path, pattern) for pattern in allowed):
            violations.append({"file": path, "reason": "outside immutable manifest allowed roots"})
    return violations


def has_scoped_repository_change(workspace: str, manifest: Dict[str, Any]) -> bool:
    repo = _repo_root(workspace)
    baseline_head = str(manifest.get("repositoryHead", ""))
    if baseline_head and git_head(repo) != baseline_head:
        return True
    return bool(scoped_changed_paths(workspace, manifest))


def _latest_artifact(workspace: str, filename: str, run_id: str = "") -> Optional[Dict[str, Any]]:
    if run_id:
        data = read_json_optional(os.path.join(run_dir(workspace, run_id), filename))
        if data is not None:
            return data
    runs = os.path.join(workspace, "runs")
    if not os.path.isdir(runs):
        return None
    for name in reversed(sorted(os.listdir(runs))):
        data = read_json_optional(os.path.join(runs, name, filename))
        if data is not None:
            return data
    return None


def _normalized_artifact_fingerprint(value: Optional[Dict[str, Any]]) -> str:
    return semantic_hash(value or {})


def _validation_fingerprint(workspace: str, core_script: str) -> Tuple[str, int]:
    try:
        proc = subprocess.run(
            [os.environ.get("PYTHON", os.sys.executable), core_script, "validate-state", "--workspace", workspace],
            capture_output=True, text=True, timeout=60, cwd=_repo_root(workspace),
        )
        normalized = "\n".join(line.strip() for line in (proc.stdout + "\n" + proc.stderr).splitlines() if line.strip())
        return semantic_hash({"exitCode": proc.returncode, "output": normalized}), 1
    except (OSError, subprocess.SubprocessError) as exc:
        return semantic_hash({"error": str(exc)}), 1


def _scoped_quality_signals(repo: str, allowed_patterns: Sequence[str], findings: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """Return conservative anti-suppression signals inside the bounded scope.

    A reduction is not accepted as progress on its own.  It must be accompanied
    by changed executable evidence (gate/review/validation/task state).
    """
    patterns = []
    for pattern in allowed_patterns:
        normalized = pattern.replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        if not normalized.startswith(".teamloop"):
            patterns.append(pattern)
    if not patterns:
        return {"todoMarkers": 0, "warningMarkers": 0, "openFindingCount": 0}
    try:
        tracked = _git_output(repo, ["ls-files", "-z"]).split("\0")
    except FastExecutionError:
        tracked = []
    paths = {p.replace("\\", "/") for p in tracked if p}
    paths.update(git_changed_paths(repo))
    todo_markers = 0
    warning_markers = 0
    textual_suffixes = {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".cs", ".java", ".kt", ".go",
        ".rs", ".rb", ".php", ".sh", ".ps1", ".md", ".txt", ".yml", ".yaml",
        ".json", ".xml", ".html", ".css", ".scss", ".sql",
    }
    for rel in sorted(paths):
        if not any(path_matches(rel, pattern) for pattern in patterns):
            continue
        abs_path = os.path.join(repo, rel)
        if not os.path.isfile(abs_path) or pathlib.Path(rel).suffix.lower() not in textual_suffixes:
            continue
        try:
            if os.path.getsize(abs_path) > 1_000_000:
                continue
            text = pathlib.Path(abs_path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        upper = text.upper()
        todo_markers += upper.count("TODO") + upper.count("FIXME") + upper.count("HACK")
        warning_markers += upper.count("WARNING") + upper.count("WARN:")
    open_findings = 0
    if findings:
        for item in findings.get("findings", []):
            if str(item.get("status", "OPEN")).upper() not in ("RESOLVED", "CLOSED", "VERIFIED"):
                open_findings += 1
    return {
        "todoMarkers": todo_markers,
        "warningMarkers": warning_markers,
        "openFindingCount": open_findings,
    }


def _behavior_evidence_fingerprint(components: Dict[str, str]) -> str:
    return semantic_hash({
        key: components.get(key, "")
        for key in ("taskStatus", "gateFailures", "evidence", "validationFailures", "openBlockers")
    })


def _suppression_only(previous: Dict[str, Any], current: Dict[str, Any]) -> bool:
    before = previous.get("qualitySignals", {})
    after = current.get("qualitySignals", {})
    reduced = any(
        int(after.get(key, 0)) < int(before.get(key, 0))
        for key in ("todoMarkers", "warningMarkers", "openFindingCount")
    )
    return bool(
        reduced
        and previous.get("behaviorEvidenceFingerprint") == current.get("behaviorEvidenceFingerprint")
        and previous.get("components", {}).get("repositoryScopeWithoutSuppressionMarkers")
        == current.get("components", {}).get("repositoryScopeWithoutSuppressionMarkers")
    )


def build_progress_snapshot(workspace: str, run_id: str, core_script: str) -> Tuple[Dict[str, Any], int]:
    manifest = read_json_optional(os.path.join(run_dir(workspace, run_id), "execution-manifest.json"))
    if manifest is None or not verify_integrity(manifest):
        raise FastExecutionError("valid execution manifest is required before recording progress")
    task = load_task(workspace, str(manifest.get("taskId", "")))
    repo = _repo_root(workspace)
    blockers = [b for b in read_jsonl(os.path.join(workspace, "state", "blockers.jsonl")) if not b.get("resolvedAtUtc")]
    open_work = sorted(
        str(t.get("taskId")) + ":" + str(t.get("status"))
        for t in _backlog(workspace)
        if t.get("status") not in ("DONE", "CANCELLED")
    )
    validation_fp, process_count = _validation_fingerprint(workspace, core_script)
    findings_artifact = _latest_artifact(workspace, "sentinel-inspection.json", run_id)
    components = {
        "repositoryScope": _scoped_repo_fingerprint(repo, manifest.get("allowedRoots", [])),
        "repositoryScopeWithoutSuppressionMarkers": _scoped_repo_fingerprint(
            repo,
            manifest.get("allowedRoots", []),
            ignore_suppression_lines=True,
        ),
        "taskRevision": str(manifest.get("taskRevision", "")),
        "taskStatus": semantic_hash({"taskId": task.get("taskId"), "status": task.get("status")}),
        "gateFailures": _normalized_artifact_fingerprint(_latest_artifact(workspace, "gate-result.json", run_id)),
        "openBlockers": semantic_hash(strip_volatile(blockers)),
        "findings": _normalized_artifact_fingerprint(findings_artifact),
        "evidence": _normalized_artifact_fingerprint(_latest_artifact(workspace, "review-evidence.json", run_id)),
        "validationFailures": validation_fp,
        "unresolvedExecutableWork": semantic_hash(open_work),
        "continuationDecision": _normalized_artifact_fingerprint(read_json_optional(os.path.join(workspace, "state", "continuation-decision.json"))),
    }
    signature = semantic_hash(components)
    quality_signals = _scoped_quality_signals(repo, manifest.get("allowedRoots", []), findings_artifact)
    behavior_evidence = _behavior_evidence_fingerprint(components)
    return {
        "schemaVersion": 1,
        "schemaId": SCHEMA_PROGRESS,
        "runId": run_id,
        "taskId": str(manifest.get("taskId", "")),
        "signature": signature,
        "rawSignature": signature,
        "components": components,
        "qualitySignals": quality_signals,
        "behaviorEvidenceFingerprint": behavior_evidence,
        "progressClassification": "INITIAL_OR_MATERIAL",
        "unresolvedExecutableWork": open_work,
        "createdAtUtc": utc_now_iso(),
    }, process_count


def record_progress(workspace: str, run_id: str, core_script: str) -> Tuple[Dict[str, Any], Dict[str, Any], int]:
    history_path = os.path.join(run_dir(workspace, run_id), "progress-history.jsonl")
    # Parse before mutation; malformed history is a hard failure and is never ignored.
    history = read_jsonl(history_path)
    snapshot, process_count = build_progress_snapshot(workspace, run_id, core_script)
    if history and _suppression_only(history[-1], snapshot):
        snapshot["signature"] = str(history[-1].get("signature", snapshot["signature"]))
        snapshot["progressClassification"] = "SUPPRESSION_ONLY_NOT_PROGRESS"
    elif history and history[-1].get("signature") == snapshot.get("signature"):
        snapshot["progressClassification"] = "SEMANTICALLY_UNCHANGED"
    elif history:
        snapshot["progressClassification"] = "MATERIAL_CHANGE"
    append_jsonl(history_path, snapshot)
    history.append(snapshot)

    policy = read_json_optional(os.path.join(run_dir(workspace, run_id), "execution-policy.json"))
    threshold = int((policy or {}).get("noProgressThreshold", 2))
    streak = 0
    for item in reversed(history):
        if item.get("signature") == snapshot["signature"]:
            streak += 1
        else:
            break
    detected = streak >= threshold
    previous_signature = history[-2].get("signature") if len(history) >= 2 else ""
    if detected:
        status = "NO_PROGRESS_DETECTED"
        next_action = "RUN_WATCHDOG"
        prefix = "suppression-only change did not count as progress; " if snapshot.get("progressClassification") == "SUPPRESSION_ONLY_NOT_PROGRESS" else ""
        reason = prefix + f"{streak} consecutive semantically identical progress snapshots reached threshold {threshold}"
    elif previous_signature and previous_signature != snapshot["signature"]:
        status = "PROGRESS_OBSERVED"
        next_action = ""
        reason = "material progress signature changed"
    else:
        status = "INSUFFICIENT_HISTORY"
        next_action = ""
        reason = f"identical snapshot streak {streak} is below threshold {threshold}"
    result = {
        "schemaVersion": 1,
        "schemaId": SCHEMA_NO_PROGRESS,
        "runId": run_id,
        "taskId": snapshot["taskId"],
        "status": status,
        "signature": snapshot["signature"],
        "identicalSnapshotStreak": streak,
        "threshold": threshold,
        "nextAction": next_action,
        "reason": reason,
        "progressClassification": snapshot.get("progressClassification", "INITIAL_OR_MATERIAL"),
        "createdAtUtc": utc_now_iso(),
    }
    write_json_atomic(os.path.join(run_dir(workspace, run_id), "no-progress-result.json"), result)
    return snapshot, result, process_count


def active_no_progress_route(workspace: str) -> Optional[Dict[str, Any]]:
    state = _state(workspace)
    run_id = resolve_run_id(workspace, task_id=str(state.get("currentTaskId", "")))
    result = _active_no_progress(workspace, run_id)
    if result and result.get("status") == "NO_PROGRESS_DETECTED":
        return {
            "nextAction": str(result.get("nextAction") or "RUN_WATCHDOG"),
            "phase": state.get("currentPhase", "EXECUTING_TASK"),
            "taskId": state.get("currentTaskId", "") or result.get("taskId", ""),
            "runId": run_id,
            "humanRequired": False,
            "reason": result.get("reason", "no progress detected"),
            "noProgressDetected": True,
        }
    return None


def acknowledge_no_progress_strategy(workspace: str, run_id: str) -> None:
    path = os.path.join(run_dir(workspace, run_id), "no-progress-result.json")
    result = read_json_optional(path)
    if not result or result.get("status") != "NO_PROGRESS_DETECTED":
        return
    result["status"] = "STRATEGY_CHANGE_REQUIRED"
    result["nextAction"] = "RETRY_EXECUTOR"
    result["reason"] = "watchdog completed; a materially different bounded strategy is required before retry"
    result["handledAtUtc"] = utc_now_iso()
    write_json_atomic(path, result)


def route_role(workspace: str, run_id: str, event: str, severity: str = "") -> Dict[str, Any]:
    policy = read_json_optional(os.path.join(run_dir(workspace, run_id), "execution-policy.json"))
    if policy is None or not verify_integrity(policy):
        raise FastExecutionError("valid execution policy is required for role routing")
    profile = str(policy.get("selectedProfile"))
    no_progress = _active_no_progress(workspace, run_id)
    detected = bool(no_progress and no_progress.get("status") == "NO_PROGRESS_DETECTED")
    severity_upper = severity.upper()

    if detected and event == "watchdog-complete":
        action, role, reason = "RETRY_EXECUTOR", "executor", "watchdog requires a materially different bounded strategy"
    elif detected:
        action, role, reason = "RUN_WATCHDOG", "watchdog", "NO_PROGRESS_DETECTED trigger"
    elif event == "implementation-complete":
        if profile in ("standard", "audit") or severity_upper in ("HIGH", "CRITICAL"):
            action, role, reason = "RUN_CHANGE_REVIEWER", "change-reviewer", "profile or severity requires review"
        else:
            action, role, reason = "RUN_GATEKEEPER", "gatekeeper", "fast profile has no reviewer trigger"
    elif event == "review-complete":
        if profile == "audit":
            action, role, reason = "RUN_WATCHDOG", "watchdog", "audit profile requires watchdog"
        else:
            action, role, reason = "RUN_GATEKEEPER", "gatekeeper", "watchdog trigger absent"
    elif event == "watchdog-complete":
        action, role = "RUN_GATEKEEPER", "gatekeeper"
        reason = (
            "audit watchdog completed; project gates are required before final sentinel"
            if profile == "audit"
            else "watchdog completed; project gates are next"
        )
    elif event in ("final-handoff", "pre-final"):
        action, role, reason = "RUN_SENTINEL", "sentinel", "final sentinel invariant"
    elif event == "sentinel-complete":
        action, role, reason = "RUN_FINAL_GATE", "final-gate", "final gate invariant"
    elif event == "gate-failed":
        action, role, reason = "RUN_WATCHDOG" if profile == "audit" else "RETRY_EXECUTOR", "watchdog" if profile == "audit" else "executor", "gate failure routing"
    else:
        raise FastExecutionError("event must be one of: implementation-complete, review-complete, watchdog-complete, gate-failed, pre-final, final-handoff, sentinel-complete")
    result: Dict[str, Any] = {
        "schemaVersion": 1,
        "schemaId": SCHEMA_ROLE_ROUTING,
        "runId": run_id,
        "taskId": policy.get("taskId", ""),
        "profile": profile,
        "event": event,
        "nextAction": action,
        "role": role,
        "reason": reason,
        "required": role in policy.get("requiredRoles", []) or action in ("RUN_SENTINEL", "RUN_FINAL_GATE"),
        "finalGateCannotBeBypassed": True,
        "finalSentinelCannotBeBypassed": True,
        "createdAtUtc": utc_now_iso(),
    }
    fingerprint = semantic_hash(result)
    result["semanticFingerprint"] = fingerprint
    result["integrity"] = {"algorithm": "sha256", "semanticSha256": fingerprint}
    return result
