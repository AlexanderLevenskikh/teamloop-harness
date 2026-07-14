#!/usr/bin/env python3
"""Deterministic Quality/Value Boundary Manager for YourAITeam.

The module deliberately contains no LLM calls. It measures primary artifacts,
validates a closed decision enum, maintains tamper-evident ledgers, and issues
acceptance receipts that are revalidated against current inputs before
advancement.
"""
from __future__ import annotations

import datetime as _dt
import fnmatch
import hashlib
import html as _html
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

BOUNDARY_SCHEMA_VERSION = 1
BOUNDARY_IMPLEMENTATION_VERSION = "0.5.0-alpha.1"
DECISIONS = (
    "ACCEPT_BOUNDARY",
    "ACCEPT_WITH_RECORDED_SOFT_DEBT",
    "IMPROVE_CURRENT_BOUNDARY",
    "SPLIT_CURRENT_BOUNDARY",
    "STOP_BUDGET_EXHAUSTED",
    "REQUEST_HUMAN_DECISION",
)
PROFILE_BUDGETS = {"fast": 2, "standard": 4, "audit": 6}
SEVERITY_WEIGHT = {"info": 0.25, "low": 0.75, "medium": 2.0, "high": 5.0, "critical": 10.0}
PASS_VALUES = {"PASS", "PASSED", "SUCCESS", "OK", "GREEN"}
PLACEHOLDER_PATTERNS = (
    r"\bTODO\b",
    r"\bFIXME\b",
    r"\bNOT_IMPLEMENTED\b",
    r"\bPLACEHOLDER\b",
    r"throw\s+new\s+NotImplemented",
    r"raise\s+NotImplemented",
)


class BoundaryError(RuntimeError):
    """Fail-closed boundary validation error."""


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise BoundaryError(f"{path}: expected JSON object")
    return value


def save_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    result: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line_no, line in enumerate(f, 1):
            text = line.strip()
            if not text:
                continue
            try:
                item = json.loads(text)
            except json.JSONDecodeError as exc:
                raise BoundaryError(f"{path}:{line_no}: malformed JSONL: {exc}") from exc
            if not isinstance(item, dict):
                raise BoundaryError(f"{path}:{line_no}: expected JSON object")
            result.append(item)
    return result


def project_root_for(workspace: str | Path, explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    return Path(workspace).resolve().parent


def boundary_dir(workspace: str | Path, boundary_id: str) -> Path:
    if not boundary_id or not re.fullmatch(r"[A-Za-z0-9._-]{3,120}", boundary_id):
        raise BoundaryError("boundaryId must match [A-Za-z0-9._-]{3,120}")
    return Path(workspace).resolve() / "boundaries" / boundary_id


def _relative_or_absolute(project_root: Path, raw: str) -> Path:
    path = Path(raw)
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def _require_project_path(project_root: Path, raw: str, label: str) -> Path:
    if not raw or "\x00" in raw:
        raise BoundaryError(f"{label} path is empty or invalid")
    path = _relative_or_absolute(project_root, raw)
    try:
        path.relative_to(project_root)
    except ValueError as exc:
        raise BoundaryError(f"{label} path escapes project root: {raw}") from exc
    return path


def _safe_rel(project_root: Path, path: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return str(path)


def _without_fingerprint(value: Dict[str, Any], field: str) -> Dict[str, Any]:
    return {k: v for k, v in value.items() if k != field}


def _policy_path(workspace: str | Path) -> Path:
    return Path(workspace).resolve() / "policies" / "boundary-policy.json"


def default_policy() -> Dict[str, Any]:
    return {
        "schemaVersion": 1,
        "enabled": True,
        "requiredForAdvancementWhenContractExists": True,
        "trustedWriterCommand": "teamloop-core",
        "managerMayWriteReceipts": False,
        "requireManagerRoleReceipt": True,
        "historyMode": "append-only-hash-chain",
        "profiles": {
            "fast": {"maxImprovementCycles": 2, "maxConsecutiveNoProgress": 1},
            "standard": {"maxImprovementCycles": 4, "maxConsecutiveNoProgress": 2},
            "audit": {"maxImprovementCycles": 6, "maxConsecutiveNoProgress": 2},
        },
        "hardInvariantTypes": [
            "MISSING_REQUIRED_DELIVERABLE",
            "EMPTY_REQUIRED_DELIVERABLE",
            "PLACEHOLDER_IMPLEMENTATION",
            "REQUIRED_VALIDATION_FAILED",
            "REQUIRED_VALIDATION_MISSING",
            "CRITICAL_FINDING",
            "HIGH_FINDING",
            "SCOPE_OR_PERMISSION_VIOLATION",
            "STALE_OR_CONTRADICTORY_EVIDENCE",
            "ACTIVE_WORK_REPRESENTED_COMPLETE",
            "MANIPULATED_EVIDENCE",
        ],
        "placeholderPatterns": list(PLACEHOLDER_PATTERNS),
        "severityWeights": dict(SEVERITY_WEIGHT),
        "minimumProgressScoreDelta": 0.000001,
    }


def load_policy(workspace: str | Path) -> Dict[str, Any]:
    path = _policy_path(workspace)
    policy = load_json(path) if path.exists() else default_policy()
    if policy.get("schemaVersion") != 1:
        raise BoundaryError("unsupported boundary policy schemaVersion")
    if not isinstance(policy.get("profiles"), dict):
        raise BoundaryError("boundary policy profiles must be an object")
    trust_contract = {
        "trustedWriterCommand": "teamloop-core",
        "managerMayWriteReceipts": False,
        "requireManagerRoleReceipt": True,
        "historyMode": "append-only-hash-chain",
    }
    for field, expected in trust_contract.items():
        if policy.get(field) != expected:
            raise BoundaryError(f"boundary policy weakens trusted-writer contract: {field}")
    return policy


def policy_fingerprint(workspace: str | Path) -> str:
    return fingerprint(load_policy(workspace))


def _state_default(boundary_id: str, contract: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Any]:
    profile = contract.get("profile", "standard")
    profile_policy = policy.get("profiles", {}).get(profile, {})
    max_cycles = int(profile_policy.get("maxImprovementCycles", PROFILE_BUDGETS.get(profile, 4)))
    max_no_progress = int(profile_policy.get("maxConsecutiveNoProgress", 2))
    return {
        "schemaVersion": 1,
        "boundaryId": boundary_id,
        "mode": "NEEDS_MEASUREMENT",
        "profile": profile,
        "maxImprovementCycles": max_cycles,
        "usedImprovementCycles": 0,
        "remainingImprovementCycles": max_cycles,
        "maxConsecutiveNoProgress": max_no_progress,
        "noProgressStreak": 0,
        "currentPacketFingerprint": "",
        "currentDecisionFingerprint": "",
        "selectedImprovementPatternId": "",
        "nextPermittedAction": "MEASURE",
        "decisionHistoryHead": "",
        "decisionHistoryCount": 0,
        "improvementHistoryHead": "",
        "improvementHistoryCount": 0,
        "acceptanceReceiptFingerprint": "",
        "createdAtUtc": utc_now(),
        "updatedAtUtc": utc_now(),
    }


def _state_path(workspace: str | Path, boundary_id: str) -> Path:
    return boundary_dir(workspace, boundary_id) / "boundary-state.json"


def load_state(workspace: str | Path, boundary_id: str) -> Dict[str, Any]:
    path = _state_path(workspace, boundary_id)
    if not path.exists():
        raise BoundaryError(f"boundary state missing: {path}")
    return load_json(path)


def save_state(workspace: str | Path, boundary_id: str, state: Dict[str, Any]) -> None:
    state = dict(state)
    state["updatedAtUtc"] = utc_now()
    save_json(_state_path(workspace, boundary_id), state)


def contract_path(workspace: str | Path, boundary_id: str) -> Path:
    return boundary_dir(workspace, boundary_id) / "boundary-contract.json"


def packet_path(workspace: str | Path, boundary_id: str) -> Path:
    return boundary_dir(workspace, boundary_id) / "boundary-packet.json"


def receipt_path(workspace: str | Path, boundary_id: str) -> Path:
    return boundary_dir(workspace, boundary_id) / "acceptance-receipt.json"


def create_contract(
    workspace: str | Path,
    contract: Dict[str, Any],
    *,
    project_root: str | Path | None = None,
) -> Dict[str, Any]:
    workspace_path = Path(workspace).resolve()
    root = project_root_for(workspace_path, project_root)
    policy = load_policy(workspace_path)
    boundary_id = str(contract.get("boundaryId", "")).strip()
    target = boundary_dir(workspace_path, boundary_id)
    target.mkdir(parents=True, exist_ok=True)
    if contract_path(workspace_path, boundary_id).exists():
        raise BoundaryError(f"boundary contract already exists: {boundary_id}")

    normalized = dict(contract)
    normalized.setdefault("schemaVersion", 1)
    normalized.setdefault("profile", "standard")
    normalized.setdefault("taskId", "")
    normalized.setdefault("runId", "")
    normalized.setdefault("adapterId", "generic-software-task")
    normalized.setdefault("expectedDeliverables", [])
    normalized.setdefault("validationEvidence", [])
    normalized.setdefault("findingSources", [])
    normalized.setdefault("improvementCandidates", [])
    normalized.setdefault("predecessorBoundaryId", "")
    normalized.setdefault("createdAtUtc", utc_now())
    normalized["projectRootIdentity"] = fingerprint(str(root))
    normalized["policyFingerprint"] = fingerprint(policy)
    normalized["contractFingerprint"] = fingerprint(_without_fingerprint(normalized, "contractFingerprint"))

    if normalized["schemaVersion"] != 1:
        raise BoundaryError("unsupported boundary contract schemaVersion")
    if normalized["profile"] not in policy.get("profiles", {}):
        raise BoundaryError(f"unknown boundary profile: {normalized['profile']}")
    if not isinstance(normalized["expectedDeliverables"], list):
        raise BoundaryError("expectedDeliverables must be an array")
    if not normalized["expectedDeliverables"] and not normalized["validationEvidence"]:
        raise BoundaryError("boundary contract requires deliverables or validation evidence")
    for collection_name in ("expectedDeliverables", "validationEvidence", "findingSources"):
        for index, spec in enumerate(normalized.get(collection_name, []), 1):
            if not isinstance(spec, dict):
                raise BoundaryError(f"{collection_name}[{index}] must be an object")
            _require_project_path(root, str(spec.get("path", "")), f"{collection_name}[{index}]")

    save_json(contract_path(workspace_path, boundary_id), normalized)
    save_json(_state_path(workspace_path, boundary_id), _state_default(boundary_id, normalized, policy))
    return normalized


def load_contract(workspace: str | Path, boundary_id: str) -> Dict[str, Any]:
    contract = load_json(contract_path(workspace, boundary_id))
    expected = fingerprint(_without_fingerprint(contract, "contractFingerprint"))
    if contract.get("contractFingerprint") != expected:
        raise BoundaryError("boundary contract fingerprint mismatch")
    if contract.get("policyFingerprint") != policy_fingerprint(workspace):
        raise BoundaryError("boundary contract policy fingerprint is stale")
    return contract


def list_boundaries(workspace: str | Path) -> List[str]:
    root = Path(workspace).resolve() / "boundaries"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "boundary-contract.json").exists())


def find_applicable_boundary(workspace: str | Path, run_id: str = "", task_id: str = "") -> Optional[str]:
    candidates: List[Tuple[str, str]] = []
    for boundary_id in list_boundaries(workspace):
        try:
            contract = load_contract(workspace, boundary_id)
        except BoundaryError:
            continue
        if run_id and contract.get("runId") == run_id:
            return boundary_id
        if task_id and contract.get("taskId") == task_id:
            candidates.append((contract.get("createdAtUtc", ""), boundary_id))
    return sorted(candidates)[-1][1] if candidates else None


def _dot_get(value: Any, path: str) -> Any:
    current = value
    for part in path.split(".") if path else []:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _artifact_measurement(root: Path, spec: Dict[str, Any], placeholder_patterns: Sequence[str]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    raw_path = str(spec.get("path", ""))
    artifact_id = str(spec.get("id") or raw_path or "artifact")
    required = bool(spec.get("required", True))
    path = _require_project_path(root, raw_path, f"deliverable {artifact_id}")
    minimum = int(spec.get("minBytes", 1))
    status = "MISSING"
    size = 0
    sha = ""
    placeholder_hits: List[str] = []
    kind = spec.get("kind", "file")
    if path.exists():
        if kind == "directory" or path.is_dir():
            children = sorted(p for p in path.rglob("*") if p.is_file())
            size = sum(p.stat().st_size for p in children)
            sha = fingerprint([(_safe_rel(root, p), file_sha256(p)) for p in children])
            status = "READY" if children and size >= minimum else "EMPTY"
        else:
            size = path.stat().st_size
            sha = file_sha256(path)
            if size < minimum:
                status = "EMPTY"
            else:
                status = "READY"
                if spec.get("checkPlaceholders", True):
                    try:
                        text = path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        text = ""
                    patterns = list(spec.get("placeholderPatterns", [])) + list(placeholder_patterns)
                    for pattern in patterns:
                        try:
                            if re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE):
                                placeholder_hits.append(pattern)
                        except re.error:
                            if pattern.lower() in text.lower():
                                placeholder_hits.append(pattern)
                    if placeholder_hits:
                        status = "PLACEHOLDER"
    hard: List[Dict[str, Any]] = []
    if required and status == "MISSING":
        hard.append(_issue("MISSING_REQUIRED_DELIVERABLE", "high", artifact_id, f"Required deliverable is missing: {raw_path}", [raw_path]))
    elif required and status == "EMPTY":
        hard.append(_issue("EMPTY_REQUIRED_DELIVERABLE", "high", artifact_id, f"Required deliverable is empty: {raw_path}", [raw_path]))
    elif required and status == "PLACEHOLDER":
        hard.append(_issue("PLACEHOLDER_IMPLEMENTATION", "high", artifact_id, f"Required deliverable contains placeholder markers: {raw_path}", [raw_path]))
    return {
        "id": artifact_id,
        "path": raw_path,
        "required": required,
        "status": status,
        "sizeBytes": size,
        "sha256": sha,
        "placeholderHits": placeholder_hits,
    }, hard


def _validation_measurement(root: Path, spec: Dict[str, Any], primary_artifact_fingerprint: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    validation_id = str(spec.get("id") or spec.get("path") or "validation")
    raw_path = str(spec.get("path", ""))
    path = _require_project_path(root, raw_path, f"validation {validation_id}")
    required = bool(spec.get("required", True))
    status_field = str(spec.get("statusField", "status"))
    pass_values = {str(x).upper() for x in spec.get("passValues", list(PASS_VALUES))}
    status = "MISSING"
    sha = ""
    parse_error = ""
    expected_input_fp = str(spec.get("inputFingerprint", ""))
    input_field = str(spec.get("inputFingerprintField", ""))
    if spec.get("bindToPrimaryArtifacts", False):
        expected_input_fp = primary_artifact_fingerprint
        input_field = input_field or "inputFingerprint"
    if path.exists() and path.is_file():
        sha = file_sha256(path)
        try:
            data = load_json(path)
            raw = _dot_get(data, status_field)
            status = str(raw).upper() if raw is not None else "UNKNOWN"
            if expected_input_fp and input_field:
                actual = _dot_get(data, input_field)
                if actual != expected_input_fp:
                    status = "STALE"
        except (BoundaryError, OSError, json.JSONDecodeError) as exc:
            status = "INVALID"
            parse_error = str(exc)
    hard: List[Dict[str, Any]] = []
    if required and status == "MISSING":
        hard.append(_issue("REQUIRED_VALIDATION_MISSING", "high", validation_id, f"Required validation evidence is missing: {raw_path}", [raw_path]))
    elif required and status not in pass_values:
        issue_type = "STALE_OR_CONTRADICTORY_EVIDENCE" if status == "STALE" else "REQUIRED_VALIDATION_FAILED"
        hard.append(_issue(issue_type, "high", validation_id, f"Required validation is not passing: {validation_id}={status}", [raw_path]))
    return {
        "id": validation_id,
        "path": raw_path,
        "required": required,
        "status": status,
        "passValues": sorted(pass_values),
        "sha256": sha,
        "parseError": parse_error,
        "inputFingerprintField": input_field,
        "expectedInputFingerprint": expected_input_fp,
    }, hard


def _issue(issue_type: str, severity: str, issue_id: str, summary: str, paths: Sequence[str] | None = None, **extra: Any) -> Dict[str, Any]:
    result = {
        "issueId": str(issue_id),
        "type": issue_type,
        "severity": severity.lower(),
        "summary": summary,
        "affectedPaths": list(paths or []),
        "rootPatternId": str(extra.pop("rootPatternId", issue_type.lower())),
        "blocking": bool(extra.pop("blocking", True)),
    }
    result.update(extra)
    return result


def _load_findings(root: Path, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_path = str(spec.get("path", ""))
    path = _require_project_path(root, raw_path, f"finding source {spec.get('id', raw_path)}")
    if not path.exists():
        if spec.get("required") or spec.get("authority", "authoritative") == "authoritative":
            return [_issue("STALE_OR_CONTRADICTORY_EVIDENCE", "high", spec.get("id", raw_path), f"Required finding source missing: {raw_path}", [raw_path])]
        return []
    try:
        if path.suffix.lower() == ".jsonl":
            items = load_jsonl(path)
        else:
            data = load_json(path)
            source_field = str(spec.get("itemsField", "findings"))
            raw_items = _dot_get(data, source_field)
            items = raw_items if isinstance(raw_items, list) else [data]
    except (BoundaryError, OSError, json.JSONDecodeError) as exc:
        return [_issue("MANIPULATED_EVIDENCE", "high", spec.get("id", raw_path), f"Finding source is malformed: {exc}", [raw_path])]
    result: List[Dict[str, Any]] = []
    authority = spec.get("authority", "authoritative")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity", "low")).lower()
        blocking = bool(item.get("blocking", severity in ("high", "critical"))) and authority == "authoritative"
        issue_type = str(item.get("type") or ("CRITICAL_FINDING" if severity == "critical" else "HIGH_FINDING" if severity == "high" else "SOFT_DEBT"))
        result.append(_issue(
            issue_type,
            severity,
            item.get("issueId") or f"{spec.get('id', 'finding')}-{index+1}",
            str(item.get("summary") or item.get("title") or issue_type),
            item.get("affectedPaths") or item.get("files") or [],
            rootPatternId=item.get("rootPatternId") or issue_type.lower(),
            blocking=blocking,
            affectedItems=item.get("affectedItems", []),
            cascadeCount=int(item.get("cascadeCount", 0)),
            confidence=float(item.get("confidence", 0.7)),
            estimatedCost=float(item.get("estimatedCost", 1.0)),
            repetition=float(item.get("repetition", 1.0)),
            sourcePath=raw_path,
            authority=authority,
        ))
    return result


def _root_patterns(issues: Sequence[Dict[str, Any]], severity_weights: Dict[str, float]) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for issue in issues:
        root_id = str(issue.get("rootPatternId") or issue.get("type") or "unknown")
        group = groups.setdefault(root_id, {
            "rootPatternId": root_id,
            "occurrenceCount": 0,
            "affectedItems": set(),
            "affectedPaths": set(),
            "severity": "info",
            "confidence": 0.0,
            "estimatedCost": 0.0,
            "cascadeCount": 0,
            "repetition": 1.0,
            "blocking": False,
        })
        group["occurrenceCount"] += 1
        group["affectedItems"].update(str(x) for x in issue.get("affectedItems", []))
        group["affectedPaths"].update(str(x) for x in issue.get("affectedPaths", []))
        if severity_weights.get(issue.get("severity", "info"), 0) > severity_weights.get(group["severity"], 0):
            group["severity"] = issue.get("severity", "info")
        group["confidence"] = max(group["confidence"], float(issue.get("confidence", 0.7)))
        group["estimatedCost"] = max(group["estimatedCost"], float(issue.get("estimatedCost", 1.0)))
        group["cascadeCount"] += int(issue.get("cascadeCount", 0))
        group["repetition"] = max(group["repetition"], float(issue.get("repetition", 1.0)))
        group["blocking"] = group["blocking"] or bool(issue.get("blocking"))
    result: List[Dict[str, Any]] = []
    for group in groups.values():
        affected = max(1, len(group["affectedItems"]) + len(group["affectedPaths"]) + group["occurrenceCount"])
        cost = max(0.1, group["estimatedCost"])
        payoff = affected * group["repetition"] * severity_weights.get(group["severity"], 1.0) * max(0.05, group["confidence"]) / cost
        result.append({
            **{k: v for k, v in group.items() if k not in ("affectedItems", "affectedPaths")},
            "affectedItems": sorted(group["affectedItems"]),
            "affectedPaths": sorted(group["affectedPaths"]),
            "expectedPayoff": round(payoff, 6),
            "payoffFactors": {
                "affectedItems": affected,
                "repetitionOrReuse": group["repetition"],
                "blockingSeverity": severity_weights.get(group["severity"], 1.0),
                "confidenceOfSafeFix": group["confidence"],
                "estimatedCost": cost,
            },
            "recommendedBoundedAction": f"Address root pattern {group['rootPatternId']} once and remeasure",
        })
    return sorted(result, key=lambda x: (-x["expectedPayoff"], x["rootPatternId"]))


def _candidate_list(contract: Dict[str, Any], roots: Sequence[Dict[str, Any]], severity_weights: Dict[str, float]) -> List[Dict[str, Any]]:
    by_root = {r["rootPatternId"]: r for r in roots}
    candidates: List[Dict[str, Any]] = []
    for index, item in enumerate(contract.get("improvementCandidates", [])):
        if not isinstance(item, dict):
            continue
        root_id = str(item.get("rootPatternId", ""))
        root = by_root.get(root_id, {})
        affected = float(item.get("affectedItems", root.get("payoffFactors", {}).get("affectedItems", 1)))
        repetition = float(item.get("repetitionOrReuse", root.get("repetition", 1)))
        severity = str(item.get("severity", root.get("severity", "medium"))).lower()
        confidence = float(item.get("confidenceOfSafeFix", root.get("confidence", 0.7)))
        cost = max(0.1, float(item.get("estimatedCost", root.get("estimatedCost", 1))))
        payoff = affected * repetition * severity_weights.get(severity, 1.0) * confidence / cost
        candidates.append({
            "candidateId": str(item.get("candidateId") or f"candidate-{index+1}"),
            "rootPatternId": root_id,
            "summary": str(item.get("summary") or f"Improve {root_id or 'current boundary'}"),
            "boundedAction": str(item.get("boundedAction") or root.get("recommendedBoundedAction") or "Perform one bounded improvement and remeasure"),
            "expectedPayoff": round(payoff, 6),
            "payoffFactors": {
                "affectedItems": affected,
                "repetitionOrReuse": repetition,
                "blockingSeverity": severity_weights.get(severity, 1.0),
                "confidenceOfSafeFix": confidence,
                "estimatedCost": cost,
            },
            "authoritative": bool(item.get("authoritative", True)),
        })
    if not candidates:
        for root in roots:
            candidates.append({
                "candidateId": f"root-{root['rootPatternId']}",
                "rootPatternId": root["rootPatternId"],
                "summary": root["recommendedBoundedAction"],
                "boundedAction": root["recommendedBoundedAction"],
                "expectedPayoff": root["expectedPayoff"],
                "payoffFactors": root["payoffFactors"],
                "authoritative": True,
            })
    return sorted(candidates, key=lambda x: (-x["expectedPayoff"], x["candidateId"]))


def _metrics(deliverables: Sequence[Dict[str, Any]], validations: Sequence[Dict[str, Any]], hard: Sequence[Dict[str, Any]], soft: Sequence[Dict[str, Any]], roots: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    ready = sum(1 for x in deliverables if x["status"] == "READY")
    accepted_outputs = 0
    draft = sum(1 for x in deliverables if x["status"] in ("EMPTY", "PLACEHOLDER"))
    missing = sum(1 for x in deliverables if x["status"] == "MISSING")
    passing_validations = sum(1 for x in validations if x["status"] in set(x.get("passValues", [])))
    top_payoff = roots[0]["expectedPayoff"] if roots else 0.0
    progress_score = (
        ready * 100.0
        + passing_validations * 20.0
        - len(hard) * 1000.0
        - len(soft) * 5.0
        - top_payoff
    )
    return {
        "expectedDeliverableCount": len(deliverables),
        "acceptedOutputCount": accepted_outputs,
        "readyOutputCount": ready,
        "draftOutputCount": draft,
        "missingOutputCount": missing,
        "validationCount": len(validations),
        "passingValidationCount": passing_validations,
        "hardFailureCount": len(hard),
        "softDebtCount": len(soft),
        "rootPatternCount": len(roots),
        "topRemainingPayoff": top_payoff,
        "progressScore": round(progress_score, 6),
    }


def current_primary_artifact_fingerprint(
    workspace: str | Path,
    boundary_id: str,
    *,
    project_root: str | Path | None = None,
) -> str:
    workspace_path = Path(workspace).resolve()
    root = project_root_for(workspace_path, project_root)
    contract = load_contract(workspace_path, boundary_id)
    if contract.get("projectRootIdentity") != fingerprint(str(root)):
        raise BoundaryError("boundary contract belongs to a different project root")
    policy = load_policy(workspace_path)
    placeholder_patterns = policy.get("placeholderPatterns", list(PLACEHOLDER_PATTERNS))
    deliverables = [
        _artifact_measurement(root, spec, placeholder_patterns)[0]
        for spec in contract.get("expectedDeliverables", [])
    ]
    return fingerprint(deliverables)


def measure_boundary(
    workspace: str | Path,
    boundary_id: str,
    *,
    project_root: str | Path | None = None,
    write: bool = True,
) -> Dict[str, Any]:
    workspace_path = Path(workspace).resolve()
    root = project_root_for(workspace_path, project_root)
    contract = load_contract(workspace_path, boundary_id)
    if contract.get("projectRootIdentity") != fingerprint(str(root)):
        raise BoundaryError("boundary contract belongs to a different project root")
    state = load_state(workspace_path, boundary_id)
    policy = load_policy(workspace_path)
    placeholder_patterns = policy.get("placeholderPatterns", list(PLACEHOLDER_PATTERNS))
    severity_weights = {**SEVERITY_WEIGHT, **policy.get("severityWeights", {})}

    deliverables: List[Dict[str, Any]] = []
    validations: List[Dict[str, Any]] = []
    hard: List[Dict[str, Any]] = []
    all_issues: List[Dict[str, Any]] = []
    for spec in contract.get("expectedDeliverables", []):
        measured, issues = _artifact_measurement(root, spec, placeholder_patterns)
        deliverables.append(measured)
        hard.extend(issues)
        all_issues.extend(issues)
    artifact_fp = fingerprint(deliverables)
    for spec in contract.get("validationEvidence", []):
        measured, issues = _validation_measurement(root, spec, artifact_fp)
        validations.append(measured)
        hard.extend(issues)
        all_issues.extend(issues)
    for spec in contract.get("findingSources", []):
        findings = _load_findings(root, spec)
        all_issues.extend(findings)
        hard.extend(x for x in findings if x.get("blocking"))

    hard_types = set(policy.get("hardInvariantTypes", []))
    for issue in all_issues:
        authoritative = issue.get("authority", "authoritative") == "authoritative"
        forced = issue.get("type") in hard_types or issue.get("severity") in ("high", "critical")
        if authoritative and (issue.get("blocking") or forced):
            if not any(existing.get("issueId") == issue.get("issueId") for existing in hard):
                hard.append(issue)

    hard_ids = {x["issueId"] for x in hard}
    soft = [x for x in all_issues if x["issueId"] not in hard_ids]
    roots = _root_patterns(all_issues, severity_weights)
    candidates = _candidate_list(contract, roots, severity_weights)
    metrics = _metrics(deliverables, validations, hard, soft, roots)

    previous_packet: Optional[Dict[str, Any]] = None
    if packet_path(workspace_path, boundary_id).exists():
        try:
            previous_packet = load_json(packet_path(workspace_path, boundary_id))
        except Exception:
            previous_packet = None
    previous_metrics = (previous_packet or {}).get("metrics", {})
    delta = {
        "previousMetricsFingerprint": (previous_packet or {}).get("metricsFingerprint", ""),
        "progressScoreDelta": round(metrics["progressScore"] - float(previous_metrics.get("progressScore", metrics["progressScore"])), 6),
        "hardFailureDelta": metrics["hardFailureCount"] - int(previous_metrics.get("hardFailureCount", metrics["hardFailureCount"])),
        "softDebtDelta": metrics["softDebtCount"] - int(previous_metrics.get("softDebtCount", metrics["softDebtCount"])),
        "acceptedOutputDelta": metrics["acceptedOutputCount"] - int(previous_metrics.get("acceptedOutputCount", metrics["acceptedOutputCount"])),
    }

    validation_fp = fingerprint(validations)
    metrics_fp = fingerprint({
        "deliverables": deliverables,
        "validations": validations,
        "hardInvariants": hard,
        "softDebt": soft,
        "rootPatterns": roots,
        "metrics": metrics,
        "contractFingerprint": contract["contractFingerprint"],
        "policyFingerprint": contract["policyFingerprint"],
    })
    packet: Dict[str, Any] = {
        "schemaVersion": 1,
        "boundaryId": boundary_id,
        "taskId": contract.get("taskId", ""),
        "runId": contract.get("runId", ""),
        "adapterId": contract.get("adapterId", "generic-software-task"),
        "profile": contract.get("profile", "standard"),
        "measuredAtUtc": utc_now(),
        "deliverables": deliverables,
        "validations": validations,
        "hardInvariants": hard,
        "softDebt": soft,
        "rootPatterns": roots,
        "improvementCandidates": candidates,
        "metrics": metrics,
        "delta": delta,
        "confidence": 1.0 if all(v["status"] not in ("UNKNOWN", "INVALID") for v in validations) else 0.7,
        "uncertainty": [v["id"] for v in validations if v["status"] in ("UNKNOWN", "INVALID")],
        "remainingProfileBudget": state.get("remainingImprovementCycles", 0),
        "consecutiveNoProgressCycles": state.get("noProgressStreak", 0),
        "primaryArtifactFingerprint": artifact_fp,
        "validationEvidenceFingerprint": validation_fp,
        "metricsFingerprint": metrics_fp,
        "configFingerprint": contract["contractFingerprint"],
        "policyFingerprint": contract["policyFingerprint"],
        "toolCompatibilityFingerprint": fingerprint({
            "implementationVersion": BOUNDARY_IMPLEMENTATION_VERSION,
            "schemaVersion": BOUNDARY_SCHEMA_VERSION,
            "adapterId": contract.get("adapterId", "generic-software-task"),
        }),
        "retainedDiagnostics": {
            "rawFindingCount": len(all_issues),
            "authoritativeSourceFingerprints": sorted(
                {x.get("sourcePath", "") for x in all_issues if x.get("sourcePath")}
            ),
        },
    }
    semantic_packet = {k: v for k, v in packet.items() if k not in ("packetFingerprint", "measuredAtUtc", "delta")}
    packet["packetFingerprint"] = fingerprint(semantic_packet)
    if write:
        save_json(packet_path(workspace_path, boundary_id), packet)
        state["mode"] = "NEEDS_DECISION"
        state["currentPacketFingerprint"] = packet["packetFingerprint"]
        state["nextPermittedAction"] = "DECIDE"
        save_state(workspace_path, boundary_id, state)
    return packet


def _append_chain(path: Path, state: Dict[str, Any], prefix: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    head_key = f"{prefix}HistoryHead"
    count_key = f"{prefix}HistoryCount"
    previous = state.get(head_key, "")
    sequence = int(state.get(count_key, 0)) + 1
    record = dict(payload)
    record["sequence"] = sequence
    record["previousRecordHash"] = previous
    record["recordedAtUtc"] = utc_now()
    record["recordHash"] = fingerprint(_without_fingerprint(record, "recordHash"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    state[head_key] = record["recordHash"]
    state[count_key] = sequence
    return record


def _verify_chain(path: Path, expected_head: str, expected_count: int) -> Dict[str, Any]:
    records = load_jsonl(path)
    previous = ""
    for index, record in enumerate(records, 1):
        if record.get("sequence") != index:
            raise BoundaryError(f"{path}: sequence mismatch at record {index}")
        if record.get("previousRecordHash", "") != previous:
            raise BoundaryError(f"{path}: previousRecordHash mismatch at record {index}")
        actual = fingerprint(_without_fingerprint(record, "recordHash"))
        if record.get("recordHash") != actual:
            raise BoundaryError(f"{path}: recordHash mismatch at record {index}")
        previous = actual
    if len(records) != int(expected_count):
        raise BoundaryError(f"{path}: history truncation/count mismatch")
    if previous != expected_head:
        raise BoundaryError(f"{path}: history head mismatch")
    return {"count": len(records), "head": previous}


def verify_histories(workspace: str | Path, boundary_id: str) -> Dict[str, Any]:
    state = load_state(workspace, boundary_id)
    root = boundary_dir(workspace, boundary_id)
    decision = _verify_chain(root / "decision-history.jsonl", state.get("decisionHistoryHead", ""), int(state.get("decisionHistoryCount", 0)))
    improvement = _verify_chain(root / "improvement-history.jsonl", state.get("improvementHistoryHead", ""), int(state.get("improvementHistoryCount", 0)))
    return {"status": "PASS", "decisionHistory": decision, "improvementHistory": improvement}


def _decision_input(decision: str, packet: Dict[str, Any], selected_candidate_id: str, soft_debt_ids: Sequence[str], reason: str) -> Dict[str, Any]:
    return {
        "schemaVersion": 1,
        "boundaryId": packet["boundaryId"],
        "decision": decision,
        "reason": reason,
        "packetFingerprint": packet["packetFingerprint"],
        "metricsFingerprint": packet["metricsFingerprint"],
        "selectedCandidateId": selected_candidate_id,
        "recordedSoftDebtIds": sorted(set(soft_debt_ids)),
    }


def record_decision(
    workspace: str | Path,
    boundary_id: str,
    decision: str,
    *,
    actor: str = "quality-value-manager",
    selected_candidate_id: str = "",
    soft_debt_ids: Sequence[str] = (),
    reason: str = "",
    project_root: str | Path | None = None,
) -> Dict[str, Any]:
    if decision not in DECISIONS:
        raise BoundaryError(f"unsupported boundary decision: {decision}")
    verify_histories(workspace, boundary_id)
    state = load_state(workspace, boundary_id)
    if state.get("nextPermittedAction") != "DECIDE":
        raise BoundaryError(f"decision not permitted in mode {state.get('mode')}")
    packet = measure_boundary(workspace, boundary_id, project_root=project_root, write=True)
    state = load_state(workspace, boundary_id)
    hard = packet["hardInvariants"]
    soft = packet["softDebt"]
    candidates = {x["candidateId"]: x for x in packet["improvementCandidates"]}
    valid_soft = {x["issueId"] for x in soft}
    remaining = int(state.get("remainingImprovementCycles", 0))
    no_progress = int(state.get("noProgressStreak", 0))
    threshold = int(state.get("maxConsecutiveNoProgress", 2))

    if decision in ("ACCEPT_BOUNDARY", "ACCEPT_WITH_RECORDED_SOFT_DEBT") and hard:
        raise BoundaryError("acceptance forbidden while hard invariants fail")
    if decision == "ACCEPT_BOUNDARY" and soft:
        raise BoundaryError("soft debt exists; use ACCEPT_WITH_RECORDED_SOFT_DEBT with an explicit debt list")
    if decision == "ACCEPT_WITH_RECORDED_SOFT_DEBT":
        if not soft_debt_ids:
            raise BoundaryError("soft-debt acceptance requires an explicit debt list")
        unknown = set(soft_debt_ids) - valid_soft
        if unknown:
            raise BoundaryError(f"unknown soft-debt ids: {sorted(unknown)}")
        if set(soft_debt_ids) != valid_soft:
            raise BoundaryError("soft-debt acceptance must record every current soft debt item")
    if decision == "IMPROVE_CURRENT_BOUNDARY":
        if remaining <= 0:
            raise BoundaryError("improvement forbidden: budget exhausted")
        if selected_candidate_id not in candidates:
            raise BoundaryError("improvement requires a selected authoritative candidate")
        if not candidates[selected_candidate_id].get("authoritative", False):
            raise BoundaryError("selected improvement candidate is not authoritative")
    if decision == "SPLIT_CURRENT_BOUNDARY" and selected_candidate_id and selected_candidate_id not in candidates:
        raise BoundaryError("split candidate is not present in current packet")
    if decision == "STOP_BUDGET_EXHAUSTED":
        meaningful = bool(candidates and candidates[next(iter(candidates))].get("expectedPayoff", 0) > 0)
        if remaining > 0 and no_progress < threshold and meaningful:
            raise BoundaryError("budget stop forbidden while meaningful bounded work remains")

    decision_input = _decision_input(decision, packet, selected_candidate_id, soft_debt_ids, reason)
    decision_fp = fingerprint(decision_input)
    candidate = candidates.get(selected_candidate_id)
    record = _append_chain(
        boundary_dir(workspace, boundary_id) / "decision-history.jsonl",
        state,
        "decision",
        {
            **decision_input,
            "decisionFingerprint": decision_fp,
            "actor": actor,
            "payoffFactors": candidate.get("payoffFactors", {}) if candidate else {},
        },
    )
    role_receipt = {
        "schemaVersion": 1,
        "boundaryId": boundary_id,
        "role": "quality-value-manager",
        "actor": actor,
        "packetFingerprint": packet["packetFingerprint"],
        "metricsFingerprint": packet["metricsFingerprint"],
        "decisionFingerprint": decision_fp,
        "decisionRecordHash": record["recordHash"],
        "completedAtUtc": utc_now(),
    }
    role_receipt["receiptFingerprint"] = fingerprint(_without_fingerprint(role_receipt, "receiptFingerprint"))
    save_json(boundary_dir(workspace, boundary_id) / "manager-role-receipt.json", role_receipt)
    save_json(boundary_dir(workspace, boundary_id) / "boundary-decision.json", {**decision_input, "decisionFingerprint": decision_fp})

    state["currentDecisionFingerprint"] = decision_fp
    if decision in ("ACCEPT_BOUNDARY", "ACCEPT_WITH_RECORDED_SOFT_DEBT"):
        contract = load_contract(workspace, boundary_id)
        predecessor_id = contract.get("predecessorBoundaryId", "")
        predecessor_fp = ""
        if predecessor_id:
            predecessor = verify_acceptance(workspace, predecessor_id, project_root=project_root)
            predecessor_fp = predecessor["receiptFingerprint"]
        acceptance = {
            "schemaVersion": 1,
            "boundaryId": boundary_id,
            "taskId": packet.get("taskId", ""),
            "runId": packet.get("runId", ""),
            "decision": decision,
            "packetFingerprint": packet["packetFingerprint"],
            "primaryArtifactFingerprint": packet["primaryArtifactFingerprint"],
            "metricsFingerprint": packet["metricsFingerprint"],
            "configFingerprint": packet["configFingerprint"],
            "policyFingerprint": packet["policyFingerprint"],
            "toolCompatibilityFingerprint": packet["toolCompatibilityFingerprint"],
            "validationEvidenceFingerprint": packet["validationEvidenceFingerprint"],
            "managerDecisionFingerprint": decision_fp,
            "managerRoleReceiptFingerprint": role_receipt["receiptFingerprint"],
            "recordedSoftDebtIds": sorted(set(soft_debt_ids)),
            "predecessorBoundaryId": predecessor_id,
            "predecessorReceiptFingerprint": predecessor_fp,
            "acceptedAtUtc": utc_now(),
        }
        acceptance["receiptFingerprint"] = fingerprint(_without_fingerprint(acceptance, "receiptFingerprint"))
        save_json(receipt_path(workspace, boundary_id), acceptance)
        state["mode"] = "ACCEPTED"
        state["nextPermittedAction"] = "ADVANCE"
        state["acceptanceReceiptFingerprint"] = acceptance["receiptFingerprint"]
    elif decision == "IMPROVE_CURRENT_BOUNDARY":
        state["mode"] = "IMPROVING"
        state["selectedImprovementPatternId"] = candidates[selected_candidate_id].get("rootPatternId", selected_candidate_id)
        state["selectedCandidateId"] = selected_candidate_id
        state["improvementBaselinePacketFingerprint"] = packet["packetFingerprint"]
        state["nextPermittedAction"] = "COMPLETE_IMPROVEMENT"
    elif decision == "SPLIT_CURRENT_BOUNDARY":
        state["mode"] = "SPLIT_REQUIRED"
        state["nextPermittedAction"] = "SPLIT"
    elif decision == "STOP_BUDGET_EXHAUSTED":
        state["mode"] = "STOPPED_BUDGET_EXHAUSTED"
        state["nextPermittedAction"] = "HANDOFF_LIMITATIONS"
    else:
        state["mode"] = "HUMAN_REQUIRED"
        state["nextPermittedAction"] = "HUMAN_DECISION"
    save_state(workspace, boundary_id, state)
    return {
        "status": "RECORDED",
        "decision": decision,
        "decisionFingerprint": decision_fp,
        "boundaryState": state,
        "roleReceipt": role_receipt,
        "acceptanceReceipt": load_json(receipt_path(workspace, boundary_id)) if receipt_path(workspace, boundary_id).exists() else None,
    }


def complete_improvement(
    workspace: str | Path,
    boundary_id: str,
    *,
    project_root: str | Path | None = None,
) -> Dict[str, Any]:
    verify_histories(workspace, boundary_id)
    state = load_state(workspace, boundary_id)
    if state.get("nextPermittedAction") != "COMPLETE_IMPROVEMENT":
        raise BoundaryError("no bounded improvement is awaiting measurement")
    old_packet = load_json(packet_path(workspace, boundary_id))
    if old_packet.get("packetFingerprint") != state.get("improvementBaselinePacketFingerprint"):
        raise BoundaryError("improvement baseline packet is stale or tampered")
    new_packet = measure_boundary(workspace, boundary_id, project_root=project_root, write=False)
    old_score = float(old_packet.get("metrics", {}).get("progressScore", 0))
    new_score = float(new_packet.get("metrics", {}).get("progressScore", 0))
    policy = load_policy(workspace)
    minimum = float(policy.get("minimumProgressScoreDelta", 0.000001))
    primary_changed = new_packet.get("primaryArtifactFingerprint") != old_packet.get("primaryArtifactFingerprint")
    if new_score > old_score + minimum and primary_changed:
        outcome = "COMPLETED"
        outcome_reason = "authoritative primary artifacts improved and the measured score increased"
    elif new_score > old_score + minimum and not primary_changed:
        outcome = "FAILED"
        outcome_reason = "evidence-only or metric-only change cannot count as implementation progress"
    elif new_score < old_score - minimum:
        outcome = "FAILED"
        outcome_reason = "authoritative measured quality regressed"
    else:
        outcome = "NO_PROGRESS"
        outcome_reason = "no authoritative before/after progress was measured"
    state["usedImprovementCycles"] = int(state.get("usedImprovementCycles", 0)) + 1
    state["remainingImprovementCycles"] = max(0, int(state.get("maxImprovementCycles", 0)) - state["usedImprovementCycles"])
    state["noProgressStreak"] = 0 if outcome == "COMPLETED" else int(state.get("noProgressStreak", 0)) + 1
    record = _append_chain(
        boundary_dir(workspace, boundary_id) / "improvement-history.jsonl",
        state,
        "improvement",
        {
            "schemaVersion": 1,
            "boundaryId": boundary_id,
            "candidateId": state.get("selectedCandidateId", ""),
            "rootPatternId": state.get("selectedImprovementPatternId", ""),
            "beforePacketFingerprint": old_packet["packetFingerprint"],
            "afterPacketFingerprint": new_packet["packetFingerprint"],
            "beforeMetricsFingerprint": old_packet["metricsFingerprint"],
            "afterMetricsFingerprint": new_packet["metricsFingerprint"],
            "beforeProgressScore": old_score,
            "afterProgressScore": new_score,
            "measuredDelta": round(new_score - old_score, 6),
            "primaryArtifactChanged": primary_changed,
            "outcome": outcome,
            "outcomeReason": outcome_reason,
        },
    )
    save_json(packet_path(workspace, boundary_id), new_packet)
    state["currentPacketFingerprint"] = new_packet["packetFingerprint"]
    state["mode"] = "NEEDS_DECISION"
    state["nextPermittedAction"] = "DECIDE"
    state["selectedImprovementPatternId"] = ""
    state["selectedCandidateId"] = ""
    state["improvementBaselinePacketFingerprint"] = ""
    save_state(workspace, boundary_id, state)
    return {"status": outcome, "record": record, "packet": new_packet, "boundaryState": state}


def verify_acceptance(
    workspace: str | Path,
    boundary_id: str,
    *,
    project_root: str | Path | None = None,
    _seen: Optional[set[str]] = None,
) -> Dict[str, Any]:
    verify_histories(workspace, boundary_id)
    receipt_file = receipt_path(workspace, boundary_id)
    if not receipt_file.exists():
        raise BoundaryError("acceptance receipt missing")
    receipt = load_json(receipt_file)
    actual_receipt_fp = fingerprint(_without_fingerprint(receipt, "receiptFingerprint"))
    if receipt.get("receiptFingerprint") != actual_receipt_fp:
        raise BoundaryError("acceptance receipt fingerprint mismatch")
    packet = measure_boundary(workspace, boundary_id, project_root=project_root, write=False)
    checks = {
        "packetFingerprint": packet["packetFingerprint"],
        "primaryArtifactFingerprint": packet["primaryArtifactFingerprint"],
        "metricsFingerprint": packet["metricsFingerprint"],
        "configFingerprint": packet["configFingerprint"],
        "policyFingerprint": packet["policyFingerprint"],
        "toolCompatibilityFingerprint": packet["toolCompatibilityFingerprint"],
        "validationEvidenceFingerprint": packet["validationEvidenceFingerprint"],
    }
    for field, current in checks.items():
        if receipt.get(field) != current:
            raise BoundaryError(f"acceptance invalidated by current-input drift: {field}")
    if packet["hardInvariants"]:
        raise BoundaryError("acceptance invalidated: hard invariants currently fail")
    decision = load_json(boundary_dir(workspace, boundary_id) / "boundary-decision.json")
    if decision.get("decisionFingerprint") != receipt.get("managerDecisionFingerprint"):
        raise BoundaryError("manager decision fingerprint mismatch")
    role = load_json(boundary_dir(workspace, boundary_id) / "manager-role-receipt.json")
    role_fp = fingerprint(_without_fingerprint(role, "receiptFingerprint"))
    if role.get("receiptFingerprint") != role_fp or role_fp != receipt.get("managerRoleReceiptFingerprint"):
        raise BoundaryError("stale or replayed manager role receipt")
    if role.get("packetFingerprint") != packet["packetFingerprint"] or role.get("decisionFingerprint") != decision.get("decisionFingerprint"):
        raise BoundaryError("manager role receipt is not bound to current packet and decision")
    if receipt.get("decision") == "ACCEPT_WITH_RECORDED_SOFT_DEBT":
        current_soft = sorted(x["issueId"] for x in packet["softDebt"])
        if current_soft != sorted(receipt.get("recordedSoftDebtIds", [])):
            raise BoundaryError("soft-debt receipt does not match current debt")
    predecessor_id = receipt.get("predecessorBoundaryId", "")
    if predecessor_id:
        seen = set(_seen or set())
        if boundary_id in seen or predecessor_id in seen:
            raise BoundaryError("predecessor acceptance cycle detected")
        seen.add(boundary_id)
        predecessor = verify_acceptance(workspace, predecessor_id, project_root=project_root, _seen=seen)
        if predecessor.get("receiptFingerprint") != receipt.get("predecessorReceiptFingerprint"):
            raise BoundaryError("predecessor acceptance chain mismatch")
    return {"status": "PASS", **receipt}


def validate_workspace(workspace: str | Path, *, project_root: str | Path | None = None) -> List[str]:
    errors: List[str] = []
    for boundary_id in list_boundaries(workspace):
        try:
            load_contract(workspace, boundary_id)
            verify_histories(workspace, boundary_id)
            state = load_state(workspace, boundary_id)
            if state.get("mode") == "ACCEPTED":
                verify_acceptance(workspace, boundary_id, project_root=project_root)
        except BoundaryError as exc:
            errors.append(f"boundary {boundary_id}: {exc}")
    return errors


def advancement_lock_status(workspace: str | Path, *, project_root: str | Path | None = None) -> Dict[str, Any]:
    blocked: List[Dict[str, Any]] = []
    accepted: List[str] = []
    for boundary_id in list_boundaries(workspace):
        state = load_state(workspace, boundary_id)
        mode = state.get("mode")
        if mode == "ACCEPTED":
            try:
                verify_acceptance(workspace, boundary_id, project_root=project_root)
                accepted.append(boundary_id)
            except BoundaryError as exc:
                blocked.append({"boundaryId": boundary_id, "reason": str(exc)})
        elif mode not in ("ARCHIVED",):
            blocked.append({"boundaryId": boundary_id, "reason": f"boundary mode is {mode}; valid acceptance receipt required"})
    return {"status": "PASS" if not blocked else "FAIL", "acceptedBoundaries": accepted, "blockingBoundaries": blocked}


def final_gate_check(workspace: str | Path, *, project_root: str | Path | None = None) -> Dict[str, Any]:
    """Return the canonical final-gate component for boundary advancement."""
    lock = advancement_lock_status(workspace, project_root=project_root)
    if lock.get("status") == "PASS":
        return {
            "name": "quality-value-boundary",
            "status": "PASS",
            "description": f"{len(lock.get('acceptedBoundaries', []))} boundary receipt chain(s) current",
            "blocking": True,
            "acceptedBoundaries": lock.get("acceptedBoundaries", []),
        }
    reasons = "; ".join(
        item.get("reason", "boundary blocked")
        for item in lock.get("blockingBoundaries", [])[:5]
    )
    return {
        "name": "quality-value-boundary",
        "status": "FAIL",
        "description": "advancement is locked by an unaccepted, stale, or tampered boundary",
        "blocking": True,
        "reason": reasons,
        "blockingBoundaries": lock.get("blockingBoundaries", []),
    }


def dashboard_status(workspace: str | Path, boundary_id: str, *, project_root: str | Path | None = None) -> Dict[str, Any]:
    state = load_state(workspace, boundary_id)
    packet = measure_boundary(workspace, boundary_id, project_root=project_root, write=False)
    decision = None
    decision_path = boundary_dir(workspace, boundary_id) / "boundary-decision.json"
    if decision_path.exists():
        decision = load_json(decision_path)
    metrics = packet["metrics"]
    expected = max(1, metrics["expectedDeliverableCount"])
    return {
        "schemaVersion": 1,
        "boundaryId": boundary_id,
        "stage": state.get("mode"),
        "acceptedProgress": 1.0 if state.get("mode") == "ACCEPTED" else 0.0,
        "draftCoverage": round(metrics["readyOutputCount"] / expected, 4),
        "completedOutputs": metrics["readyOutputCount"] if state.get("mode") == "ACCEPTED" else 0,
        "expectedOutputs": metrics["expectedDeliverableCount"],
        "hardBlockers": packet["hardInvariants"],
        "rootIssues": packet["rootPatterns"],
        "managerDecision": decision,
        "highestPayoffNextAction": packet["improvementCandidates"][0] if packet["improvementCandidates"] else None,
        "remainingImprovementBudget": state.get("remainingImprovementCycles", 0),
        "humanInterventionRequired": state.get("mode") == "HUMAN_REQUIRED",
        "diagnosticMetrics": metrics,
        "trends": packet.get("delta", {}),
        "hints": {
            "acceptedProgress": "Receipt-verified user value; generated files and closed tickets do not count by themselves.",
            "draftCoverage": "How much expected output exists in a non-empty, non-placeholder form; not an acceptance signal.",
            "hardBlockers": "Deterministic failures the manager cannot waive.",
            "highestPayoffNextAction": "Policy-driven payoff = affected items x reuse x severity x confidence / cost.",
            "remainingImprovementBudget": "Finite profile budget; fast changes ceremony, not hard quality thresholds.",
        },
    }


def render_dashboard_html(status: Dict[str, Any]) -> str:
    """Render a dependency-free read-only boundary dashboard."""
    def esc(value: Any) -> str:
        if value is None:
            return "--"
        return _html.escape(str(value))

    def hint(key: str) -> str:
        text = status.get("hints", {}).get(key, "")
        return f'<abbr class="hint" title="{esc(text)}">?</abbr>' if text else ""

    blockers = status.get("hardBlockers", [])
    roots = status.get("rootIssues", [])
    blocker_rows = "".join(
        f"<li><strong>{esc(item.get('type', item.get('issueId', 'blocker')))}</strong>: "
        f"{esc(item.get('summary', item.get('message', '')))}</li>"
        for item in blockers
    ) or "<li>None</li>"
    root_rows = "".join(
        f"<li><strong>{esc(item.get('rootPatternId', 'root'))}</strong> — "
        f"payoff {esc(item.get('expectedPayoff', 0))}, "
        f"occurrences {esc(item.get('occurrenceCount', 0))}, "
        f"cascades {esc(item.get('cascadeCount', 0))}</li>"
        for item in roots
    ) or "<li>None</li>"
    decision = status.get("managerDecision") or {}
    next_action = status.get("highestPayoffNextAction") or {}
    metrics = status.get("diagnosticMetrics", {})
    metric_rows = "".join(
        f"<tr><th>{esc(key)}</th><td>{esc(value)}</td></tr>"
        for key, value in sorted(metrics.items())
    )
    human = "YES" if status.get("humanInterventionRequired") else "NO"
    blocker_class = "bad" if blockers else "good"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>YourAITeam boundary {esc(status.get('boundaryId'))}</title>
<style>
body{{font:15px/1.5 system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;background:#111;color:#eee}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:1rem}}
.card{{background:#1d1d1d;border:1px solid #444;border-radius:10px;padding:1rem}}
.value{{font-size:1.6rem;font-weight:700}}
.hint{{cursor:help;border:1px solid #777;border-radius:50%;padding:0 .35rem;text-decoration:none}}
table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;border-bottom:1px solid #444;padding:.4rem}}
.bad{{color:#ff8b8b}}.good{{color:#86efac}}code{{background:#292929;padding:.1rem .3rem}}
</style></head>
<body><h1>Quality / Value Boundary</h1><p><code>{esc(status.get('boundaryId'))}</code></p>
<div class="grid">
<div class="card"><div>Stage</div><div class="value">{esc(status.get('stage'))}</div></div>
<div class="card"><div>Accepted progress {hint('acceptedProgress')}</div><div class="value">{esc(status.get('acceptedProgress'))}</div></div>
<div class="card"><div>Draft coverage {hint('draftCoverage')}</div><div class="value">{esc(status.get('draftCoverage'))}</div></div>
<div class="card"><div>Remaining budget {hint('remainingImprovementBudget')}</div><div class="value">{esc(status.get('remainingImprovementBudget'))}</div></div>
<div class="card"><div>Human required</div><div class="value">{human}</div></div>
</div>
<h2>Hard blockers {hint('hardBlockers')}</h2><ul class="{blocker_class}">{blocker_rows}</ul>
<h2>Root issues</h2><ul>{root_rows}</ul>
<h2>Manager decision</h2><p><strong>{esc(decision.get('decision', 'not recorded'))}</strong> {esc(decision.get('reason', ''))}</p>
<h2>Highest-payoff next action {hint('highestPayoffNextAction')}</h2><p><strong>{esc(next_action.get('candidateId', 'none'))}</strong> {esc(next_action.get('summary', ''))}</p>
<h2>Diagnostic metrics</h2><table>{metric_rows}</table>
<p><small>Generated files, closed tickets, and visited stages are not acceptance authority.</small></p></body></html>"""
