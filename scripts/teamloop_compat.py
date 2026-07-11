#!/usr/bin/env python3
"""
TeamLoop Harness — Backward Compatibility Gate

Validates that:
  a. All expected CLI commands still exist (no removals or renames)
  b. All existing artifacts in workspace parse against current schemas
  c. Schema files in schemas/ are valid JSON

Returns structured result: {"status": "PASS|FAIL", "findings": [...]}
"""
import glob as globmod
import json
import os
import re
import sys

# Stable public API — commands that must never be removed or renamed.
EXPECTED_COMMANDS = [
    "init-workspace",
    "apply-transition",
    "write-event",
    "next-action",
    "check-scope",
    "run-gates",
    "validate-state",
    "run-sentinel",
    "check-guard-integrity",
    "memory-doctor",
    "final-gate",
    "prepare-execution",
    "cache-inspect",
    "cache-clear",
    "cache-stats",
    "release-info",
    "test-select",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_json_multienc(path):
    """Read a JSON file trying multiple encodings."""
    for enc in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            with open(path, "r", encoding=enc) as f:
                return json.load(f)
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            continue
    return None


def _is_valid_json_file(path):
    """Return True if file can be parsed as JSON (any encoding)."""
    return _read_json_multienc(path) is not None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_registered_commands():
    """Return list of registered CLI subcommand names from teamloop-core.py.

    Reads the *commands* dict from teamloop-core.py source and returns the
    sorted list of command names (keys).  This avoids importing the module
    and triggering side-effects.
    """
    core_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "teamloop-core.py"
    )
    with open(core_path, "r", encoding="utf-8-sig") as f:
        source = f.read()

    # Find the `commands = { ... }` dict near the end of main()
    match = re.search(r'\bcommands\s*=\s*\{(.*?)\}', source, re.DOTALL)
    if not match:
        return []

    dict_body = match.group(1)
    # Extract keys: "command-name": ...
    keys = re.findall(r'"([^"]+)"\s*:', dict_body)
    return sorted(keys)


def check_backward_compat(workspace):
    """Run all backward-compatibility checks against *workspace*.

    Returns:
        dict with keys:
            status: "PASS" | "FAIL"
            findings: list of {check, status, detail}
    """
    findings = []
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # ---- Check 1: CLI command presence ----
    findings.extend(_check_cli_commands())

    # ---- Check 2: Schema files are valid JSON ----
    findings.extend(_check_schema_files_valid_json(project_root))

    # ---- Check 3: Workspace artifacts parse against schemas ----
    findings.extend(_check_workspace_artifacts(workspace, project_root))

    status = "FAIL" if any(f["status"] == "FAIL" for f in findings) else "PASS"
    return {"status": status, "findings": findings}


# ---------------------------------------------------------------------------
# Check implementations
# ---------------------------------------------------------------------------

def _check_cli_commands():
    """Verify all EXPECTED_COMMANDS are still registered."""
    findings = []
    registered = get_registered_commands()

    for cmd in EXPECTED_COMMANDS:
        if cmd not in registered:
            findings.append({
                "check": "cli-command-present",
                "status": "FAIL",
                "detail": f"Expected command '{cmd}' is not registered in teamloop-core.py",
            })

    if not findings:
        findings.append({
            "check": "cli-command-present",
            "status": "PASS",
            "detail": f"All {len(EXPECTED_COMMANDS)} expected CLI commands are registered",
        })
    return findings


def _check_schema_files_valid_json(project_root):
    """Verify every schema file in schemas/ parses as valid JSON."""
    findings = []
    schemas_dir = os.path.join(project_root, "schemas")
    if not os.path.isdir(schemas_dir):
        findings.append({
            "check": "schemas-valid-json",
            "status": "FAIL",
            "detail": f"Schemas directory not found: {schemas_dir}",
        })
        return findings

    schema_files = sorted([
        f for f in os.listdir(schemas_dir) if f.endswith(".json")
    ])
    bad = []
    for fname in schema_files:
        fpath = os.path.join(schemas_dir, fname)
        if not _is_valid_json_file(fpath):
            bad.append(fname)

    if bad:
        findings.append({
            "check": "schemas-valid-json",
            "status": "FAIL",
            "detail": f"{len(bad)} schema file(s) contain invalid JSON: {', '.join(bad)}",
        })
    else:
        findings.append({
            "check": "schemas-valid-json",
            "status": "PASS",
            "detail": f"All {len(schema_files)} schema files are valid JSON",
        })
    return findings


def _check_workspace_artifacts(workspace, project_root):
    """Verify existing workspace artifacts parse against current schemas."""
    findings = []
    workspace = os.path.abspath(workspace)

    if not os.path.isdir(workspace):
        findings.append({
            "check": "workspace-artifacts",
            "status": "FAIL",
            "detail": f"Workspace directory not found: {workspace}",
        })
        return findings

    # Build schema map from schemas/
    schemas_dir = os.path.join(project_root, "schemas")
    schema_map = {}
    if os.path.isdir(schemas_dir):
        for fname in os.listdir(schemas_dir):
            if fname.endswith(".schema.json"):
                schema_name = fname.replace(".schema.json", "")
                fpath = os.path.join(schemas_dir, fname)
                schema_obj = _read_json_multienc(fpath)
                if schema_obj is not None:
                    schema_map[schema_name] = schema_obj

    # Map artifact paths to schema names
    artifact_checks = {
        os.path.join(workspace, "state", "team-state.json"): "team-state",
        os.path.join(workspace, "profiles", "active-profile.json"): "profile",
        os.path.join(workspace, "state", "continuation-decision.json"): "continuation-decision",
        os.path.join(workspace, "state", "final-gate-result.json"): "final-gate",
    }

    # Add JSONL files with their schema names
    jsonl_checks = {
        os.path.join(workspace, "state", "backlog.jsonl"): ("task", True),
        os.path.join(workspace, "state", "events.jsonl"): ("event", True),
        os.path.join(workspace, "state", "run-ledger.jsonl"): ("run", True),
        os.path.join(workspace, "state", "blockers.jsonl"): ("blocker", True),
        os.path.join(workspace, "state", "decisions.jsonl"): ("decision", True),
    }

    bad = []

    # Check single JSON artifacts
    for fpath, schema_name in artifact_checks.items():
        if not os.path.exists(fpath):
            continue  # optional
        rel = os.path.relpath(fpath, workspace)
        data = _read_json_multienc(fpath)
        if data is None:
            bad.append(f"{rel}: invalid JSON")
            continue
        # Schema-level check: required fields present
        schema = schema_map.get(schema_name)
        if schema:
            errors = _validate_against_schema(data, schema, rel)
            if errors:
                bad.append(f"{rel}: schema violation — {'; '.join(errors[:3])}")

    # Check JSONL files
    for fpath, (schema_name, _) in jsonl_checks.items():
        if not os.path.exists(fpath):
            continue
        try:
            for enc in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
                try:
                    with open(fpath, "r", encoding=enc) as f:
                        for line_no, line in enumerate(f, 1):
                            line = line.strip()
                            if not line:
                                continue
                            entry = json.loads(line)
                            # Schema check
                            schema = schema_map.get(schema_name)
                            if schema:
                                errors = _validate_against_schema(
                                    entry, schema,
                                    f"{os.path.relpath(fpath, workspace)} line {line_no}",
                                )
                                if errors:
                                    rel = os.path.relpath(fpath, workspace)
                                    bad.append(
                                        f"{rel} line {line_no}: schema violation — "
                                        f"{' '.join(errors[:3])}"
                                    )
                    break
                except (UnicodeDecodeError, ValueError):
                    continue
                except json.JSONDecodeError as e:
                    rel = os.path.relpath(fpath, workspace)
                    bad.append(f"{rel} line {line_no}: {e}")
                    break
        except Exception as e:
            rel = os.path.relpath(fpath, workspace)
            bad.append(f"{rel}: read error — {e}")

    # Also check run artifacts (gate-result.json, etc.)
    runs_dir = os.path.join(workspace, "runs")
    if os.path.isdir(runs_dir):
        for run_name in sorted(os.listdir(runs_dir)):
            run_path = os.path.join(runs_dir, run_name)
            if not os.path.isdir(run_path):
                continue
            # gate-result.json
            gr_path = os.path.join(run_path, "gate-result.json")
            if os.path.exists(gr_path):
                gr = _read_json_multienc(gr_path)
                if gr is None:
                    bad.append(f"runs/{run_name}/gate-result.json: invalid JSON")
                else:
                    schema = schema_map.get("gate-result")
                    if schema:
                        errors = _validate_against_schema(gr, schema, f"runs/{run_name}/gate-result.json")
                        if errors:
                            bad.append(f"runs/{run_name}/gate-result.json: schema violation — {'; '.join(errors[:3])}")

    if bad:
        findings.append({
            "check": "workspace-artifacts",
            "status": "FAIL",
            "detail": f"{len(bad)} artifact(s) failed validation:\n" + "\n".join(f"  - {b}" for b in bad),
        })
    else:
        findings.append({
            "check": "workspace-artifacts",
            "status": "PASS",
            "detail": "All workspace artifacts parse correctly against current schemas",
        })
    return findings


# ---------------------------------------------------------------------------
# Minimal schema validator (reuses the same logic as teamloop-core.py)
# ---------------------------------------------------------------------------

def _validate_against_schema(data, schema, path="root"):
    """Minimal JSON Schema draft-07 validator."""
    errors = []
    _validate(data, schema, path, errors)
    return errors


def _validate(instance, schema, path, errors):
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(instance, dict):
            errors.append(f"{path}: expected object, got {type(instance).__name__}")
            return
        for req in schema.get("required", []):
            if req not in instance:
                errors.append(f"{path}: missing required field '{req}'")
        if schema.get("additionalProperties") is False:
            allowed = set(schema.get("properties", {}).keys())
            for key in instance:
                if key not in allowed:
                    errors.append(f"{path}: additional property '{key}' not allowed")
        for prop, prop_schema in schema.get("properties", {}).items():
            if prop in instance:
                _validate(instance[prop], prop_schema, f"{path}.{prop}", errors)
    elif schema_type == "array":
        if not isinstance(instance, list):
            errors.append(f"{path}: expected array, got {type(instance).__name__}")
            return
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: array has {len(instance)} items, minimum is {schema['minItems']}")
        for i, item in enumerate(instance):
            if "items" in schema:
                _validate(item, schema["items"], f"{path}[{i}]", errors)
    elif schema_type == "string":
        if not isinstance(instance, str):
            errors.append(f"{path}: expected string, got {type(instance).__name__}")
    elif schema_type == "integer":
        if not isinstance(instance, int) or isinstance(instance, bool):
            errors.append(f"{path}: expected integer, got {type(instance).__name__}")
    elif schema_type == "boolean":
        if not isinstance(instance, bool):
            errors.append(f"{path}: expected boolean, got {type(instance).__name__}")
    elif schema_type == "number":
        if not isinstance(instance, (int, float)) or isinstance(instance, bool):
            errors.append(f"{path}: expected number, got {type(instance).__name__}")
    if "const" in schema and schema_type != "integer":
        if instance != schema["const"]:
            errors.append(f"{path}: value != const {schema['const']}")


# ---------------------------------------------------------------------------
# CLI entry point (also callable from teamloop-core.py)
# ---------------------------------------------------------------------------

def main():
    """Standalone CLI for testing: python scripts/teamloop_compat.py --workspace .teamloop [--json]"""
    import argparse
    parser = argparse.ArgumentParser(description="Backward compatibility gate")
    parser.add_argument("--workspace", "-w", default=".teamloop")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    result = check_backward_compat(args.workspace)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Compatibility gate: {result['status']}")
        for f in result["findings"]:
            tag = f["status"]
            print(f"  [{tag}] {f['check']}: {f['detail']}")

    sys.exit(1 if result["status"] == "FAIL" else 0)


if __name__ == "__main__":
    main()
