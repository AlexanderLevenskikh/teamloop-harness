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

    # --- Check all existing .json files for valid JSON ---
    # A file that exists but contains invalid JSON is a validation error.
    # A file that doesn't exist is optional — ignored.
    import glob as globmod
    json_pattern = os.path.join(workspace, "**", "*.json")
    for jpath in globmod.glob(json_pattern, recursive=True):
        rel = os.path.relpath(jpath, workspace)
        if is_invalid_json_file(jpath):
            errors.append(f"{rel}: file exists but contains invalid JSON")

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


# ---------------------------------------------------------------------------
# Command: next-action
# ---------------------------------------------------------------------------

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

def cmd_write_event(args):
    workspace = resolve_workspace(args.workspace)
    events_file = os.path.join(workspace, "state", "events.jsonl")

    if not os.path.exists(events_file):
        print("Error: Events file not found. Run init-workspace first.", file=sys.stderr)
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
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
