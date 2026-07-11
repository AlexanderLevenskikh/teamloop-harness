#!/usr/bin/env python3
"""
TeamLoop Harness — Core Runtime
Shared Python implementation for all runtime operations.
Called by .sh and .ps1 wrappers.
"""
import argparse
import datetime
import glob as globmod
import json
import os
import re
import shutil
import subprocess
import sys
import fnmatch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def read_json(path):
    for enc in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            with open(path, "r", encoding=enc) as f:
                return json.load(f)
        except (UnicodeDecodeError, ValueError):
            continue
    raise ValueError(f"Cannot decode JSON file: {path}")


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")
    shutil.move(tmp, path)


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    entries = []
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


def append_jsonl(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_json_file_safe(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    for enc in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            with open(path, "r", encoding=enc) as f:
                return json.load(f)
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            continue
    return None


def is_invalid_json_file(path):
    """Return True if file exists, is non-empty, and contains invalid JSON."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    for enc in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            with open(path, "r", encoding=enc) as f:
                json.load(f)
            return False
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            continue
    return True


# ---------------------------------------------------------------------------
# Schema Validation (lightweight, no external deps)
# ---------------------------------------------------------------------------

def validate_against_schema(data, schema, path="root"):
    """Minimal JSON Schema draft-07 validator for our schemas."""
    errors = []
    _validate(data, schema, path, errors)
    return errors


def _validate(instance, schema, path, errors):
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(instance, dict):
            errors.append(f"{path}: expected object, got {type(instance).__name__}")
            return
        # required
        for req in schema.get("required", []):
            if req not in instance:
                errors.append(f"{path}: missing required field '{req}'")
        # additionalProperties
        if schema.get("additionalProperties") is False:
            allowed = set(schema.get("properties", {}).keys())
            for key in instance:
                if key not in allowed:
                    errors.append(f"{path}: additional property '{key}' not allowed")
        # properties
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
            return
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: string length {len(instance)} < minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: string length {len(instance)} > maxLength {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: string '{instance}' does not match pattern '{schema['pattern']}'")
        if "enum" in schema and instance not in schema["enum"]:
            errors.append(f"{path}: value '{instance}' not in enum {schema['enum']}")
    elif schema_type == "integer":
        if not isinstance(instance, int) or isinstance(instance, bool):
            errors.append(f"{path}: expected integer, got {type(instance).__name__}")
            return
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: value {instance} < minimum {schema['minimum']}")
        if "const" in schema and instance != schema["const"]:
            errors.append(f"{path}: value {instance} != const {schema['const']}")
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
# Command: init-workspace
# ---------------------------------------------------------------------------

def cmd_init_workspace(args):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    template_dir = os.path.join(project_root, "templates", "workspace")
    workspace = args.workspace
    profile = args.profile or "generic-software-task"

    if os.path.isabs(workspace):
        target_dir = workspace
    else:
        target_dir = os.path.join(os.getcwd(), workspace)

    if os.path.exists(target_dir):
        print(f"Error: Workspace already exists at {target_dir}. Remove it first or use a different name.", file=sys.stderr)
        sys.exit(1)

    now = utc_now_iso()

    for subdir in ["state", "runs", "research", "policies", "profiles"]:
        os.makedirs(os.path.join(target_dir, subdir), exist_ok=True)

    # team-state.json
    src = os.path.join(template_dir, "state", "team-state.json")
    dst = os.path.join(target_dir, "state", "team-state.json")
    state = read_json(src)
    state["createdAtUtc"] = now
    state["updatedAtUtc"] = now
    state["profile"] = profile
    write_json(dst, state)

    # JSONL ledgers (empty)
    for name in ["backlog.jsonl", "events.jsonl", "run-ledger.jsonl", "decisions.jsonl", "blockers.jsonl"]:
        with open(os.path.join(target_dir, "state", name), "w", encoding="utf-8") as f:
            pass

    # Policies
    for name in ["gate-policy.json", "role-policy.json"]:
        src = os.path.join(template_dir, "policies", name)
        dst = os.path.join(target_dir, "policies", name)
        shutil.copy2(src, dst)

    # scope-policy.json — substitute .teamloop/** with actual workspace basename
    scope_src = os.path.join(template_dir, "policies", "scope-policy.json")
    scope_dst = os.path.join(target_dir, "policies", "scope-policy.json")
    scope_data = read_json(scope_src)
    ws_basename = os.path.basename(target_dir)
    for key in ("defaultAllowedWrites", "alwaysAllowedWrites"):
        if key in scope_data:
            scope_data[key] = [
                p.replace(".teamloop", ws_basename, 1) if ".teamloop" in p else p
                for p in scope_data[key]
            ]
    write_json(scope_dst, scope_data)

    # Profile
    profile_source = os.path.join(project_root, "profiles", profile, "profile.json")
    if os.path.exists(profile_source):
        shutil.copy2(profile_source, os.path.join(target_dir, "profiles", "active-profile.json"))
    else:
        src = os.path.join(template_dir, "profiles", "active-profile.json")
        profile_data = read_json(src)
        profile_data["profileId"] = profile
        write_json(os.path.join(target_dir, "profiles", "active-profile.json"), profile_data)

    # Memory directory
    memory_src_dir = os.path.join(template_dir, "memory")
    memory_dst_dir = os.path.join(target_dir, "memory")
    os.makedirs(memory_dst_dir, exist_ok=True)

    # project-profile.json — copy template and substitute workspace name
    pp_src = os.path.join(memory_src_dir, "project-profile.json")
    pp_dst = os.path.join(memory_dst_dir, "project-profile.json")
    if os.path.exists(pp_src):
        pp_data = read_json(pp_src)
        pp_data["workspace"] = ws_basename
        pp_data["memoryVersion"] = "1"
        write_json(pp_dst, pp_data)
    else:
        # Fallback: create a valid default profile
        write_json(pp_dst, {
            "schemaVersion": 1,
            "workspace": ws_basename,
            "memoryVersion": "1"
        })

    # Copy remaining memory template files (JSONL and markdown)
    for name in ["lessons.jsonl", "antipatterns.jsonl", "decisions.jsonl", "evidence-map.jsonl", "memory-summary.md"]:
        src = os.path.join(memory_src_dir, name)
        dst = os.path.join(memory_dst_dir, name)
        if os.path.exists(src):
            shutil.copy2(src, dst)
        else:
            # Fallback: create empty file for JSONL, stub for markdown
            with open(dst, "w", encoding="utf-8") as f:
                if name.endswith(".md"):
                    f.write("# Memory Summary\n\nNo lessons, antipatterns, or decisions recorded yet.\n")

    print(f"TeamLoop workspace initialized at {target_dir} with profile '{profile}'.")


# ---------------------------------------------------------------------------
# Command: validate-state
# ---------------------------------------------------------------------------

def cmd_validate_state(args):
    workspace = resolve_workspace(args.workspace)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    schemas_dir = os.path.join(project_root, "schemas")

    errors = []

    # Load schemas
    schema_map = {}
    for name in os.listdir(schemas_dir):
        if name.endswith(".schema.json"):
            base = name.replace(".schema.json", "")
            schema_map[base] = read_json(os.path.join(schemas_dir, name))

    # --- team-state.json ---
    state_file = os.path.join(workspace, "state", "team-state.json")
    state = read_json_file_safe(state_file)
    if state is None:
        errors.append("team-state.json: file not found or invalid JSON")
    else:
        schema_errors = validate_against_schema(state, schema_map.get("team-state", {}), "team-state")
        errors.extend(schema_errors)

        status = state.get("status", "")
        phase = state.get("currentPhase", "")

        # Phase invariants
        errors.extend(_validate_phase_invariants(phase, state, workspace))

        # DONE validation
        if status == "DONE" or phase == "DONE":
            errors.extend(_validate_done(workspace, state))

        # HUMAN_DECISION_REQUIRED validation
        if phase == "HUMAN_DECISION_REQUIRED" or status == "HUMAN_DECISION_REQUIRED":
            errors.extend(_validate_human_required(workspace))

        # currentTaskId validation
        task_id = state.get("currentTaskId", "")
        if task_id:
            found = False
            for task in read_jsonl(os.path.join(workspace, "state", "backlog.jsonl")):
                if task.get("taskId") == task_id:
                    found = True
                    break
            if not found:
                ct = read_json_file_safe(os.path.join(workspace, "state", "current-task.json"))
                if ct and ct.get("taskId") == task_id:
                    found = True
            if not found:
                errors.append(f"team-state: currentTaskId '{task_id}' not found in backlog or current-task.json")

        # currentRunId validation
        run_id = state.get("currentRunId", "")
        if run_id:
            run_dir = os.path.join(workspace, "runs", run_id)
            if not os.path.isdir(run_dir):
                run_found = False
                for entry in read_jsonl(os.path.join(workspace, "state", "run-ledger.jsonl")):
                    if entry.get("runId") == run_id:
                        run_found = True
                        break
                if not run_found:
                    errors.append(f"team-state: currentRunId '{run_id}' not found")

    # --- JSONL files ---
    jsonl_schemas = {
        "backlog": "task",
        "events": "event",
        "run-ledger": "run",
        "blockers": "blocker",
    }
    for name, schema_name in jsonl_schemas.items():
        jsonl_path = os.path.join(workspace, "state", f"{name}.jsonl")
        if not os.path.exists(jsonl_path):
            errors.append(f"{name}.jsonl: file not found")
            continue
        schema = schema_map.get(schema_name, {})
        if not schema:
            continue
        try:
            entries = read_jsonl(jsonl_path)
            for i, entry in enumerate(entries, 1):
                entry_errors = validate_against_schema(entry, schema, f"{name}.jsonl line {i}")
                errors.extend(entry_errors)
        except (json.JSONDecodeError, ValueError) as e:
            errors.append(f"{name}.jsonl: JSON parse error: {e}")

    # --- current-task.json ---
    ct_path = os.path.join(workspace, "state", "current-task.json")
    ct = read_json_file_safe(ct_path)
    if ct is not None:
        schema_errors = validate_against_schema(ct, schema_map.get("task", {}), "current-task.json")
        errors.extend(schema_errors)

    # --- active-profile.json ---
    profile_path = os.path.join(workspace, "profiles", "active-profile.json")
    profile = read_json_file_safe(profile_path)
    if profile is None:
        errors.append("active-profile.json: file not found or invalid JSON")
    else:
        schema_errors = validate_against_schema(profile, schema_map.get("profile", {}), "active-profile.json")
        errors.extend(schema_errors)

    # --- gate-result.json files ---
    runs_dir = os.path.join(workspace, "runs")
    if os.path.isdir(runs_dir):
        for run_name in os.listdir(runs_dir):
            gr_path = os.path.join(runs_dir, run_name, "gate-result.json")
            gr = read_json_file_safe(gr_path)
            if gr is not None:
                schema_errors = validate_against_schema(gr, schema_map.get("gate-result", {}), f"runs/{run_name}/gate-result.json")
                errors.extend(schema_errors)

    # --- research files ---
    research_dir = os.path.join(workspace, "research")
    if os.path.isdir(research_dir):
        for rfile in os.listdir(research_dir):
            if rfile.endswith(".json"):
                rpath = f"research/{rfile}"
                rdata = read_json_file_safe(os.path.join(research_dir, rfile))
                if rdata is not None:
                    matched = False
                    for sname in ["research-report", "research-review"]:
                        schema = schema_map.get(sname, None)
                        if schema:
                            rerrors = validate_against_schema(rdata, schema, rpath)
                            if not rerrors:
                                matched = True
                                break
                    if not matched:
                        # None of the research schemas matched; report against the first one
                        first_schema = schema_map.get("research-report")
                        if first_schema:
                            rerrors = validate_against_schema(rdata, first_schema, rpath)
                            errors.extend(rerrors)
                        else:
                            errors.append(f"{rpath}: no research schema available for validation")

    # --- Memory directory ---
    memory_dir = os.path.join(workspace, "memory")
    if os.path.isdir(memory_dir):
        # Memory JSONL files validated against their schemas
        memory_jsonl_schemas = {
            "lessons.jsonl": "lesson",
            "antipatterns.jsonl": "antipattern",
            "decisions.jsonl": "decision",
            "evidence-map.jsonl": "evidence",
        }
        for name, schema_name in memory_jsonl_schemas.items():
            jsonl_path = os.path.join(memory_dir, name)
            if not os.path.exists(jsonl_path):
                continue  # memory files are optional; missing is fine
            schema = schema_map.get(schema_name, {})
            if not schema:
                continue
            try:
                entries = read_jsonl(jsonl_path)
                for i, entry in enumerate(entries, 1):
                    entry_errors = validate_against_schema(entry, schema, f"memory/{name} line {i}")
                    errors.extend(entry_errors)
            except (json.JSONDecodeError, ValueError) as e:
                errors.append(f"memory/{name}: JSON parse error: {e}")

        # project-profile.json validated against memory-profile schema
        pp_path = os.path.join(memory_dir, "project-profile.json")
        pp = read_json_file_safe(pp_path)
        if pp is not None:
            pp_errors = validate_against_schema(pp, schema_map.get("memory-profile", {}), "memory/project-profile.json")
            errors.extend(pp_errors)

        # Semantic validation: use the canonical _validate_memory function.
        # This covers evidence linkage AND supersededBy integrity.
        # We do NOT re-run JSON parse or schema checks here — those are already
        # handled by the loops above. Instead we call _validate_memory_internal
        # with semantic_only=True so only the semantic checks fire.
        memory_result = _validate_memory_internal(memory_dir, schema_map={}, semantic_only=True)
        errors.extend(memory_result["issues"])

    # --- Stale current-task.json check ---
    # If team-state has no active task but current-task.json exists with IN_PROGRESS, that's stale.
    if state is not None and not state.get("currentTaskId", ""):
        ct_path = os.path.join(workspace, "state", "current-task.json")
        ct = read_json_file_safe(ct_path)
        if ct and ct.get("status") == "IN_PROGRESS":
            errors.append("state/current-task.json: stale IN_PROGRESS task while team-state has no currentTaskId")

    # --- Active current-task.json taskId mismatch invariant ---
    # If phase is task-scoped and currentTaskId is set, current-task.json must exist
    # and its taskId must match team-state's currentTaskId.
    if state is not None:
        task_scoped_phases = frozenset([
            "EXECUTING_TASK", "NEEDS_CHANGE_REVIEW", "NEEDS_GATE",
            "REVIEW_FAILED", "GATE_FAILED"
        ])
        if phase in task_scoped_phases and task_id:
            ct_path = os.path.join(workspace, "state", "current-task.json")
            ct = read_json_file_safe(ct_path)
            if ct is None:
                errors.append(f"team-state: phase '{phase}' with currentTaskId '{task_id}' requires current-task.json to exist")
            elif ct.get("taskId") != task_id:
                errors.append(f"team-state: current-task.json.taskId '{ct.get('taskId')}' does not match currentTaskId '{task_id}'")

    # --- Continuation decision consistency ---
    if state is not None:
        errors.extend(_validate_continuation_consistency(workspace, state, schema_map))

    # --- Check all existing .json files for valid JSON ---
    # A file that exists but contains invalid JSON is a validation error.
    # A file that doesn't exist is optional — ignored.
    import glob as globmod
    json_pattern = os.path.join(workspace, "**", "*.json")
    for jpath in globmod.glob(json_pattern, recursive=True):
        rel = os.path.relpath(jpath, workspace)
        if is_invalid_json_file(jpath):
            errors.append(f"{rel}: file exists but contains invalid JSON")

    # --- Guard integrity check (last, optional, backward-compatible) ---
    # If protected-paths.json exists, run guard integrity checks.
    # enforcementLevel "error" adds errors (fails validation),
    # "warn" adds warnings (does not fail validation),
    # "off" or missing policy → skip entirely.
    guard_errors, guard_warnings = _check_guard_integrity_for_validate(workspace, project_root)
    errors.extend(guard_errors)
    if guard_warnings:
        for w in guard_warnings:
            print(f"  WARNING: {w}", file=sys.stderr)

    if errors:
        print("VALIDATION FAILED:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    print("VALIDATION PASSED")


def _validate_phase_invariants(phase, state, workspace):
    """Semantic invariants that must hold for each operational phase."""
    errors = []
    task_id = state.get("currentTaskId", "")
    run_id = state.get("currentRunId", "")

    if phase in ("EXECUTING_TASK", "NEEDS_CHANGE_REVIEW", "NEEDS_GATE"):
        if not task_id:
            errors.append(f"team-state: phase '{phase}' requires currentTaskId")
        if not run_id:
            errors.append(f"team-state: phase '{phase}' requires currentRunId")

    if phase in ("GATE_FAILED",):
        if not run_id:
            errors.append(f"team-state: phase '{phase}' requires currentRunId")
        if run_id:
            gr_path = os.path.join(workspace, "runs", run_id, "gate-result.json")
            if not os.path.exists(gr_path):
                errors.append(f"team-state: phase '{phase}' requires gate-result.json for run '{run_id}'")

    if phase == "REVIEW_FAILED":
        if not task_id:
            errors.append(f"team-state: phase '{phase}' requires currentTaskId")
        if not run_id:
            errors.append(f"team-state: phase '{phase}' requires currentRunId")

    return errors


def _validate_done(workspace, state):
    """DONE requires: no open tasks, no unresolved blockers, passing required gates, final report."""
    errors = []

    # No open tasks
    for task in read_jsonl(os.path.join(workspace, "state", "backlog.jsonl")):
        if task.get("status") not in ("DONE", "CANCELLED", "SKIPPED", "FAILED"):
            errors.append(f"team-state: cannot be DONE with open task '{task.get('taskId')}' (status: {task.get('status')})")
            break

    # No unresolved blockers
    for blocker in read_jsonl(os.path.join(workspace, "state", "blockers.jsonl")):
        if not blocker.get("resolvedAtUtc"):
            errors.append(f"team-state: cannot be DONE with unresolved blocker '{blocker.get('blockerId')}'")
            break

    # Passing required gates: check last gate-result
    run_id = state.get("currentRunId", "")
    if run_id:
        gr_path = os.path.join(workspace, "runs", run_id, "gate-result.json")
        gr = read_json_file_safe(gr_path)
        if gr:
            for check in gr.get("checks", []):
                if check.get("status") == "FAIL":
                    errors.append(f"team-state: cannot be DONE with failed gate check '{check.get('name')}'")
                    break

    # Final report
    final_report = os.path.join(workspace, "final-report.md")
    if not os.path.exists(final_report):
        errors.append("team-state: cannot be DONE without final-report.md")

    return errors


def _validate_human_required(workspace):
    """HUMAN_DECISION_REQUIRED requires at least one valid open blocker."""
    blockers = read_jsonl(os.path.join(workspace, "state", "blockers.jsonl"))
    for b in blockers:
        if b.get("resolvedAtUtc"):
            continue
        valid = True
        if b.get("type") != "HUMAN_DECISION_REQUIRED":
            valid = False
        if not b.get("category"):
            valid = False
        if not b.get("summary"):
            valid = False
        if not b.get("evidence") or not isinstance(b.get("evidence"), list) or len(b["evidence"]) == 0:
            valid = False
        questions = b.get("questionsForHuman")
        if not questions or not isinstance(questions, list) or len(questions) == 0:
            valid = False
        if valid:
            return []  # found valid blocker
    return ["team-state: HUMAN_DECISION_REQUIRED requires at least one valid open blocker (type=HUMAN_DECISION_REQUIRED, category, non-empty summary, evidence, questionsForHuman)"]


def _validate_continuation_consistency(workspace, state, schema_map):
    """Validate continuation-decision.json for consistency with team-state.

    Checks:
      1. Schema validation against continuation-decision.schema.json
      2. Decision vs phase consistency
      3. Stale taskId reference check
      4. HUMAN_DECISION_REQUIRED requires open blockers
      5. DONE requires clean state (no open tasks, no active run/task)
      6. CONTINUE requires at least one READY task
      7. BLOCKED requires at least one open blocker
      8. SAFE_CHECKPOINT in DONE phase is impossible
      9. Empty checks array is invalid
    """
    errors = []
    decision_file = os.path.join(workspace, "state", "continuation-decision.json")

    # Missing file is OK — backward compatibility
    if not os.path.exists(decision_file):
        return errors

    # Check for invalid JSON before schema validation
    if is_invalid_json_file(decision_file):
        errors.append("continuation-decision.json: file exists but contains invalid JSON")
        return errors

    decision = read_json_file_safe(decision_file)
    if decision is None:
        return errors

    # 1. Schema validation
    cd_schema = schema_map.get("continuation-decision", None)
    if cd_schema:
        schema_errors = validate_against_schema(decision, cd_schema, "continuation-decision.json")
        errors.extend(schema_errors)

    # If schema validation already failed (e.g., missing required fields),
    # we can still check what we can with .get() fallbacks
    decision_val = decision.get("decision", "")
    phase = state.get("currentPhase", "")
    status = state.get("status", "")
    task_id_ref = decision.get("taskId", "")

    # 9. Empty checks array fails validation
    checks = decision.get("checks", None)
    if checks is not None and isinstance(checks, list) and len(checks) == 0:
        errors.append("continuation-decision.json: 'checks' array must not be empty (minItems: 1)")

    # 2. Decision vs phase consistency
    if decision_val == "DONE":
        if phase != "DONE" and status != "DONE":
            errors.append(
                f"continuation-decision.json: decision 'DONE' inconsistent with "
                f"phase '{phase}' / status '{status}' (must be DONE)"
            )

    elif decision_val == "HUMAN_DECISION_REQUIRED":
        if phase != "HUMAN_DECISION_REQUIRED" and status != "HUMAN_DECISION_REQUIRED":
            errors.append(
                f"continuation-decision.json: decision 'HUMAN_DECISION_REQUIRED' inconsistent with "
                f"phase '{phase}' / status '{status}' (must be HUMAN_DECISION_REQUIRED)"
            )

    elif decision_val == "SAFE_CHECKPOINT":
        if phase == "DONE" or status == "DONE":
            errors.append(
                f"continuation-decision.json: decision 'SAFE_CHECKPOINT' inconsistent with "
                f"completed phase '{phase}' / status '{status}' (cannot checkpoint after DONE)"
            )

    # 3. Stale taskId reference check
    if task_id_ref:
        backlog = read_jsonl(os.path.join(workspace, "state", "backlog.jsonl"))
        found = False
        for task in backlog:
            if task.get("taskId") == task_id_ref:
                found = True
                break
        # Also check current-task.json
        if not found:
            ct = read_json_file_safe(os.path.join(workspace, "state", "current-task.json"))
            if ct and ct.get("taskId") == task_id_ref:
                found = True
        if not found:
            errors.append(
                f"continuation-decision.json: taskId '{task_id_ref}' not found in backlog or current-task.json"
            )

    # 4. HUMAN_DECISION_REQUIRED requires open blockers
    if decision_val == "HUMAN_DECISION_REQUIRED":
        blockers = read_jsonl(os.path.join(workspace, "state", "blockers.jsonl"))
        has_open = any(not b.get("resolvedAtUtc") for b in blockers)
        blockers_summary = decision.get("blockersSummary", None)
        if not has_open and not blockers_summary:
            errors.append(
                "continuation-decision.json: decision 'HUMAN_DECISION_REQUIRED' requires "
                "at least one open blocker in blockers.jsonl or 'blockersSummary' in the decision"
            )

    # 5. DONE requires clean state
    if decision_val == "DONE":
        # No READY or IN_PROGRESS tasks
        for task in read_jsonl(os.path.join(workspace, "state", "backlog.jsonl")):
            if task.get("status") in ("READY", "IN_PROGRESS"):
                errors.append(
                    f"continuation-decision.json: decision 'DONE' with open task "
                    f"'{task.get('taskId')}' (status: {task.get('status')})"
                )
                break

        # No active run or task references in team-state
        if state.get("currentTaskId", ""):
            errors.append(
                f"continuation-decision.json: decision 'DONE' with active currentTaskId "
                f"'{state['currentTaskId']}' in team-state"
            )
        if state.get("currentRunId", ""):
            errors.append(
                f"continuation-decision.json: decision 'DONE' with active currentRunId "
                f"'{state['currentRunId']}' in team-state"
            )

    # 6. CONTINUE requires at least one READY task
    if decision_val == "CONTINUE":
        has_ready = False
        for task in read_jsonl(os.path.join(workspace, "state", "backlog.jsonl")):
            if task.get("status") == "READY":
                has_ready = True
                break
        if not has_ready:
            errors.append(
                "continuation-decision.json: decision 'CONTINUE' requires at least one "
                "READY task in backlog (none found)"
            )

    # 7. BLOCKED requires at least one open blocker
    if decision_val == "BLOCKED":
        blockers = read_jsonl(os.path.join(workspace, "state", "blockers.jsonl"))
        has_open = any(not b.get("resolvedAtUtc") for b in blockers)
        if not has_open:
            errors.append(
                "continuation-decision.json: decision 'BLOCKED' requires at least one "
                "open blocker in blockers.jsonl (none found)"
            )

    return errors


# ---------------------------------------------------------------------------
# Guard integrity check for validate-state
# ---------------------------------------------------------------------------

def _check_guard_integrity_for_validate(workspace, project_root):
    """Lightweight guard integrity check integrated into validate-state.

    Only runs if .teamloop/policies/protected-paths.json exists (backward-compatible).
    Reuses the same check functions as cmd_check_guard_integrity.

    Returns:
        (errors, warnings) — two lists of strings.
        errors: enforcementLevel is "error" and a check failed.
        warnings: enforcementLevel is "warn" and a check produced findings.
    """
    errors = []
    warnings = []

    # Skip if policy does not exist (backward-compatible)
    policy_path = os.path.join(workspace, "policies", "protected-paths.json")
    if not os.path.exists(policy_path):
        return errors, warnings

    policy = read_json_file_safe(policy_path)
    if policy is None:
        # Policy file exists but is not valid JSON — still check what we can
        return errors, warnings

    enforcement_level = policy.get("enforcementLevel", "error")
    if enforcement_level == "off":
        return errors, warnings

    # Get git status entries
    git_status_entries = _get_git_status_entries()

    # Check 1: protected paths
    pp_check, pp_violations = _check_protected_paths(policy, git_status_entries, workspace)

    # Check 2: dangerous operations
    do_check, do_violations = _check_dangerous_operations(git_status_entries)

    # Check 3: schema integrity
    si_check, si_violations = _check_schema_integrity(project_root)

    all_checks = [pp_check, do_check, si_check]
    all_violations = pp_violations + do_violations + si_violations

    has_fail = any(c["status"] == "FAIL" for c in all_checks)
    has_warn = any(c["status"] == "WARNING" for c in all_checks)

    if has_fail:
        if enforcement_level == "error":
            for v in all_violations:
                errors.append(f"guard-integrity [{v.get('check', 'unknown')}]: {v.get('detail', 'violation detected')}")
        elif enforcement_level == "warn":
            for v in all_violations:
                warnings.append(f"guard-integrity [{v.get('check', 'unknown')}]: {v.get('detail', 'violation detected')}")
    elif has_warn:
        if enforcement_level == "warn":
            for c in all_checks:
                if c["status"] == "WARNING":
                    warnings.append(f"guard-integrity [{c['name']}]: {c.get('details', 'warning')}")

    return errors, warnings


# ---------------------------------------------------------------------------
# Command: next-action
# ---------------------------------------------------------------------------

def _validate_memory(memory_dir):
    """Canonical memory validation function.

    Produces a structured result covering ALL memory checks in one pass:
    - JSON parse validity of memory JSONL files
    - Schema conformance of each entry (given a schema_map, or empty)
    - Active records have verified evidence
    - Orphaned supersededBy references

    Returns:
        dict with keys:
            checks: list of {name, status, description}
            issues: list of issue strings
            status: "PASS" | "FAIL"

    Parameters:
        memory_dir: path to the memory directory
        schema_map: optional dict of schema_name -> schema_object for schema checks
    """
    return _validate_memory_internal(memory_dir, schema_map=None, semantic_only=False)


def _validate_memory_internal(memory_dir, schema_map=None, semantic_only=False):
    """Internal implementation of _validate_memory with optional schema_map.

    When semantic_only=True, only the semantic checks (evidence linkage +
    supersededBy integrity) are run. This is used by validate-state which
    already handles JSON parse and schema checks in its own loops.

    When schema_map is None or empty, schema conformance checks are skipped.

    Returns:
        dict with keys:
            checks: list of {name, status, description}
            issues: list of issue strings
            status: "PASS" | "FAIL"
    """
    if schema_map is None:
        schema_map = {}

    all_checks = []
    all_issues = []

    if not semantic_only:
        # --- Check 1: memory-json-parse ---
        _do_json_parse_check(memory_dir, all_checks, all_issues)

        # --- Check 2: memory-schema-valid ---
        _do_schema_check(memory_dir, schema_map, all_checks, all_issues)

    # --- Check 3: active-has-evidence (semantic) ---
    _do_active_evidence_check(memory_dir, all_checks, all_issues)

    # --- Check 4: superseded-by-integrity (semantic) ---
    _do_superseded_by_check(memory_dir, all_checks, all_issues)

    overall = "FAIL" if all_issues else "PASS"
    return {
        "checks": all_checks,
        "issues": all_issues,
        "status": overall,
    }


def _do_json_parse_check(memory_dir, checks, all_issues):
    """Check 1: all memory JSONL files parse without errors."""
    issues = []
    files_checked = 0
    jsonl_files = [
        "lessons.jsonl", "antipatterns.jsonl", "decisions.jsonl",
        "evidence-map.jsonl",
    ]

    for name in jsonl_files:
        path = os.path.join(memory_dir, name)
        if not os.path.exists(path):
            continue
        files_checked += 1
        try:
            entries = read_jsonl(path)
            with open(path, "r", encoding="utf-8-sig") as f:
                lines = [l for l in f.readlines() if l.strip()]
            for i, line in enumerate(lines, 1):
                try:
                    json.loads(line)
                except json.JSONDecodeError as e:
                    issues.append(f"{name} line {i}: {e}")
        except (json.JSONDecodeError, ValueError) as e:
            issues.append(f"{name}: parse error: {e}")

    pp_path = os.path.join(memory_dir, "project-profile.json")
    if os.path.exists(pp_path):
        files_checked += 1
        if is_invalid_json_file(pp_path):
            issues.append("project-profile.json: invalid JSON")

    status = "PASS" if not issues else "FAIL"
    checks.append({
        "name": "memory-json-parse",
        "status": status,
        "description": (
            f"Parsed {files_checked} memory JSON file(s) without errors"
            if not issues
            else f"Found {len(issues)} JSON parse error(s) in memory files"
        ),
    })
    all_issues.extend(issues)


def _do_schema_check(memory_dir, schema_map, checks, all_issues):
    """Check 2: validate entries against their schemas."""
    issues = []
    memory_jsonl_schemas = {
        "lessons.jsonl": "lesson",
        "antipatterns.jsonl": "antipattern",
        "decisions.jsonl": "decision",
        "evidence-map.jsonl": "evidence",
    }

    entries_validated = 0

    for name, schema_name in memory_jsonl_schemas.items():
        jsonl_path = os.path.join(memory_dir, name)
        if not os.path.exists(jsonl_path):
            continue
        schema = schema_map.get(schema_name, {})
        if not schema:
            continue
        try:
            entries = read_jsonl(jsonl_path)
        except (json.JSONDecodeError, ValueError):
            continue
        for i, entry in enumerate(entries, 1):
            entries_validated += 1
            entry_errors = validate_against_schema(entry, schema, f"memory/{name} line {i}")
            issues.extend(entry_errors)

    pp_path = os.path.join(memory_dir, "project-profile.json")
    pp = read_json_file_safe(pp_path)
    if pp is not None:
        entries_validated += 1
        pp_errors = validate_against_schema(pp, schema_map.get("memory-profile", {}), "memory/project-profile.json")
        issues.extend(pp_errors)

    status = "PASS" if not issues else "FAIL"
    checks.append({
        "name": "memory-schema-valid",
        "status": status,
        "description": (
            f"Validated {entries_validated} entry(ies) against schemas"
            if not issues
            else f"Found {len(issues)} schema violation(s) across {entries_validated} entries"
        ),
    })
    all_issues.extend(issues)


def _do_active_evidence_check(memory_dir, checks, all_issues):
    """Check 3: ACTIVE guidance records must have verified evidence.

    Canonical evidence linkage check — consumed by both validate-state
    and memory-doctor. No duplicate logic.
    """
    issues = []
    non_active_statuses = frozenset(["DEPRECATED", "REJECTED", "SUPERSEDED"])

    # Load evidence-map
    evidence_path = os.path.join(memory_dir, "evidence-map.jsonl")
    evidence_entries = []
    if os.path.exists(evidence_path):
        try:
            evidence_entries = read_jsonl(evidence_path)
        except (json.JSONDecodeError, ValueError):
            pass

    evidence_ids = set()
    unverified_evidence_ids = set()
    for ev in evidence_entries:
        eid = ev.get("evidenceId", "")
        if eid:
            evidence_ids.add(eid)
            if ev.get("status", "VERIFIED").upper() == "UNVERIFIED":
                unverified_evidence_ids.add(eid)

    guidance_files = [
        ("lessons.jsonl", "lessonId"),
        ("antipatterns.jsonl", "antipatternId"),
        ("decisions.jsonl", "decisionId"),
    ]

    for filename, id_field in guidance_files:
        filepath = os.path.join(memory_dir, filename)
        if not os.path.exists(filepath):
            continue
        try:
            entries = read_jsonl(filepath)
        except (json.JSONDecodeError, ValueError):
            continue

        for entry in entries:
            status = entry.get("status", "")
            if status in non_active_statuses:
                continue
            if status != "ACTIVE":
                continue

            record_id = entry.get(id_field, "")
            evidence_ids_ref = entry.get("evidenceIds", [])
            if not isinstance(evidence_ids_ref, list):
                evidence_ids_ref = []

            if not evidence_ids_ref:
                issues.append(
                    f"memory/{filename}: ACTIVE {id_field} '{record_id}' has no evidenceIds"
                )
                continue

            for eid_ref in evidence_ids_ref:
                if eid_ref not in evidence_ids:
                    issues.append(
                        f"memory/{filename}: ACTIVE {id_field} '{record_id}' references missing evidenceId '{eid_ref}'"
                    )
                elif eid_ref in unverified_evidence_ids:
                    issues.append(
                        f"memory/{filename}: ACTIVE {id_field} '{record_id}' references UNVERIFIED evidenceId '{eid_ref}'"
                    )

    status = "PASS" if not issues else "FAIL"
    checks.append({
        "name": "active-has-evidence",
        "status": status,
        "description": "All ACTIVE guidance has verified evidence" if not issues
            else f"Found {len(issues)} evidence linkage error(s)",
    })
    all_issues.extend(issues)


def _do_superseded_by_check(memory_dir, checks, all_issues):
    """Check 4: supersededBy references must point to existing records.

    Canonical supersededBy integrity check — consumed by both validate-state
    and memory-doctor. No duplicate logic.
    """
    issues = []

    superseded_guidance = [
        ("lessons.jsonl", "lessonId", "supersededBy"),
        ("decisions.jsonl", "decisionId", "supersededBy"),
    ]

    for filename, id_field, sup_field in superseded_guidance:
        filepath = os.path.join(memory_dir, filename)
        if not os.path.exists(filepath):
            continue
        try:
            entries = read_jsonl(filepath)
        except (json.JSONDecodeError, ValueError):
            continue

        existing_ids = {e.get(id_field) for e in entries}

        for entry in entries:
            sup_ref = entry.get(sup_field, "")
            if sup_ref and sup_ref not in existing_ids:
                record_id = entry.get(id_field, "unknown")
                issues.append(
                    f"memory/{filename}: {id_field} '{record_id}' supersededBy '{sup_ref}' not found"
                )

    status = "PASS" if not issues else "FAIL"
    checks.append({
        "name": "superseded-by-integrity",
        "status": status,
        "description": "All supersededBy references point to existing records" if not issues
            else f"Found {len(issues)} orphaned supersededBy reference(s)",
    })
    all_issues.extend(issues)


# ---------------------------------------------------------------------------
# Command: memory-doctor
# ---------------------------------------------------------------------------

def cmd_memory_doctor(args):
    """Validate memory JSONL files and report structured findings.

    Delegates to the canonical _validate_memory function — no duplicated rules.
    Produces gate-result-style JSON output with checks array.
    Exits 0 if clean, 1 if issues found.
    """
    workspace = resolve_workspace(args.workspace)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    schemas_dir = os.path.join(project_root, "schemas")
    memory_dir = os.path.join(workspace, "memory")

    # --- Check 0: memory-subsystem-present ---
    # If the memory directory does not exist, report structured FAIL immediately.
    # If it exists but is empty (no guidance or evidence data), report WARNING.
    subsystem_checks = []
    subsystem_issues = []

    if not os.path.isdir(memory_dir):
        subsystem_checks.append({
            "name": "memory-subsystem-present",
            "status": "FAIL",
            "description": "memory directory not found at {}".format(memory_dir),
        })
        subsystem_issues.append("memory-subsystem-present: memory directory not found at {}".format(memory_dir))
    else:
        # Directory exists — check if project-profile.json is present
        pp_path = os.path.join(memory_dir, "project-profile.json")
        has_profile = os.path.exists(pp_path)

        # Check if any guidance or evidence data exists
        guidance_files = [
            "lessons.jsonl", "antipatterns.jsonl", "decisions.jsonl", "evidence-map.jsonl",
        ]
        has_data = False
        for gf in guidance_files:
            gp = os.path.join(memory_dir, gf)
            if os.path.exists(gp) and os.path.getsize(gp) > 0:
                has_data = True
                break

        if has_profile and has_data:
            subsystem_checks.append({
                "name": "memory-subsystem-present",
                "status": "PASS",
                "description": "memory directory present with project-profile.json and data files",
            })
        elif has_profile and not has_data:
            # Empty subsystem — warn but don't fail
            subsystem_checks.append({
                "name": "memory-subsystem-present",
                "status": "WARNING",
                "description": "memory directory present but no guidance or evidence data found",
            })
        else:
            # Missing project-profile.json
            subsystem_checks.append({
                "name": "memory-subsystem-present",
                "status": "FAIL",
                "description": "memory directory present but project-profile.json missing",
            })
            subsystem_issues.append("memory-subsystem-present: project-profile.json missing from {}".format(memory_dir))

    # Load schemas for schema conformance checks
    schema_map = {}
    for name in os.listdir(schemas_dir):
        if name.endswith(".schema.json"):
            base = name.replace(".schema.json", "")
            schema_map[base] = read_json(os.path.join(schemas_dir, name))

    # Run canonical validation (JSON parse, schema, evidence, supersededBy)
    # Only run deeper checks if the memory directory exists.
    # When it doesn't exist, _validate_memory_internal would silently pass,
    # which is why the memory-subsystem-present check above is essential.
    if os.path.isdir(memory_dir):
        memory_result = _validate_memory_internal(memory_dir, schema_map=schema_map)
    else:
        memory_result = {
            "checks": [],
            "issues": [],
            "status": "PASS",
        }

    # Compute counts for summary
    counts = _collect_memory_counts(memory_dir)

    # Prepend the subsystem check to the checks array
    all_checks = list(subsystem_checks) + list(memory_result["checks"])
    all_issues = list(subsystem_issues) + list(memory_result["issues"])

    # Overall status: FAIL if any FAIL check, else PASS
    has_fail = any(c["status"] == "FAIL" for c in all_checks)
    overall_status = "FAIL" if has_fail else "PASS"

    result = {
        "schemaVersion": 1,
        "status": overall_status,
        "checks": all_checks,
        "summary": counts,
        "issues": all_issues,
    }

    print(json.dumps(result, ensure_ascii=False))

    if overall_status == "FAIL":
        sys.exit(1)


def _collect_memory_counts(memory_dir):
    """Count active/deprecated/rejected entries across memory files."""
    counts = {"active": 0, "deprecated": 0, "rejected": 0, "superseded": 0, "evidence": 0}
    guidance_files = [
        ("lessons.jsonl", "lesson"),
        ("antipatterns.jsonl", "antipattern"),
        ("decisions.jsonl", "decision"),
    ]

    for filename, _ in guidance_files:
        filepath = os.path.join(memory_dir, filename)
        if not os.path.exists(filepath):
            continue
        try:
            entries = read_jsonl(filepath)
        except (json.JSONDecodeError, ValueError):
            continue
        for entry in entries:
            status = entry.get("status", "").lower()
            if status in counts:
                counts[status] += 1

    ev_path = os.path.join(memory_dir, "evidence-map.jsonl")
    if os.path.exists(ev_path):
        try:
            counts["evidence"] = len(read_jsonl(ev_path))
        except (json.JSONDecodeError, ValueError):
            pass

    return counts


def cmd_next_action(args):
    workspace = resolve_workspace(args.workspace)
    state = read_json(os.path.join(workspace, "state", "team-state.json"))
    phase = state.get("currentPhase", "")
    status = state.get("status", "")
    human_required = state.get("humanRequired", False)

    result = _compute_next_action(phase, status, human_required, workspace)
    print(json.dumps(result, ensure_ascii=False))


def _compute_next_action(phase, status, human_required, workspace):
    task_id = ""
    next_human = False

    if phase in ("NEEDS_DISCOVERY",):
        backlog = read_jsonl(os.path.join(workspace, "state", "backlog.jsonl"))
        for task in backlog:
            if task.get("status") == "READY":
                return {"nextAction": "RUN_EXECUTOR", "phase": "EXECUTING_TASK", "taskId": task["taskId"], "humanRequired": False}
        return {"nextAction": "RUN_DISCOVERY", "phase": "NEEDS_DISCOVERY", "taskId": "", "humanRequired": False}

    # If there are READY tasks in backlog, prioritize execution over slicing/discovery.
    # Also handles READY_FOR_NEXT_TASK and empty-phase (fresh workspace).
    if phase in ("", "READY_FOR_NEXT_TASK", "NEEDS_TASK_SLICING"):
        backlog = read_jsonl(os.path.join(workspace, "state", "backlog.jsonl"))
        for task in backlog:
            if task.get("status") == "READY":
                return {"nextAction": "RUN_EXECUTOR", "phase": "EXECUTING_TASK", "taskId": task["taskId"], "humanRequired": False}
        if phase == "READY_FOR_NEXT_TASK":
            return {"nextAction": "NO_READY_TASK", "phase": "READY_FOR_NEXT_TASK", "taskId": "", "humanRequired": False}
        if phase == "NEEDS_TASK_SLICING":
            return {"nextAction": "RUN_TASK_SLICER", "phase": "NEEDS_TASK_SLICING", "taskId": "", "humanRequired": False}
        # Fresh workspace with no ready tasks → discovery
        if phase == "":
            return {"nextAction": "RUN_DISCOVERY", "phase": "NEEDS_DISCOVERY", "taskId": "", "humanRequired": False}

    dispatch = {
        "NEEDS_PLAN": ("RUN_RESEARCH", "NEEDS_RESEARCH", ""),
        "NEEDS_RESEARCH": ("RUN_RESEARCHER", "NEEDS_RESEARCH", ""),
        "NEEDS_RESEARCH_REVIEW": ("RUN_RESEARCH_LEAD", "NEEDS_RESEARCH_REVIEW", ""),
        "NEEDS_TASK_SLICING": ("RUN_TASK_SLICER", "NEEDS_TASK_SLICING", ""),
        "EXECUTING_TASK": ("RUN_EXECUTOR", "EXECUTING_TASK", ""),
        "NEEDS_CHANGE_REVIEW": ("RUN_CHANGE_REVIEWER", "NEEDS_CHANGE_REVIEW", ""),
        "NEEDS_GATE": ("RUN_GATEKEEPER", "NEEDS_GATE", ""),
        "REVIEW_FAILED": ("RUN_EXECUTOR", "EXECUTING_TASK", ""),
        "HUMAN_DECISION_REQUIRED": ("STOP", "HUMAN_DECISION_REQUIRED", ""),
        "DONE": ("STOP", "DONE", ""),
    }

    if phase == "GATE_FAILED":
        state = read_json(os.path.join(workspace, "state", "team-state.json"))
        run_id = state.get("currentRunId", "")
        task_id = state.get("currentTaskId", "")
        gr_path = os.path.join(workspace, "runs", run_id, "gate-result.json") if run_id else ""
        gr = read_json_file_safe(gr_path) if gr_path else None
        if gr:
            if gr.get("humanRequired"):
                return {"nextAction": "HUMAN_DECISION", "phase": "HUMAN_DECISION_REQUIRED", "taskId": task_id, "humanRequired": True}
            na = gr.get("nextAction", "FIX_GATE_FAILURE")
            if na == "NEEDS_RESEARCH":
                return {"nextAction": "RUN_RESEARCHER", "phase": "NEEDS_RESEARCH", "taskId": task_id, "humanRequired": False}
        return {"nextAction": "RUN_EXECUTOR", "phase": "EXECUTING_TASK", "taskId": task_id, "humanRequired": False}

    if phase == "SAFE_CHECKPOINT":
        if human_required:
            return {"nextAction": "HUMAN_DECISION", "phase": "HUMAN_DECISION_REQUIRED", "taskId": "", "humanRequired": True}
        return {"nextAction": "CONTINUE_LOOP", "phase": "READY_FOR_NEXT_TASK", "taskId": "", "humanRequired": False}

    entry = dispatch.get(phase)
    if entry:
        action, new_phase, tid = entry
        if phase in ("EXECUTING_TASK", "NEEDS_CHANGE_REVIEW", "NEEDS_GATE", "REVIEW_FAILED"):
            state = read_json(os.path.join(workspace, "state", "team-state.json"))
            tid = state.get("currentTaskId", "")
        return {"nextAction": action, "phase": new_phase, "taskId": tid, "humanRequired": next_human}

    return {"nextAction": "UNKNOWN", "phase": phase, "taskId": "", "humanRequired": False}


# ---------------------------------------------------------------------------
# Command: apply-transition
# ---------------------------------------------------------------------------

# Mapping of action → (phase, requires_task_id, creates_run)
# Actions that preserve currentTaskId/currentRunId from the existing state:
#   RUN_CHANGE_REVIEWER, RUN_GATEKEEPER, GATE_FAILED
_TRANSITIONS = {
    "RUN_DISCOVERY": ("NEEDS_DISCOVERY", False, False),
    "RUN_RESEARCHER": ("NEEDS_RESEARCH", False, False),
    "RUN_RESEARCH_LEAD": ("NEEDS_RESEARCH_REVIEW", False, False),
    "RUN_TASK_SLICER": ("NEEDS_TASK_SLICING", False, False),
    "RUN_EXECUTOR": ("EXECUTING_TASK", True, True),
    "RUN_CHANGE_REVIEWER": ("NEEDS_CHANGE_REVIEW", False, False),
    "RUN_GATEKEEPER": ("NEEDS_GATE", False, False),
    "CONTINUE_LOOP": ("READY_FOR_NEXT_TASK", False, False),
    "SET_SAFE_CHECKPOINT": ("SAFE_CHECKPOINT", False, False),
    "SET_HUMAN_REQUIRED": ("HUMAN_DECISION_REQUIRED", False, False),
    "GATE_FAILED": ("GATE_FAILED", False, False),
    "REQUEST_CHANGES": ("REVIEW_FAILED", False, False),
    "SET_DONE": ("DONE", False, False),
}

# Actions that must preserve the active run/task identity from the previous state.
_TRANSITIONS_PRESERVE_IDENTITY = frozenset([
    "RUN_CHANGE_REVIEWER",
    "RUN_GATEKEEPER",
    "GATE_FAILED",
    "REQUEST_CHANGES",
])

def _maybe_write_continuation_decision(workspace, action, state, task_id, run_id):
    """Auto-write continuation-decision.json for terminal transitions.

    Terminal transitions produce a decision that determines the next step
    of the supervisor loop.  Transient transitions (RUN_EXECUTOR, etc.)
    do NOT write a decision.

    This call is wrapped in try/except so a decision-write failure never
    breaks the transition itself.
    """
    try:
        # Mapping of terminal transition -> (decision, phase_override_or_None)
        # For transitions where currentTaskId/currentRunId were cleared by the
        # transition logic, we read them from the *just-written* state (which
        # already has the cleared values).  We need the PRE-transition values
        # for traceability, so we use the task_id/run_id passed from the caller.
        # For CONTINUE_LOOP we must inspect backlog for READY tasks.

        if action == "SET_DONE":
            _write_continuation_decision(
                workspace=workspace,
                decision="DONE",
                phase=state.get("currentPhase", "DONE"),
                task_id=task_id,
                run_id=run_id,
                justification="All work completed; team state set to DONE",
            )

        elif action == "SET_SAFE_CHECKPOINT":
            _write_continuation_decision(
                workspace=workspace,
                decision="SAFE_CHECKPOINT",
                phase=state.get("currentPhase", "SAFE_CHECKPOINT"),
                task_id=task_id,
                run_id=run_id,
                justification="Safe checkpoint reached; team state is verified",
            )

        elif action == "SET_HUMAN_REQUIRED":
            # Read blockers for a summary
            blockers_summary = ""
            try:
                blockers = read_jsonl(os.path.join(workspace, "state", "blockers.jsonl"))
                open_blockers = [b for b in blockers if not b.get("resolvedAtUtc")]
                if open_blockers:
                    summaries = [b.get("summary", "no summary") for b in open_blockers[:5]]
                    blockers_summary = "; ".join(summaries)
            except Exception:
                pass

            _write_continuation_decision(
                workspace=workspace,
                decision="HUMAN_DECISION_REQUIRED",
                phase=state.get("currentPhase", "HUMAN_DECISION_REQUIRED"),
                task_id=task_id,
                run_id=run_id,
                justification="Human decision required; blocker(s) present",
                blockers_summary=blockers_summary,
            )

        elif action == "CONTINUE_LOOP":
            # Check backlog for READY tasks
            has_ready = False
            try:
                for task in read_jsonl(os.path.join(workspace, "state", "backlog.jsonl")):
                    if task.get("status") == "READY":
                        has_ready = True
                        break
            except Exception:
                pass

            if has_ready:
                _write_continuation_decision(
                    workspace=workspace,
                    decision="CONTINUE",
                    phase=state.get("currentPhase", "READY_FOR_NEXT_TASK"),
                    task_id=task_id,
                    run_id=run_id,
                    justification="READY tasks remain in backlog; continuing loop",
                )
            else:
                _write_continuation_decision(
                    workspace=workspace,
                    decision="SAFE_CHECKPOINT",
                    phase=state.get("currentPhase", "READY_FOR_NEXT_TASK"),
                    task_id=task_id,
                    run_id=run_id,
                    justification="No READY tasks in backlog; checkpoint reached",
                )

        # All other transitions (RUN_EXECUTOR, RUN_CHANGE_REVIEWER, etc.)
        # are transient — do NOT write a decision.

    except Exception as exc:
        print(
            f"Warning: auto-write continuation decision failed for "
            f"transition '{action}': {exc}",
            file=sys.stderr,
        )


def cmd_apply_transition(args):
    workspace = resolve_workspace(args.workspace)
    action = args.action
    task_id = args.task_id or ""

    if action not in _TRANSITIONS:
        print(f"Error: unsupported transition action '{action}'", file=sys.stderr)
        sys.exit(1)

    phase, requires_task, creates_run = _TRANSITIONS[action]

    if requires_task and not task_id:
        print(f"Error: '{action}' requires --task-id", file=sys.stderr)
        sys.exit(1)

    state = read_json(os.path.join(workspace, "state", "team-state.json"))
    now = utc_now_iso()
    run_id = ""

    # For transitions that preserve run/task identity, read from existing state
    if action in _TRANSITIONS_PRESERVE_IDENTITY:
        if not task_id:
            task_id = state.get("currentTaskId", "")
        if not run_id:
            run_id = state.get("currentRunId", "")

    # For task-scoped transitions, look up task in backlog
    if task_id:
        backlog = read_jsonl(os.path.join(workspace, "state", "backlog.jsonl"))
        for task in backlog:
            if task.get("taskId") == task_id:
                task["status"] = "IN_PROGRESS"
                write_json(os.path.join(workspace, "state", "current-task.json"), task)
                break
        backlog_path = os.path.join(workspace, "state", "backlog.jsonl")
        with open(backlog_path, "w", encoding="utf-8") as f:
            for t in backlog:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")

    # For transitions that create a run
    if creates_run:
        run_id = f"run-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')}-{os.getpid()}"
        run_dir = os.path.join(workspace, "runs", run_id)
        os.makedirs(run_dir, exist_ok=True)

        run_entry = {
            "schemaVersion": 1,
            "runId": run_id,
            "status": "IN_PROGRESS",
            "startedAtUtc": now,
            "taskId": task_id,
            "phase": phase,
            "result": "NEEDS_GATE"
        }
        append_jsonl(os.path.join(workspace, "state", "run-ledger.jsonl"), run_entry)

    # Clear stale current-task.json and identity for transitions that end the active task
    task_identity_cleared = False
    if phase in ("READY_FOR_NEXT_TASK", "SAFE_CHECKPOINT", "DONE"):
        ct_path = os.path.join(workspace, "state", "current-task.json")
        if os.path.exists(ct_path):
            os.remove(ct_path)
        state["currentTaskId"] = ""
        state["currentRunId"] = ""
        task_identity_cleared = True

    # Update state — preserve task/run identity for transitions that should not clear them
    state["currentPhase"] = phase
    state["currentTaskId"] = task_id
    state["currentRunId"] = run_id
    state["status"] = "IN_PROGRESS" if phase not in ("DONE", "SAFE_CHECKPOINT", "HUMAN_DECISION_REQUIRED") else phase
    state["updatedAtUtc"] = now

    # Re-clear identity if this transition ends the active task
    if task_identity_cleared:
        state["currentTaskId"] = ""
        state["currentRunId"] = ""

    write_json(os.path.join(workspace, "state", "team-state.json"), state)

    # ---- Auto-write continuation decision for terminal transitions ----
    _maybe_write_continuation_decision(workspace, action, state, task_id, run_id)

    # Append events
    event_types = {
        "RUN_EXECUTOR": "STATE_TRANSITION",
        "RUN_GATEKEEPER": "STATE_TRANSITION",
        "SET_SAFE_CHECKPOINT": "STATE_TRANSITION",
        "SET_HUMAN_REQUIRED": "STATE_TRANSITION",
        "SET_DONE": "STATE_TRANSITION",
    }
    evt_type = event_types.get(action, "STATE_TRANSITION")

    event = {
        "schemaVersion": 1,
        "eventId": f"evt-{os.getpid()}{int(datetime.datetime.now(datetime.timezone.utc).timestamp())}",
        "type": evt_type,
        "actor": "supervisor",
        "timestampUtc": now,
        "summary": f"Transitioned to {phase}",
        "taskId": task_id,
    }
    if run_id:
        event["runId"] = run_id
    append_jsonl(os.path.join(workspace, "state", "events.jsonl"), event)

    result = {
        "transitionApplied": True,
        "runId": run_id,
        "taskId": task_id,
        "phase": phase
    }
    print(json.dumps(result, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Command: write-event
# ---------------------------------------------------------------------------

def _load_event_types():
    """Load the canonical event type enum from schemas/event.schema.json.

    Returns a frozenset of valid event type strings.
    The schema is the single source of truth — no hardcoded duplication.
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    schema_path = os.path.join(project_root, "schemas", "event.schema.json")
    schema = read_json(schema_path)
    enum_values = schema.get("properties", {}).get("type", {}).get("enum", [])
    return frozenset(enum_values)


def cmd_write_event(args):
    workspace = resolve_workspace(args.workspace)
    events_file = os.path.join(workspace, "state", "events.jsonl")

    if not os.path.exists(events_file):
        print("Error: Events file not found. Run init-workspace first.", file=sys.stderr)
        sys.exit(1)

    # Validate event type against canonical enum from schema
    valid_types = _load_event_types()
    if args.type not in valid_types:
        print(
            f"Error: invalid event type '{args.type}'. "
            f"Valid types: {', '.join(sorted(valid_types))}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Count existing events
    existing = read_jsonl(events_file)
    counter = len(existing) + 1
    event_id = f"evt-{counter:06d}"
    now = utc_now_iso()

    event = {
        "schemaVersion": 1,
        "eventId": event_id,
        "type": args.type,
        "actor": args.actor,
        "timestampUtc": now,
        "summary": args.summary
    }

    if args.run_id:
        event["runId"] = args.run_id
    if args.task_id:
        event["taskId"] = args.task_id
    if args.data:
        try:
            event["data"] = json.loads(args.data)
        except json.JSONDecodeError:
            event["data"] = {"raw": args.data}

    append_jsonl(events_file, event)
    print(json.dumps(event, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Command: check-scope
# ---------------------------------------------------------------------------

def cmd_check_scope(args):
    workspace = resolve_workspace(args.workspace)

    scope_policy_path = os.path.join(workspace, "policies", "scope-policy.json")
    if not os.path.exists(scope_policy_path):
        print("Error: scope-policy.json not found", file=sys.stderr)
        sys.exit(1)

    scope_policy = read_json(scope_policy_path)
    state = read_json(os.path.join(workspace, "state", "team-state.json"))
    task_file = os.path.join(workspace, "state", "current-task.json")

    # Build allowed/forbidden lists
    always_allowed = scope_policy.get("alwaysAllowedWrites", [])
    always_forbidden = scope_policy.get("alwaysForbiddenWrites", [])
    default_allowed = scope_policy.get("defaultAllowedWrites", [])

    allowed = list(always_allowed) + list(default_allowed)
    forbidden = list(always_forbidden)

    if os.path.exists(task_file):
        task = read_json_file_safe(task_file)
        # Only apply task-scoped allowedWrites if the task is still active in team-state.
        # Stale current-task.json must not grant write privileges after task completion.
        if task and scope_policy.get("taskAllowedWritesOverride", True):
            state_task_id = state.get("currentTaskId", "")
            task_scoped_phases = frozenset([
                "EXECUTING_TASK", "NEEDS_CHANGE_REVIEW", "NEEDS_GATE",
                "REVIEW_FAILED", "GATE_FAILED"
            ])
            task_is_active = (
                state_task_id
                and task.get("taskId") == state_task_id
                and state.get("currentPhase", "") in task_scoped_phases
            )
            if task_is_active:
                if task.get("allowedWrites"):
                    allowed = list(always_allowed) + task["allowedWrites"]
                if task.get("forbiddenWrites"):
                    forbidden = forbidden + task["forbiddenWrites"]

    # Get git root
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10
        )
        git_root = result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        git_root = os.getcwd()

    # Get changed files
    changed_files = _get_git_changed_files(git_root)

    violations = []
    matched_allowed = []

    for cf in changed_files:
        in_allowed = False
        for pat in allowed:
            if _glob_match(cf, pat):
                in_allowed = True
                matched_allowed.append(cf)
                break

        in_forbidden = False
        for pat in forbidden:
            if _glob_match(cf, pat):
                violations.append({"file": cf, "reason": f"forbidden pattern: {pat}"})
                in_forbidden = True
                break

        if not in_allowed and not in_forbidden:
            violations.append({"file": cf, "reason": "outside allowed writes"})

    overall = "PASS" if not violations else "FAIL"
    summary = "All changes within scope" if not violations else f"{len(violations)} file(s) outside scope"

    result = {
        "schemaVersion": 1,
        "status": overall,
        "checks": [
            {
                "name": "scope",
                "status": overall,
                "summary": summary
            }
        ],
        "violations": violations
    }

    print(json.dumps(result, ensure_ascii=False))

    if overall == "FAIL":
        sys.exit(1)


def _get_git_changed_files(git_root):
    """Get all changed files from git status, including untracked, preserving spaces."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            capture_output=True, text=True, timeout=10,
            cwd=git_root
        )
        output = result.stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        return []

    files = []
    # -z gives null-separated entries. But porcelain v1 with -z still uses newlines within entries.
    # Parse line by line, handling the format.
    # With -z, entries are separated by null bytes.
    if "\0" in output:
        entries = output.split("\0")
    else:
        entries = output.split("\n")

    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue

        # porcelain v1 format: "XY path" or "XY oldpath -> newpath" or "R100 oldpath -> newpath"
        # First 2-3 chars are status, then space, then path
        if "-> " in entry:
            # Renamed/copied: "R100 old -> new" or "C100 old -> new"
            arrow_idx = entry.index(" -> ")
            file_path = entry[arrow_idx + 4:]
        elif len(entry) > 3 and entry[2] == ' ':
            file_path = entry[3:]
        elif len(entry) > 2:
            # Status char may be followed by a digit (for rename score)
            file_path = entry.lstrip("AMCDRUTC!?").lstrip("0123456789").strip()
        else:
            continue

        if file_path:
            # Make relative to git root if absolute
            if os.path.isabs(file_path):
                try:
                    file_path = os.path.relpath(file_path, git_root)
                except ValueError:
                    pass
            files.append(file_path)

    return files


def _glob_match(path, pattern):
    """Match a file path against a glob pattern like 'src/**' or '.teamloop/**'."""
    # Convert glob to regex
    regex = _glob_to_regex(pattern)
    return bool(re.match(regex, path))


def _glob_to_regex(pattern):
    """Convert a glob pattern to a regex pattern."""
    # Handle ** (match anything including /)
    # Handle * (match anything except /)
    # Escape regex special chars except * and ?
    i = 0
    n = len(pattern)
    result = "^"
    while i < n:
        c = pattern[i]
        if c == '*':
            if i + 1 < n and pattern[i + 1] == '*':
                # **
                if i + 2 < n and pattern[i + 2] == '/':
                    result += "(?:.*/)?"
                    i += 3
                    continue
                else:
                    result += ".*"
                    i += 2
                    continue
            else:
                result += "[^/]*"
        elif c == '?':
            result += "[^/]"
        elif c in '.+^$[]{}|()\\':
            result += '\\' + c
        else:
            result += c
        i += 1
    result += "$"
    return result


# ---------------------------------------------------------------------------
# Command: run-gates
# ---------------------------------------------------------------------------

def cmd_run_gates(args):
    workspace = resolve_workspace(args.workspace)

    gate_policy_path = os.path.join(workspace, "policies", "gate-policy.json")
    if not os.path.exists(gate_policy_path):
        print("Error: gate-policy.json not found", file=sys.stderr)
        sys.exit(1)

    state_path = os.path.join(workspace, "state", "team-state.json")
    state = read_json(state_path)

    run_id = state.get("currentRunId", "")
    task_id = state.get("currentTaskId", "")

    if not run_id:
        print("Error: No currentRunId in team-state. Start a run first.", file=sys.stderr)
        sys.exit(1)

    run_dir = os.path.join(workspace, "runs", run_id)
    os.makedirs(run_dir, exist_ok=True)

    gate_policy = read_json(gate_policy_path)
    gates = gate_policy.get("gates", [])

    checks = []
    overall = "PASS"
    has_required_fail = False
    next_action = ""

    for gate in gates:
        gate_name = gate.get("name", "")
        gate_type = gate.get("type", "")
        is_required = gate.get("required", True)
        timeout_sec = gate.get("timeoutSeconds", 300)

        check = {
            "name": gate_name,
        }

        if gate_type == "built-in" and gate_name == "scope":
            # Run check-scope from workspace parent so git root detection
            # anchors to the correct repository context.
            script_dir = os.path.dirname(os.path.abspath(__file__))
            core_script = os.path.join(script_dir, "teamloop-core.py")
            try:
                proc = subprocess.run(
                    [sys.executable, core_script, "check-scope", "--workspace", workspace],
                    capture_output=True, text=True, timeout=30,
                    cwd=os.path.dirname(os.path.abspath(workspace))
                )
                scope_result = json.loads(proc.stdout)
                check["status"] = scope_result.get("status", "ERROR")
                check["summary"] = scope_result.get("checks", [{}])[0].get("summary", "Scope check error")
            except (json.JSONDecodeError, subprocess.SubprocessError, FileNotFoundError) as e:
                check["status"] = "ERROR"
                check["summary"] = f"Scope check error: {e}"

        elif gate_type == "shell":
            command = gate.get("command", "")
            if not command:
                check["status"] = "SKIPPED"
                check["summary"] = "No command specified"
            else:
                check["command"] = command
                try:
                    proc = subprocess.run(
                        ["bash" if os.name != "nt" else "cmd.exe", "/c" if os.name == "nt" else "-c", command],
                        capture_output=True, text=True, timeout=timeout_sec,
                        cwd=os.getcwd()
                    )
                    check["exitCode"] = proc.returncode
                    check["stdout"] = proc.stdout[:4000] if proc.stdout else ""
                    check["stderr"] = proc.stderr[:4000] if proc.stderr else ""
                    if proc.returncode == 0:
                        check["status"] = "PASS"
                        check["summary"] = "Command succeeded"
                    else:
                        check["status"] = "FAIL"
                        check["summary"] = f"Exit code {proc.returncode}"
                except subprocess.TimeoutExpired:
                    check["status"] = "FAIL"
                    check["summary"] = f"Timeout after {timeout_sec}s"
                    check["exitCode"] = -1
                except FileNotFoundError as e:
                    check["status"] = "ERROR"
                    check["summary"] = f"Execution error: {e}"
                    check["exitCode"] = -1
                except subprocess.SubprocessError as e:
                    check["status"] = "ERROR"
                    check["summary"] = f"Execution error: {e}"
                    check["exitCode"] = -1
        elif gate_type == "built-in-guard":
            # Run guard integrity check as a built-in gate.
            # Delegates to the same logic as cmd_check_guard_integrity via
            # the subprocess interface so the result is captured in gate-result.json.
            script_dir = os.path.dirname(os.path.abspath(__file__))
            core_script = os.path.join(script_dir, "teamloop-core.py")
            try:
                proc = subprocess.run(
                    [sys.executable, core_script, "check-guard-integrity", "--workspace", workspace],
                    capture_output=True, text=True, timeout=30,
                    cwd=os.path.dirname(os.path.abspath(workspace))
                )
                try:
                    ghi_result = json.loads(proc.stdout)
                    ghi_status = ghi_result.get("status", "ERROR")
                    # Map guard-integrity status to gate check status
                    if ghi_status == "FAIL":
                        check["status"] = "FAIL"
                    elif ghi_status == "WARNING":
                        check["status"] = "PASS"
                        check["warning"] = True
                    else:
                        check["status"] = "PASS"
                    check["summary"] = f"Guard integrity: {ghi_status}"
                    check["details"] = ghi_result.get("checks", [])
                except json.JSONDecodeError:
                    check["status"] = "ERROR"
                    check["summary"] = "Guard integrity output not valid JSON"
            except subprocess.TimeoutExpired:
                check["status"] = "FAIL"
                check["summary"] = "Guard integrity check timed out after 30s"
            except FileNotFoundError as e:
                check["status"] = "ERROR"
                check["summary"] = f"Guard integrity error: {e}"
            except subprocess.SubprocessError as e:
                check["status"] = "ERROR"
                check["summary"] = f"Guard integrity error: {e}"
        else:
            check["status"] = "SKIPPED"
            check["summary"] = f"Unknown gate type: {gate_type}"

        checks.append(check)

        if check["status"] in ("FAIL", "ERROR") and is_required:
            has_required_fail = True

    if has_required_fail:
        overall = "FAIL"
        next_action = "FIX_GATE_FAILURE"

    gate_result = {
        "schemaVersion": 1,
        "runId": run_id,
        "taskId": task_id,
        "status": overall,
        "checks": checks,
        "nextAction": next_action,
        "humanRequired": False
    }

    write_json(os.path.join(run_dir, "gate-result.json"), gate_result)

    # Update team-state based on gate outcome
    if overall == "PASS":
        # Mark task as DONE in backlog
        backlog = read_jsonl(os.path.join(workspace, "state", "backlog.jsonl"))
        for task in backlog:
            if task.get("taskId") == task_id:
                task["status"] = "DONE"
                break
        backlog_path = os.path.join(workspace, "state", "backlog.jsonl")
        with open(backlog_path, "w", encoding="utf-8") as f:
            for t in backlog:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")

        # Mark run as COMPLETED in ledger
        ledger_path = os.path.join(workspace, "state", "run-ledger.jsonl")
        ledger = read_jsonl(ledger_path)
        for entry in ledger:
            if entry.get("runId") == run_id:
                entry["status"] = "COMPLETED"
                entry["result"] = "SAFE_CHECKPOINT"
                break
        with open(ledger_path, "w", encoding="utf-8") as f:
            for e in ledger:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

        state["currentPhase"] = "SAFE_CHECKPOINT"
        state["status"] = "IN_PROGRESS"
        state["lastGateStatus"] = "PASS"
        state["currentTaskId"] = ""
        state["currentRunId"] = ""
        state["updatedAtUtc"] = utc_now_iso()
        write_json(state_path, state)

        # Clear stale current-task.json after gate PASS
        ct_path = os.path.join(workspace, "state", "current-task.json")
        if os.path.exists(ct_path):
            os.remove(ct_path)

        # Append GATE_PASSED event
        event = {
            "schemaVersion": 1,
            "eventId": f"evt-{os.getpid()}{int(datetime.datetime.now(datetime.timezone.utc).timestamp())}",
            "type": "GATE_PASSED",
            "actor": "gatekeeper",
            "timestampUtc": utc_now_iso(),
            "summary": f"Gates passed for run {run_id}",
            "taskId": task_id,
            "runId": run_id
        }
        append_jsonl(os.path.join(workspace, "state", "events.jsonl"), event)

        # Auto-write SAFE_CHECKPOINT continuation decision on gate pass
        # Wrapped in try/except so a decision-write failure never breaks the gate command
        try:
            _write_continuation_decision(
                workspace=workspace,
                decision="SAFE_CHECKPOINT",
                phase="SAFE_CHECKPOINT",
                task_id=task_id,
                run_id=run_id,
                justification=f"Gates passed for run {run_id}",
            )
        except Exception as _exc:
            print(
                f"Warning: auto-write continuation decision failed after gate pass: {_exc}",
                file=sys.stderr,
            )
    else:
        # FAIL — transition to GATE_FAILED
        state["currentPhase"] = "GATE_FAILED"
        state["status"] = "IN_PROGRESS"
        state["lastGateStatus"] = "FAIL"
        state["updatedAtUtc"] = utc_now_iso()
        write_json(state_path, state)

        # Mark run as FAILED in ledger
        ledger_path = os.path.join(workspace, "state", "run-ledger.jsonl")
        ledger = read_jsonl(ledger_path)
        for entry in ledger:
            if entry.get("runId") == run_id:
                entry["status"] = "FAILED"
                entry["result"] = "FAILED"
                break
        with open(ledger_path, "w", encoding="utf-8") as f:
            for e in ledger:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

        # Append GATE_FAILED event
        event = {
            "schemaVersion": 1,
            "eventId": f"evt-{os.getpid()}{int(datetime.datetime.now(datetime.timezone.utc).timestamp())}",
            "type": "GATE_FAILED",
            "actor": "gatekeeper",
            "timestampUtc": utc_now_iso(),
            "summary": f"Gates failed for run {run_id}",
            "taskId": task_id,
            "runId": run_id
        }
        append_jsonl(os.path.join(workspace, "state", "events.jsonl"), event)

    print(json.dumps(gate_result, ensure_ascii=False))

    if overall == "FAIL":
        sys.exit(1)


# ---------------------------------------------------------------------------
# Command: validate-task (for test support)
# ---------------------------------------------------------------------------

def cmd_validate_task(args):
    """Validate a task JSON against the task schema."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    schema_path = os.path.join(project_root, "schemas", "task.schema.json")
    schema = read_json(schema_path)

    if args.json_file:
        task = read_json(args.json_file)
    else:
        task = json.loads(args.json_string)

    errors = validate_against_schema(task, schema, "task")
    if errors:
        print("TASK VALIDATION FAILED:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)
    print("TASK VALIDATION PASSED")


# ---------------------------------------------------------------------------
# Command: validate-research (for test support)
# ---------------------------------------------------------------------------

def cmd_validate_research(args):
    """Validate a research report inventory against the research-report schema."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    schema_path = os.path.join(project_root, "schemas", "research-report.schema.json")
    schema = read_json(schema_path)

    if args.json_file:
        report = read_json(args.json_file)
    else:
        report = json.loads(args.json_string)

    errors = validate_against_schema(report, schema, "research-report")
    if errors:
        print("RESEARCH VALIDATION FAILED:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)
    print("RESEARCH VALIDATION PASSED")


# ---------------------------------------------------------------------------
# Internal helper: _write_continuation_decision
# ---------------------------------------------------------------------------

def _write_continuation_decision(workspace, decision, phase, task_id="", run_id="",
                                 justification="", blockers_summary="", blocker_id="",
                                 evidence=None):
    """Write a continuation-decision.json record and append a STATE_TRANSITION event.

    This is the canonical internal entry point for writing continuation decisions.
    Both the CLI command (cmd_write_continuation_decision) and any future runtime
    callers (e.g., cmd_apply_transition) delegate to this function.

    Parameters
    ----------
    workspace : str
        Absolute path to the .teamloop workspace.
    decision : str
        One of: DONE, SAFE_CHECKPOINT, CONTINUE, HUMAN_DECISION_REQUIRED, BLOCKED.
    phase : str
        Current phase string (must be non-empty).
    task_id : str, optional
        Current task ID.
    run_id : str, optional
        Current run ID.
    justification : str, optional
        Human-readable justification. Auto-generated from decision+phase if empty.
    blockers_summary : str, optional
        Summary of blockers (added as extra check entry when present).
    blocker_id : str, optional
        Blocker identifier for BLOCKED/HUMAN_DECISION_REQUIRED decisions.
    evidence : list[str], optional
        List of evidence strings.

    Returns
    -------
    dict | None
        The written decision object on success, None on any error.
        Errors are logged to stderr; the caller is never aborted.
    """
    try:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # ---- load schema ----
        schema_path = os.path.join(project_root, "schemas", "continuation-decision.schema.json")
        schema = read_json(schema_path)

        # ---- validate decision enum from schema (single source of truth) ----
        valid_decisions = frozenset(
            schema.get("properties", {}).get("decision", {}).get("enum", [])
        )
        if decision not in valid_decisions:
            print(
                f"Warning: invalid decision '{decision}'. "
                f"Valid decisions: {', '.join(sorted(valid_decisions))}",
                file=sys.stderr,
            )
            return None

        if not phase:
            print("Warning: phase is required for continuation decision", file=sys.stderr)
            return None

        if not justification:
            justification = f"Decision {decision} recorded for phase {phase}"

        # ---- auto-generate baseline checks ----
        checks = [
            {
                "name": "decision-valid",
                "status": "PASS",
                "summary": f"Decision '{decision}' is a valid enum value"
            },
            {
                "name": "phase-set",
                "status": "PASS",
                "summary": f"Phase '{phase}' is set"
            },
        ]

        # If a blockers-summary was provided, add an extra check entry.
        if blockers_summary:
            checks.append({
                "name": "blockers-recorded",
                "status": "PASS",
                "summary": blockers_summary
            })

        now = utc_now_iso()

        decision_obj = {
            "schemaVersion": 1,
            "decision": decision,
            "phase": phase,
            "justification": justification,
            "checks": checks,
            "createdAtUtc": now,
        }
        if task_id:
            decision_obj["taskId"] = task_id
        if run_id:
            decision_obj["runId"] = run_id
        if blocker_id:
            decision_obj["blockerId"] = blocker_id
        if evidence:
            decision_obj["evidence"] = evidence

        # ---- write file ----
        decision_file = os.path.join(workspace, "state", "continuation-decision.json")
        write_json(decision_file, decision_obj)

        # ---- validate written file against schema ----
        written = read_json(decision_file)
        errors = validate_against_schema(written, schema, "continuation-decision.json")
        if errors:
            print("Warning: continuation-decision.json failed schema validation:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            return None

        # ---- append STATE_TRANSITION event ----
        events_file = os.path.join(workspace, "state", "events.jsonl")
        event = {
            "schemaVersion": 1,
            "eventId": f"evt-{os.getpid()}{int(datetime.datetime.now(datetime.timezone.utc).timestamp())}",
            "type": "STATE_TRANSITION",
            "actor": "executor",
            "timestampUtc": now,
            "summary": f"Continuation decision '{decision}' written for phase '{phase}'",
        }
        if task_id:
            event["taskId"] = task_id
        if run_id:
            event["runId"] = run_id
        append_jsonl(events_file, event)

        return decision_obj

    except Exception as exc:
        print(f"Warning: failed to write continuation decision: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Command: write-continuation-decision
# ---------------------------------------------------------------------------

def cmd_write_continuation_decision(args):
    """Write a continuation-decision.json record for the current run/task.

    Delegates to _write_continuation_decision() which contains the canonical
    logic for schema loading, validation, file writing, and event appending.

    Reads team-state.json to auto-populate phase, taskId, and runId when not
    supplied on the command line.  Auto-generates a minimal checks array so
    the file is always schema-valid.  Appends a STATE_TRANSITION event for
    auditability.
    """
    workspace = resolve_workspace(args.workspace)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # ---- validate decision enum from schema (CLI must fail hard) ----
    schema_path = os.path.join(project_root, "schemas", "continuation-decision.schema.json")
    schema = read_json(schema_path)
    valid_decisions = frozenset(
        schema.get("properties", {}).get("decision", {}).get("enum", [])
    )
    if args.decision not in valid_decisions:
        print(
            f"Error: invalid decision '{args.decision}'. "
            f"Valid decisions: {', '.join(sorted(valid_decisions))}",
            file=sys.stderr,
        )
        sys.exit(1)

    # ---- auto-populate phase / taskId / runId from team-state ----
    state_file = os.path.join(workspace, "state", "team-state.json")
    state = read_json_file_safe(state_file) or {}

    phase = args.phase or state.get("currentPhase", "")
    task_id = args.task_id or state.get("currentTaskId", "")
    run_id = args.run_id or state.get("currentRunId", "")

    if not phase:
        print("Error: --phase is required (not set on CLI or in team-state.json)", file=sys.stderr)
        sys.exit(1)

    # ---- delegate to internal helper ----
    result = _write_continuation_decision(
        workspace=workspace,
        decision=args.decision,
        phase=phase,
        task_id=task_id,
        run_id=run_id,
        justification=args.justification or "",
        blockers_summary=args.blockers_summary or "",
        blocker_id=args.blocker_id or "",
        evidence=args.evidence if args.evidence else None,
    )

    if result is None:
        print("Error: _write_continuation_decision returned None (see stderr for details)", file=sys.stderr)
        sys.exit(1)

    # ---- output ----
    print(json.dumps(result, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Command: check-guard-integrity
# ---------------------------------------------------------------------------

def cmd_check_guard_integrity(args):
    """Check guard integrity for the workspace.

    Performs three checks:
      1. Protected path modifications — loads .teamloop/policies/protected-paths.json
         and compares git status against protectedPaths glob patterns.
      2. Dangerous operations — detects test file deletion, gate-policy modification,
         and schema file deletion from git status.
      3. Schema integrity — verifies all .json files in schemas/ are valid parseable JSON.

    Outputs structured JSON to stdout.  Exit 0 for PASS/WARNING, exit 1 for FAIL
    (unless enforcementLevel is 'warn', in which case always exit 0).
    """
    workspace = resolve_workspace(args.workspace)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    checks = []
    violations = []

    # ------------------------------------------------------------------
    # Determine enforcement level from protected-paths policy (if present)
    # ------------------------------------------------------------------
    enforcement_level = "error"  # default
    policy_loaded = False

    policy_path = os.path.join(workspace, "policies", "protected-paths.json")
    policy = None
    if os.path.exists(policy_path):
        policy = read_json_file_safe(policy_path)
        if policy is not None:
            policy_loaded = True
            enforcement_level = policy.get("enforcementLevel", "error")

    # ------------------------------------------------------------------
    # Get git changed files
    # ------------------------------------------------------------------
    git_status_entries = _get_git_status_entries()

    # ------------------------------------------------------------------
    # Check 1: protected-paths
    # ------------------------------------------------------------------
    pp_check, pp_violations = _check_protected_paths(
        policy, git_status_entries, workspace
    )
    checks.append(pp_check)
    violations.extend(pp_violations)

    # ------------------------------------------------------------------
    # Check 2: dangerous-operations
    # ------------------------------------------------------------------
    do_check, do_violations = _check_dangerous_operations(git_status_entries)
    checks.append(do_check)
    violations.extend(do_violations)

    # ------------------------------------------------------------------
    # Check 3: schema-integrity
    # ------------------------------------------------------------------
    si_check, si_violations = _check_schema_integrity(project_root)
    checks.append(si_check)
    violations.extend(si_violations)

    # ------------------------------------------------------------------
    # Compute overall status
    # ------------------------------------------------------------------
    has_fail = any(c["status"] == "FAIL" for c in checks)
    has_warn = any(c["status"] == "WARNING" for c in checks)

    if has_fail:
        overall_status = "FAIL"
    elif has_warn:
        overall_status = "WARNING"
    else:
        overall_status = "PASS"

    result = {
        "schemaVersion": 1,
        "status": overall_status,
        "checks": checks,
        "violations": violations,
    }

    if not policy_loaded:
        result["note"] = "protected-paths.json not found; policy is not configured"

    print(json.dumps(result, ensure_ascii=False))

    # Exit code: 1 for FAIL (unless enforcementLevel is 'warn')
    if overall_status == "FAIL" and enforcement_level != "warn":
        sys.exit(1)


def _get_git_status_entries():
    """Parse git status --porcelain into list of {status, path} dicts.

    Each entry has:
      - status: the porcelain status string (e.g. 'M ', 'D ', '??')
      - path: the file path relative to git root
      - raw: the raw porcelain line
    """
    entries = []
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1"],
            capture_output=True, text=True, timeout=10,
        )
        git_root_result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
        git_root = git_root_result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return entries

    for line in result.stdout.split("\n"):
        line = line.strip()
        if not line:
            continue

        # porcelain v1: "XY path" or "XY old -> new"
        status = line[:2]
        path_part = line[3:] if len(line) > 3 else ""

        if "-> " in path_part:
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

        entries.append({"status": status, "path": path_part, "raw": line})

    return entries


def _check_protected_paths(policy, git_status_entries, workspace):
    """Check 1: protected path modifications.

    Returns (check_dict, violations_list).
    """
    violations = []
    details = "no modifications"

    if policy is None:
        return {
            "name": "protected-paths",
            "status": "PASS",
            "details": "policy not configured (protected-paths.json missing)",
        }, []

    protected_patterns = policy.get("protectedPaths", [])
    if not protected_patterns:
        return {
            "name": "protected-paths",
            "status": "PASS",
            "details": "no protected paths configured",
        }, []

    # Find changed files that match protected patterns
    matched_files = []
    for entry in git_status_entries:
        path = entry["path"]
        for pat in protected_patterns:
            if _glob_match(path, pat):
                matched_files.append({
                    "path": path,
                    "git_status": entry["status"],
                    "matched_pattern": pat,
                })
                break

    if matched_files:
        details = f"{len(matched_files)} protected path(s) modified"
        violations.append({
            "check": "protected-paths",
            "paths": [m["path"] for m in matched_files],
            "detail": details,
        })
        return {
            "name": "protected-paths",
            "status": "FAIL",
            "details": details,
        }, violations

    return {
        "name": "protected-paths",
        "status": "PASS",
        "details": details,
    }, []


def _check_dangerous_operations(git_status_entries):
    """Check 2: dangerous operations detection.

    Detects:
      - Test file deletion (files in tests/ showing as deleted)
      - Gate policy modification (.teamloop/policies/gate-policy.json modified)
      - Schema file deletion (files in schemas/ deleted)

    Returns (check_dict, violations_list).
    """
    violations = []

    for entry in git_status_entries:
        status = entry["status"]
        path = entry["path"]

        # Is file deleted? (status starts with 'D')
        is_deleted = status[0] == "D"
        # Is file modified? (status starts with 'M' or 'A')
        is_modified = status[0] in ("M", "A", "R", "C", "T")

        # Test file deletion
        if is_deleted and (path.startswith("tests/") or path.startswith("tests\\")):
            violations.append({
                "check": "dangerous-operations",
                "type": "test-file-deleted",
                "path": path,
                "detail": f"Test file deleted: {path}",
            })

        # Gate policy modification
        if is_modified and path in (".teamloop/policies/gate-policy.json", ".teamloop\\policies\\gate-policy.json"):
            violations.append({
                "check": "dangerous-operations",
                "type": "gate-policy-modified",
                "path": path,
                "detail": f"Gate policy modified: {path}",
            })

        # Schema file deletion
        if is_deleted and (path.startswith("schemas/") or path.startswith("schemas\\")):
            violations.append({
                "check": "dangerous-operations",
                "type": "schema-file-deleted",
                "path": path,
                "detail": f"Schema file deleted: {path}",
            })

    if violations:
        return {
            "name": "dangerous-operations",
            "status": "FAIL",
            "details": f"{len(violations)} dangerous operation(s) detected",
        }, violations

    return {
        "name": "dangerous-operations",
        "status": "PASS",
        "details": "none detected",
    }, []


def _check_schema_integrity(project_root):
    """Check 3: schema integrity.

    Verifies all .json files in schemas/ are valid parseable JSON.

    Returns (check_dict, violations_list).
    """
    violations = []
    schemas_dir = os.path.join(project_root, "schemas")

    if not os.path.isdir(schemas_dir):
        return {
            "name": "schema-integrity",
            "status": "PASS",
            "details": "schemas directory not found (nothing to check)",
        }, []

    schema_files_checked = 0
    for name in sorted(os.listdir(schemas_dir)):
        if not name.endswith(".json"):
            continue
        schema_files_checked += 1
        fpath = os.path.join(schemas_dir, name)
        if is_invalid_json_file(fpath):
            violations.append({
                "check": "schema-integrity",
                "type": "invalid-json",
                "path": f"schemas/{name}",
                "detail": f"Schema file contains invalid JSON: {name}",
            })

    if violations:
        return {
            "name": "schema-integrity",
            "status": "FAIL",
            "details": f"{len(violations)} schema file(s) with invalid JSON",
        }, violations

    return {
        "name": "schema-integrity",
        "status": "PASS",
        "details": f"all {schema_files_checked} schema(s) valid",
    }, []


# ---------------------------------------------------------------------------
# Command: run-sentinel
# ---------------------------------------------------------------------------

# Known valid phase values (single source of truth for sentinel check #8).
_KNOWN_PHASES = frozenset([
    "",  # fresh workspace
    "NEEDS_DISCOVERY",
    "NEEDS_RESEARCH",
    "NEEDS_RESEARCH_REVIEW",
    "NEEDS_TASK_SLICING",
    "EXECUTING_TASK",
    "NEEDS_CHANGE_REVIEW",
    "NEEDS_GATE",
    "GATE_FAILED",
    "REVIEW_FAILED",
    "SAFE_CHECKPOINT",
    "READY_FOR_NEXT_TASK",
    "HUMAN_DECISION_REQUIRED",
    "DONE",
])

# Baseline forbiddenWrites every scope-policy.json must contain.
_SCOPE_POLICY_BASELINE_FORBIDDEN = [".git/**", "node_modules/**"]


def _sentinel_get_run_id(workspace):
    """Return a runId string. Prefers currentRunId from team-state, else creates one."""
    state = read_json_file_safe(os.path.join(workspace, "state", "team-state.json"))
    if state and state.get("currentRunId"):
        return state["currentRunId"]
    return "run-{}".format(datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S"))


def _sentinel_check_scope_policy_weakening(workspace):
    """Check 1: scope-policy-weakening — scope-policy.json must have baseline forbiddenWrites."""
    policy_path = os.path.join(workspace, "policies", "scope-policy.json")
    if not os.path.exists(policy_path):
        return {
            "category": "scope-policy-weakening",
            "severity": "CRITICAL",
            "title": "Scope policy file missing",
            "description": "scope-policy.json not found — no write guards in place",
            "evidence": [{"type": "MISSING_ARTIFACT", "detail": policy_path}],
            "resolutionHint": "Run init-workspace to restore scope-policy.json",
        }

    policy = read_json_file_safe(policy_path)
    if policy is None:
        return {
            "category": "scope-policy-weakening",
            "severity": "CRITICAL",
            "title": "Scope policy file is invalid JSON",
            "description": "scope-policy.json exists but cannot be parsed",
            "evidence": [{"type": "FILE_PATH", "detail": policy_path}],
            "resolutionHint": "Restore scope-policy.json with valid JSON",
        }

    # Check all forbidden-write field variants
    all_forbidden = []
    for key in ("forbiddenWrites", "alwaysForbiddenWrites"):
        val = policy.get(key, [])
        if isinstance(val, list):
            all_forbidden.extend(val)

    missing_baseline = []
    for baseline_path in _SCOPE_POLICY_BASELINE_FORBIDDEN:
        if baseline_path not in all_forbidden:
            missing_baseline.append(baseline_path)

    if missing_baseline:
        return {
            "category": "scope-policy-weakening",
            "severity": "CRITICAL",
            "title": "Scope policy weakened — baseline forbidden writes missing",
            "description": "scope-policy.json is missing baseline forbiddenWrites: {}".format(
                ", ".join(missing_baseline)
            ),
            "evidence": [
                {"type": "SCHEMA_FIELD", "detail": "forbiddenWrites missing: " + ", ".join(missing_baseline)},
                {"type": "FILE_PATH", "detail": policy_path},
            ],
            "resolutionHint": "Add baseline paths to forbiddenWrites in scope-policy.json",
        }

    return {
        "category": "scope-policy-weakening",
        "severity": "INFO",
        "title": "Scope policy has baseline forbidden writes",
        "description": "scope-policy.json contains required baseline forbidden writes",
        "evidence": [{"type": "FILE_PATH", "detail": policy_path}],
    }


def _sentinel_check_gate_policy_weakening(workspace):
    """Check 2: gate-policy-weakening — gate-policy.json must have at least one gate."""
    policy_path = os.path.join(workspace, "policies", "gate-policy.json")
    if not os.path.exists(policy_path):
        return {
            "category": "gate-policy-weakening",
            "severity": "WARNING",
            "title": "Gate policy file missing",
            "description": "gate-policy.json not found — no gate checks configured",
            "evidence": [{"type": "MISSING_ARTIFACT", "detail": policy_path}],
            "resolutionHint": "Run init-workspace to restore gate-policy.json",
        }

    policy = read_json_file_safe(policy_path)
    if policy is None:
        return {
            "category": "gate-policy-weakening",
            "severity": "WARNING",
            "title": "Gate policy file is invalid JSON",
            "description": "gate-policy.json exists but cannot be parsed",
            "evidence": [{"type": "FILE_PATH", "detail": policy_path}],
            "resolutionHint": "Restore gate-policy.json with valid JSON",
        }

    gates = policy.get("gates", [])
    if not gates or not isinstance(gates, list) or len(gates) == 0:
        return {
            "category": "gate-policy-weakening",
            "severity": "WARNING",
            "title": "No gates configured",
            "description": "gate-policy.json has no gates — all changes pass without verification",
            "evidence": [
                {"type": "SCHEMA_FIELD", "detail": "gates array is empty"},
                {"type": "FILE_PATH", "detail": policy_path},
            ],
            "resolutionHint": "Add at least one gate to gate-policy.json",
        }

    return {
        "category": "gate-policy-weakening",
        "severity": "INFO",
        "title": "Gate policy has {} gate(s)".format(len(gates)),
        "description": "gate-policy.json contains {} gate definition(s)".format(len(gates)),
        "evidence": [{"type": "FILE_PATH", "detail": policy_path}],
    }


def _sentinel_check_schema_integrity(workspace):
    """Check 3: schema-integrity — every schemas/*.schema.json must be valid JSON."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    schemas_dir = os.path.join(project_root, "schemas")
    invalid_files = []

    if not os.path.isdir(schemas_dir):
        return {
            "category": "schema-integrity",
            "severity": "INFO",
            "title": "Schemas directory not found",
            "description": "schemas/ directory does not exist — nothing to check",
            "evidence": [{"type": "MISSING_ARTIFACT", "detail": schemas_dir}],
        }

    for name in sorted(os.listdir(schemas_dir)):
        if not name.endswith(".schema.json"):
            continue
        fpath = os.path.join(schemas_dir, name)
        if is_invalid_json_file(fpath):
            invalid_files.append("schemas/{}".format(name))

    if invalid_files:
        return {
            "category": "schema-integrity",
            "severity": "CRITICAL",
            "title": "Schema files contain invalid JSON",
            "description": "{} schema file(s) with invalid JSON".format(len(invalid_files)),
            "evidence": [{"type": "FILE_PATH", "detail": p} for p in invalid_files],
            "resolutionHint": "Fix JSON syntax in affected schema files",
        }

    return {
        "category": "schema-integrity",
        "severity": "INFO",
        "title": "All schema files are valid JSON",
        "description": "All .schema.json files in schemas/ parsed without errors",
        "evidence": [{"type": "FILE_PATH", "detail": schemas_dir}],
    }


def _sentinel_check_test_suppression(workspace):
    """Check 4: test-suppression — test runner scripts must exist and be non-empty."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    missing_or_empty = []

    for script_name in ("tests/run-tests.sh", "tests/run-tests.ps1"):
        spath = os.path.join(project_root, script_name)
        if not os.path.exists(spath):
            missing_or_empty.append(script_name)
        elif os.path.getsize(spath) == 0:
            missing_or_empty.append(script_name)

    if missing_or_empty:
        return {
            "category": "test-suppression",
            "severity": "CRITICAL",
            "title": "Test runner script(s) missing or empty",
            "description": "Test runner script(s) missing or empty: {}".format(
                ", ".join(missing_or_empty)
            ),
            "evidence": [{"type": "MISSING_ARTIFACT", "detail": p} for p in missing_or_empty],
            "resolutionHint": "Restore test runner scripts to the tests/ directory",
        }

    return {
        "category": "test-suppression",
        "severity": "INFO",
        "title": "Test runner scripts present",
        "description": "Both tests/run-tests.sh and tests/run-tests.ps1 exist and are non-empty",
        "evidence": [
            {"type": "FILE_PATH", "detail": os.path.join(project_root, "tests/run-tests.sh")},
            {"type": "FILE_PATH", "detail": os.path.join(project_root, "tests/run-tests.ps1")},
        ],
    }


def _sentinel_check_state_mutation(workspace):
    """Check 5: state-mutation — core state files must be valid JSON."""
    invalid_files = []

    # team-state.json is a single JSON object
    ts_path = os.path.join(workspace, "state", "team-state.json")
    if os.path.exists(ts_path) and os.path.getsize(ts_path) > 0:
        if is_invalid_json_file(ts_path):
            invalid_files.append("state/team-state.json")

    # JSONL files — check each line is valid JSON
    for name in ("events.jsonl", "backlog.jsonl"):
        jpath = os.path.join(workspace, "state", name)
        if not os.path.exists(jpath) or os.path.getsize(jpath) == 0:
            continue
        # Check if the file can be read as valid JSONL
        is_valid = True
        for enc in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
            try:
                with open(jpath, "r", encoding=enc) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            json.loads(line)
                is_valid = True
                break
            except (UnicodeDecodeError, ValueError):
                is_valid = False
                continue
        if not is_valid:
            invalid_files.append("state/{}".format(name))

    if invalid_files:
        return {
            "category": "state-mutation",
            "severity": "CRITICAL",
            "title": "State file(s) contain invalid JSON",
            "description": "State file(s) corrupted or truncated: {}".format(
                ", ".join(invalid_files)
            ),
            "evidence": [{"type": "FILE_PATH", "detail": p} for p in invalid_files],
            "resolutionHint": "Restore state files from git or reinitialize workspace",
        }

    return {
        "category": "state-mutation",
        "severity": "INFO",
        "title": "All state files are valid JSON",
        "description": "Core state files (team-state.json, events.jsonl, backlog.jsonl) are valid",
        "evidence": [{"type": "FILE_PATH", "detail": os.path.join(workspace, "state")}],
    }


def _sentinel_check_protected_file_changes(workspace):
    """Check 6: protected-file-changes — reuse guard integrity check infrastructure."""
    # Reuse _get_git_status_entries from guard integrity
    git_status_entries = _get_git_status_entries()

    if not git_status_entries:
        return {
            "category": "protected-file-changes",
            "severity": "INFO",
            "title": "No git changes detected",
            "description": "Git status is clean — no file modifications detected",
            "evidence": [{"type": "GIT_DIFF", "detail": "no changes"}],
        }

    # Load protected-paths policy if available
    policy_path = os.path.join(workspace, "policies", "protected-paths.json")
    policy = None
    if os.path.exists(policy_path):
        policy = read_json_file_safe(policy_path)

    # Run the same protected-paths check used by guard integrity
    _, violations = _check_protected_paths(policy, git_status_entries, workspace)

    # Also check for dangerous operations
    _, do_violations = _check_dangerous_operations(git_status_entries)
    all_violations = violations + do_violations

    if all_violations:
        paths = []
        for v in all_violations:
            if "paths" in v:
                paths.extend(v["paths"])
            elif "path" in v:
                paths.append(v["path"])
        return {
            "category": "protected-file-changes",
            "severity": "WARNING",
            "title": "Protected file changes detected",
            "description": "{} file change(s) matching protected patterns".format(len(paths)),
            "evidence": [{"type": "GIT_DIFF", "detail": p} for p in paths],
            "resolutionHint": "Review changed protected files and revert if unintended",
        }

    # Report changed files as info
    changed = [e["path"] for e in git_status_entries]
    return {
        "category": "protected-file-changes",
        "severity": "INFO",
        "title": "{} file(s) changed, none protected".format(len(changed)),
        "description": "Git shows {} modified file(s), none match protected patterns".format(len(changed)),
        "evidence": [{"type": "GIT_DIFF", "detail": p} for p in changed[:5]],
    }


def _sentinel_check_hidden_unresolved_work(workspace):
    """Check 7: hidden-unresolved-work — READY tasks that may be orphaned."""
    backlog_path = os.path.join(workspace, "state", "backlog.jsonl")
    if not os.path.exists(backlog_path):
        return {
            "category": "hidden-unresolved-work",
            "severity": "INFO",
            "title": "No backlog file found",
            "description": "backlog.jsonl not found — cannot check for orphaned work",
            "evidence": [{"type": "MISSING_ARTIFACT", "detail": backlog_path}],
        }

    try:
        backlog = read_jsonl(backlog_path)
    except (json.JSONDecodeError, ValueError):
        return {
            "category": "hidden-unresolved-work",
            "severity": "INFO",
            "title": "Backlog file is unreadable",
            "description": "backlog.jsonl cannot be parsed — skipping orphaned work check",
            "evidence": [{"type": "FILE_PATH", "detail": backlog_path}],
        }

    ready_tasks = [t for t in backlog if t.get("status") == "READY"]

    if ready_tasks:
        task_ids = [t.get("taskId", "unknown") for t in ready_tasks]
        return {
            "category": "hidden-unresolved-work",
            "severity": "INFO",
            "title": "{} READY task(s) in backlog".format(len(ready_tasks)),
            "description": "Found {} READY task(s) that have not been picked up: {}".format(
                len(ready_tasks), ", ".join(task_ids)
            ),
            "evidence": [{"type": "STATE_FIELD", "detail": "taskId: {}, status: READY".format(tid)} for tid in task_ids],
        }

    return {
        "category": "hidden-unresolved-work",
        "severity": "INFO",
        "title": "No orphaned READY tasks",
        "description": "All tasks in backlog are processed (no READY tasks waiting)",
        "evidence": [{"type": "STATE_FIELD", "detail": "backlog has {} task(s), 0 READY".format(len(backlog))}],
    }


def _sentinel_check_manual_state_mutation(workspace):
    """Check 8: manual-state-mutation — team-state.json phase must be a known value."""
    state_path = os.path.join(workspace, "state", "team-state.json")
    if not os.path.exists(state_path):
        return {
            "category": "manual-state-mutation",
            "severity": "CRITICAL",
            "title": "Team state file missing",
            "description": "team-state.json not found — workspace state is unknown",
            "evidence": [{"type": "MISSING_ARTIFACT", "detail": state_path}],
            "resolutionHint": "Run init-workspace to restore team-state.json",
        }

    state = read_json_file_safe(state_path)
    if state is None:
        return {
            "category": "manual-state-mutation",
            "severity": "CRITICAL",
            "title": "Team state file is invalid JSON",
            "description": "team-state.json exists but cannot be parsed as JSON",
            "evidence": [{"type": "FILE_PATH", "detail": state_path}],
            "resolutionHint": "Restore team-state.json from git or reinitialize workspace",
        }

    phase = state.get("currentPhase", "")
    if phase not in _KNOWN_PHASES:
        return {
            "category": "manual-state-mutation",
            "severity": "CRITICAL",
            "title": "Invalid phase value in team-state",
            "description": "currentPhase '{}' is not a known phase value. Known phases: {}".format(
                phase, ", ".join(sorted(_KNOWN_PHASES - {""}))
            ),
            "evidence": [
                {"type": "STATE_FIELD", "detail": "currentPhase: {}".format(phase)},
                {"type": "FILE_PATH", "detail": state_path},
            ],
            "resolutionHint": "Restore currentPhase to a valid phase value",
        }

    return {
        "category": "manual-state-mutation",
        "severity": "INFO",
        "title": "Phase '{}' is valid".format(phase or "(empty)"),
        "description": "team-state.json has a valid currentPhase value",
        "evidence": [{"type": "STATE_FIELD", "detail": "currentPhase: {}".format(phase)}],
    }


def _sentinel_check_evidence_manipulation(workspace):
    """Check 9: evidence-manipulation — evidence refs in continuation-decision.json must point to existing files."""
    decision_path = os.path.join(workspace, "state", "continuation-decision.json")
    if not os.path.exists(decision_path):
        return {
            "category": "evidence-manipulation",
            "severity": "INFO",
            "title": "No continuation decision file",
            "description": "continuation-decision.json not found — no evidence references to check",
            "evidence": [{"type": "MISSING_ARTIFACT", "detail": decision_path}],
        }

    decision = read_json_file_safe(decision_path)
    if decision is None:
        return {
            "category": "evidence-manipulation",
            "severity": "INFO",
            "title": "Continuation decision file is unreadable",
            "description": "continuation-decision.json exists but cannot be parsed — skipping evidence check",
            "evidence": [{"type": "FILE_PATH", "detail": decision_path}],
        }

    # Collect evidence references — could be in various fields
    evidence_refs = []
    for field in ("evidence", "evidenceIds", "checkEvidence"):
        val = decision.get(field, None)
        if isinstance(val, list):
            evidence_refs.extend(val)
        elif isinstance(val, str) and val:
            evidence_refs.append(val)

    # Also check check entries for evidence paths
    checks = decision.get("checks", [])
    if isinstance(checks, list):
        for check in checks:
            if isinstance(check, dict):
                for ev_field in ("evidence", "evidencePath", "artifactPath"):
                    val = check.get(ev_field, None)
                    if isinstance(val, list):
                        evidence_refs.extend(val)
                    elif isinstance(val, str) and val:
                        evidence_refs.append(val)

    if not evidence_refs:
        return {
            "category": "evidence-manipulation",
            "severity": "INFO",
            "title": "No evidence references in continuation decision",
            "description": "continuation-decision.json contains no file evidence references",
            "evidence": [{"type": "FILE_PATH", "detail": decision_path}],
        }

    # Resolve workspace root for relative path checking
    workspace_root = os.path.dirname(workspace)
    missing_refs = []
    for ref in evidence_refs:
        # Try as relative to workspace root first, then workspace itself
        candidate = os.path.join(workspace_root, ref)
        if not os.path.exists(candidate):
            candidate = os.path.join(workspace, ref)
        if not os.path.exists(candidate):
            # Could be an absolute path or a ref to a non-file entity — only flag if looks like a path
            if "/" in ref or "\\" in ref:
                missing_refs.append(ref)

    if missing_refs:
        return {
            "category": "evidence-manipulation",
            "severity": "WARNING",
            "title": "Missing evidence references in continuation decision",
            "description": "{} evidence reference(s) in continuation-decision.json point to missing files".format(
                len(missing_refs)
            ),
            "evidence": [{"type": "MISSING_ARTIFACT", "detail": ref} for ref in missing_refs],
            "resolutionHint": "Ensure referenced evidence files exist or update continuation-decision.json",
        }

    return {
        "category": "evidence-manipulation",
        "severity": "INFO",
        "title": "All evidence references resolve",
        "description": "{} evidence reference(s) in continuation-decision.json all point to existing files".format(
            len(evidence_refs)
        ),
        "evidence": [{"type": "FILE_PATH", "detail": decision_path}],
    }


def cmd_run_sentinel(args):
    """READ-ONLY sentinel inspection command.

    Runs 9 integrity checks on the workspace and produces a structured JSON
    report matching schemas/sentinel-inspection.schema.json.

    Does not modify any files except writing its own report to
    .teamloop/runs/<run-id>/sentinel-inspection.json.
    """
    workspace = resolve_workspace(args.workspace)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Determine runId
    run_id = _sentinel_get_run_id(workspace)

    # Ensure the run directory exists for the report
    run_dir = os.path.join(workspace, "runs", run_id)
    os.makedirs(run_dir, exist_ok=True)

    # Run all 9 checks
    findings = [
        _sentinel_check_scope_policy_weakening(workspace),
        _sentinel_check_gate_policy_weakening(workspace),
        _sentinel_check_schema_integrity(workspace),
        _sentinel_check_test_suppression(workspace),
        _sentinel_check_state_mutation(workspace),
        _sentinel_check_protected_file_changes(workspace),
        _sentinel_check_hidden_unresolved_work(workspace),
        _sentinel_check_manual_state_mutation(workspace),
        _sentinel_check_evidence_manipulation(workspace),
    ]

    # Compute counts for summary
    critical_count = sum(1 for f in findings if f["severity"] == "CRITICAL")
    warning_count = sum(1 for f in findings if f["severity"] == "WARNING")
    info_count = sum(1 for f in findings if f["severity"] == "INFO")

    # Determine overall status
    if critical_count > 0:
        overall_status = "FAIL"
    elif warning_count > 0:
        overall_status = "WARNING"
    else:
        overall_status = "PASS"

    report = {
        "schemaVersion": 1,
        "runId": run_id,
        "inspectedAtUtc": utc_now_iso(),
        "findings": findings,
        "overallStatus": overall_status,
        "summary": {
            "totalFindings": len(findings),
            "criticalCount": critical_count,
            "warningCount": warning_count,
            "infoCount": info_count,
        },
    }

    # Write report to run directory
    report_path = os.path.join(run_dir, "sentinel-inspection.json")
    write_json(report_path, report)

    # Print to stdout
    print(json.dumps(report, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Workspace path resolution
# ---------------------------------------------------------------------------

def resolve_workspace(workspace):
    if os.path.isabs(workspace):
        return workspace
    return os.path.join(os.getcwd(), workspace)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="TeamLoop Harness Core")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # init-workspace
    p_init = subparsers.add_parser("init-workspace", help="Initialize a new workspace")
    p_init.add_argument("--workspace", "-w", default=".teamloop")
    p_init.add_argument("--profile", "-p", default="generic-software-task")

    # validate-state
    p_validate = subparsers.add_parser("validate-state", help="Validate workspace state")
    p_validate.add_argument("--workspace", "-w", default=".teamloop")

    # next-action
    p_next = subparsers.add_parser("next-action", help="Compute next action")
    p_next.add_argument("--workspace", "-w", default=".teamloop")

    # apply-transition
    p_apply = subparsers.add_parser("apply-transition", help="Apply a state transition")
    p_apply.add_argument("--workspace", "-w", default=".teamloop")
    p_apply.add_argument("--action", required=True, help="Transition action (e.g., RUN_EXECUTOR)")
    p_apply.add_argument("--task-id", default="", help="Task ID for the transition")

    # write-event
    p_event = subparsers.add_parser("write-event", help="Append an event")
    p_event.add_argument("--workspace", "-w", default=".teamloop")
    p_event.add_argument("--type", "-t", required=True)
    p_event.add_argument("--actor", "-a", required=True)
    p_event.add_argument("--summary", "-s", required=True)
    p_event.add_argument("--run-id", default="")
    p_event.add_argument("--task-id", default="")
    p_event.add_argument("--data", default="")

    # check-scope
    p_scope = subparsers.add_parser("check-scope", help="Check git changes against scope policy")
    p_scope.add_argument("--workspace", "-w", default=".teamloop")

    # run-gates
    p_gates = subparsers.add_parser("run-gates", help="Run gate checks")
    p_gates.add_argument("--workspace", "-w", default=".teamloop")

    # validate-task
    p_vtask = subparsers.add_parser("validate-task", help="Validate a task JSON")
    p_vtask.add_argument("--json-file", default="")
    p_vtask.add_argument("--json-string", default="")
    p_vtask.add_argument("--workspace", "-w", default="", help="Ignored, for wrapper compatibility")

    # validate-research
    p_vresearch = subparsers.add_parser("validate-research", help="Validate a research report inventory")
    p_vresearch.add_argument("--json-file", default="")
    p_vresearch.add_argument("--json-string", default="")
    p_vresearch.add_argument("--workspace", "-w", default="", help="Ignored, for wrapper compatibility")

    # memory-doctor
    p_mdoctor = subparsers.add_parser("memory-doctor", help="Validate memory JSONL files and report findings")
    p_mdoctor.add_argument("--workspace", "-w", default=".teamloop")

    # check-guard-integrity
    p_ghi = subparsers.add_parser("check-guard-integrity", help="Check guard integrity for protected paths, dangerous operations, and schema validity")
    p_ghi.add_argument("--workspace", "-w", default=".teamloop")

    # write-continuation-decision
    p_wcd = subparsers.add_parser("write-continuation-decision", help="Write a continuation decision record")
    p_wcd.add_argument("--workspace", "-w", default=".teamloop")
    p_wcd.add_argument("--decision", required=True, help="Decision value (DONE, SAFE_CHECKPOINT, CONTINUE, HUMAN_DECISION_REQUIRED, BLOCKED)")
    p_wcd.add_argument("--phase", default="", help="Phase (auto-populated from team-state.json if omitted)")
    p_wcd.add_argument("--justification", default="", help="Justification text (auto-generated if omitted)")
    p_wcd.add_argument("--task-id", default="", help="Task ID (auto-populated from team-state.json if omitted)")
    p_wcd.add_argument("--run-id", default="", help="Run ID (auto-populated from team-state.json if omitted)")
    p_wcd.add_argument("--blocker-id", default="", help="Blocker ID for BLOCKED/HUMAN_DECISION_REQUIRED")
    p_wcd.add_argument("--blockers-summary", default="", help="Summary of blockers")
    p_wcd.add_argument("--evidence", default=[], action="append", help="Evidence item (repeatable)")

    # run-sentinel
    p_sentinel = subparsers.add_parser("run-sentinel", help="Run read-only sentinel integrity inspection")
    p_sentinel.add_argument("--workspace", "-w", default=".teamloop")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "init-workspace": cmd_init_workspace,
        "validate-state": cmd_validate_state,
        "next-action": cmd_next_action,
        "apply-transition": cmd_apply_transition,
        "write-event": cmd_write_event,
        "check-scope": cmd_check_scope,
        "run-gates": cmd_run_gates,
        "validate-task": cmd_validate_task,
        "validate-research": cmd_validate_research,
        "memory-doctor": cmd_memory_doctor,
        "write-continuation-decision": cmd_write_continuation_decision,
        "check-guard-integrity": cmd_check_guard_integrity,
        "run-sentinel": cmd_run_sentinel,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
