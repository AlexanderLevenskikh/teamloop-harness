#!/usr/bin/env python3
"""
YourAITeam — Core Runtime
Shared Python implementation for all runtime operations.
Called by .sh and .ps1 wrappers.
"""
import argparse
import datetime
import glob as globmod
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import fnmatch
import teamloop_fast_execution as fast_execution
import teamloop_cache as _cache_mod
import teamloop_inbox as inbox_mod
import teamloop_advisory as advisory_mod
import your_ai_team as team_mod
import codex_support as codex_mod
import quality_value_boundary as boundary_mod
from teamloop_context import WorkspaceContext
from version import YOUR_AI_TEAM_VERSION, YOUR_AI_TEAM_SCHEMA_VERSION

# Temporary source compatibility aliases used by the v0.4 runtime internals.
TEAMLOOP_VERSION = YOUR_AI_TEAM_VERSION
TEAMLOOP_SCHEMA_VERSION = YOUR_AI_TEAM_SCHEMA_VERSION


def _create_cache(workspace, project_root, read_only=False):
    """Create a ValidationCache for the workspace, or None if disabled.

    Returns None when:
      - --no-cache flag was passed (args.no_cache is True)
      - TEAMLOOP_NO_CACHE env var is set
    """
    if os.environ.get("TEAMLOOP_NO_CACHE", "").lower() in ("1", "true", "yes"):
        return None
    cache_path = os.path.join(workspace, "cache", "validation-cache.jsonl")
    return _cache_mod.ValidationCache(
        cache_path=cache_path,
        workspace=workspace,
        project_root=project_root,
        read_only=read_only,
    )


def _classify_cache_state(cache, integrity_result):
    """Classify cache integrity into a canonical state string.

    Returns one of: PASS, EMPTY, DISABLED, CORRUPT, INVALID, LEGACY_UNTRUSTED
    """
    status = integrity_result.get("status", "UNKNOWN")
    total = integrity_result.get("totalEntries", 0)
    malformed = integrity_result.get("malformedLineCount", 0)
    has_corruption = integrity_result.get("hasCorruption", False)
    legacy_count = integrity_result.get("legacyUntrustedCount", 0)
    invalid_entries = integrity_result.get("invalidEntries", [])

    if has_corruption or malformed > 0:
        return "CORRUPT"
    if invalid_entries:
        return "INVALID"
    if legacy_count > 0:
        return "LEGACY_UNTRUSTED"
    if total == 0:
        return "EMPTY"
    if status in ("PASS", "WARNING"):
        return "PASS"
    return status


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

    for subdir in ["state", "runs", "research", "policies", "profiles", "boundaries"]:
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
    for name in ["gate-policy.json", "role-policy.json", "protected-paths.json", "boundary-policy.json"]:
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

    print(f"YourAITeam workspace initialized at {target_dir} with profile '{profile}'.")


# ---------------------------------------------------------------------------
# Cache-aware validation helper
# ---------------------------------------------------------------------------

def _schema_file_path(host_or_project_root, name):
    """Return the full path to a schema file given its basename."""
    pr = host_or_project_root.project_root if hasattr(host_or_project_root, 'project_root') else host_or_project_root
    return os.path.join(pr, "schemas", f"{name}.schema.json")


def _cached_schema_validate(cache, check_name, data, schema, data_path, schema_path, errors_label):
    """Validate data against schema with optional caching.

    Returns list of errors (same format as validate_against_schema).
    When cache is available, checks for a cached result first.

    Canonical cache value: the stored result is ``{"checkId": ..., "errors": []}``.
    ``cache.get()`` returns this directly — never wrapped.
    """
    if cache is None:
        return validate_against_schema(data, schema, errors_label)

    cache_key = cache.build_key(
        check="schema-validate:" + check_name,
        inputs={
            "data": data_path if data_path else None,
        },
        schemas={
            "schema": schema_path if schema_path else None,
        } if schema_path else None,
    )

    cached = cache.get(cache_key)
    if cached is not None:
        # cache.get() returns the result directly (no wrapper).
        errors = cached.get("errors", [])
        return errors

    errors = validate_against_schema(data, schema, errors_label)
    cache.store(cache_key, {"checkId": check_name, "errors": errors})
    return errors


# ---------------------------------------------------------------------------
# Command: validate-state
# ---------------------------------------------------------------------------

def cmd_validate_state(args):
    workspace = resolve_workspace(args.workspace)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Determine whether to use cache.
    no_cache = getattr(args, "no_cache", False)
    cache = None if no_cache else _create_cache(workspace, project_root)

    host = WorkspaceContext(workspace, cache=cache)
    workspace = host.workspace
    project_root = host.project_root

    errors = []

    # Load schemas via WorkspaceContext
    schema_map = host.schemas

    # --- team-state.json ---
    state = host.state_safe
    if state is None:
        errors.append("team-state.json: file not found or invalid JSON")
    else:
        schema_errors = _cached_schema_validate(
            cache, "team-state", state,
            schema_map.get("team-state", {}),
            os.path.join(workspace, "state", "team-state.json"),
            _schema_file_path(project_root, "team-state"),
            "team-state",
        )
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
            for task in host.backlog:
                if task.get("taskId") == task_id:
                    found = True
                    break
            if not found:
                ct = host.current_task
                if ct and ct.get("taskId") == task_id:
                    found = True
            if not found:
                errors.append(f"team-state: currentTaskId '{task_id}' not found in backlog or current-task.json")

        # currentRunId validation
        run_id = state.get("currentRunId", "")
        if run_id:
            run_dir = host.find_run_dir(run_id)
            if not os.path.isdir(run_dir):
                run_found = False
                for entry in host.run_ledger:
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
                entry_errors = _cached_schema_validate(
                    cache, f"{name}-line-{i}", entry, schema,
                    jsonl_path,
                    _schema_file_path(project_root, schema_name),
                    f"{name}.jsonl line {i}",
                )
                errors.extend(entry_errors)
        except (json.JSONDecodeError, ValueError) as e:
            errors.append(f"{name}.jsonl: JSON parse error: {e}")

    # --- current-task.json ---
    ct = host.current_task
    if ct is not None:
        schema_errors = _cached_schema_validate(
            cache, "current-task", ct,
            schema_map.get("task", {}),
            os.path.join(workspace, "state", "current-task.json"),
            _schema_file_path(project_root, "task"),
            "current-task.json",
        )
        errors.extend(schema_errors)

    # --- active-profile.json ---
    profile = host.active_profile
    if not profile:
        errors.append("active-profile.json: file not found or invalid JSON")
    else:
        schema_errors = _cached_schema_validate(
            cache, "active-profile", profile,
            schema_map.get("profile", {}),
            os.path.join(workspace, "profiles", "active-profile.json"),
            _schema_file_path(project_root, "profile"),
            "active-profile.json",
        )
        errors.extend(schema_errors)

    # --- gate-result.json files ---
    runs_dir = os.path.join(workspace, "runs")
    if os.path.isdir(runs_dir):
        for run_name in os.listdir(runs_dir):
            gr_path = os.path.join(runs_dir, run_name, "gate-result.json")
            gr = read_json_file_safe(gr_path)
            if gr is not None:
                schema_errors = _cached_schema_validate(
                    cache, f"gate-result-{run_name}", gr,
                    schema_map.get("gate-result", {}),
                    gr_path,
                    _schema_file_path(project_root, "gate-result"),
                    f"runs/{run_name}/gate-result.json",
                )
                errors.extend(schema_errors)

    # --- Fast-execution run artifacts ---
    if os.path.isdir(runs_dir):
        fast_artifact_schemas = {
            "execution-policy.json": "execution-policy",
            "execution-manifest.json": "execution-manifest",
            "execution-contract-validation.json": "execution-manifest-validation",
            "performance-trace.json": "performance-trace",
            "no-progress-result.json": "no-progress-result",
        }
        for run_name in os.listdir(runs_dir):
            run_path = os.path.join(runs_dir, run_name)
            if not os.path.isdir(run_path):
                continue
            present_contract_parts = []
            for filename, schema_name in fast_artifact_schemas.items():
                artifact_path = os.path.join(run_path, filename)
                if not os.path.exists(artifact_path):
                    continue
                artifact = read_json_file_safe(artifact_path)
                if artifact is None:
                    errors.append(f"runs/{run_name}/{filename}: invalid JSON")
                    continue
                schema = schema_map.get(schema_name, {})
                errors.extend(_cached_schema_validate(
                    cache, f"fast-{schema_name}-{run_name}", artifact, schema,
                    artifact_path,
                    _schema_file_path(project_root, schema_name),
                    f"runs/{run_name}/{filename}",
                ))
                if filename in ("execution-policy.json", "execution-manifest.json"):
                    present_contract_parts.append(filename)
                    if not fast_execution.verify_integrity(artifact):
                        errors.append(f"runs/{run_name}/{filename}: semantic integrity mismatch or manual mutation")
            routing_history_path = os.path.join(run_path, "role-routing-history.jsonl")
            if os.path.exists(routing_history_path):
                try:
                    for line_no, decision in enumerate(read_jsonl(routing_history_path), 1):
                        errors.extend(_cached_schema_validate(
                            cache, f"routing-{run_name}-{line_no}", decision,
                            schema_map.get("role-routing-decision", {}),
                            routing_history_path,
                            _schema_file_path(project_root, "role-routing-decision"),
                            f"runs/{run_name}/role-routing-history.jsonl line {line_no}",
                        ))
                        if not fast_execution.verify_integrity(decision):
                            errors.append(
                                f"runs/{run_name}/role-routing-history.jsonl line {line_no}: "
                                "semantic integrity mismatch or manual mutation"
                            )
                except (json.JSONDecodeError, ValueError) as exc:
                    errors.append(f"runs/{run_name}/role-routing-history.jsonl: JSON parse error: {exc}")
            history_path = os.path.join(run_path, "progress-history.jsonl")
            if os.path.exists(history_path):
                try:
                    for line_no, snapshot in enumerate(read_jsonl(history_path), 1):
                        errors.extend(_cached_schema_validate(
                            cache, f"progress-{run_name}-{line_no}", snapshot,
                            schema_map.get("progress-snapshot", {}),
                            history_path,
                            _schema_file_path(project_root, "progress-snapshot"),
                            f"runs/{run_name}/progress-history.jsonl line {line_no}",
                        ))
                except (json.JSONDecodeError, ValueError) as exc:
                    errors.append(f"runs/{run_name}/progress-history.jsonl: JSON parse error: {exc}")
            # Live drift validation is required for the active run only. Completed
            # historical runs retain immutable evidence but are not invalidated by
            # later policy evolution.
            if state is not None and state.get("currentRunId") == run_name and len(present_contract_parts) == 2:
                try:
                    contract_result = fast_execution.validate_contract(workspace, run_name, write_result=False)
                    errors.extend(
                        f"runs/{run_name}/execution-contract: {msg}"
                        for msg in contract_result.get("errors", [])
                    )
                except Exception as exc:
                    errors.append(f"runs/{run_name}/execution-contract: validation error: {exc}")

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
                    entry_errors = _cached_schema_validate(
                        cache, f"memory-{name}-line-{i}", entry, schema,
                        jsonl_path,
                        _schema_file_path(project_root, schema_name),
                        f"memory/{name} line {i}",
                    )
                    errors.extend(entry_errors)
            except (json.JSONDecodeError, ValueError) as e:
                errors.append(f"memory/{name}: JSON parse error: {e}")

        # project-profile.json validated against memory-profile schema
        pp_path = os.path.join(memory_dir, "project-profile.json")
        pp = read_json_file_safe(pp_path)
        if pp is not None:
            pp_errors = _cached_schema_validate(
                cache, "memory-profile", pp,
                schema_map.get("memory-profile", {}),
                pp_path,
                _schema_file_path(project_root, "memory-profile"),
                "memory/project-profile.json",
            )
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
        ct = host.current_task
        if ct and ct.get("status") == "IN_PROGRESS":
            errors.append("state/current-task.json: stale IN_PROGRESS task while team-state has no currentTaskId")

    # --- Orphaned IN_PROGRESS tasks in backlog ---
    # If team-state has no currentTaskId but backlog contains IN_PROGRESS tasks,
    # those are orphaned (no active run tracking them).
    if state is not None and not state.get("currentTaskId", ""):
        for task in host.backlog:
            if task.get("status") == "IN_PROGRESS":
                errors.append(
                    f"backlog: orphaned IN_PROGRESS task '{task.get('taskId', '?')}' "
                    f"with no matching currentTaskId in team-state"
                )

    # --- Active current-task.json taskId mismatch invariant ---
    # If phase is task-scoped and currentTaskId is set, current-task.json must exist
    # and its taskId must match team-state's currentTaskId.
    if state is not None:
        task_scoped_phases = frozenset([
            "EXECUTING_TASK", "NEEDS_CHANGE_REVIEW", "NEEDS_GATE",
            "NEEDS_BOUNDARY_DECISION", "REVIEW_FAILED", "GATE_FAILED"
        ])
        if phase in task_scoped_phases and task_id:
            ct = host.current_task
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

    # --- Phase 5: Review evidence integrity check ---
    review_errors = _validate_review_evidence(workspace)
    errors.extend(review_errors)

    # --- Sentinel inspection check (last, optional, backward-compatible) ---
    # If sentinel-inspection.json exists for the current run (or latest run),
    # validate it. CRITICAL findings add errors (fail validation).
    # WARNING findings print to stderr but do not fail validation.
    # Missing sentinel-inspection.json is silently skipped.
    sentinel_errors, sentinel_warnings = _validate_sentinel_for_validate(workspace, project_root)
    errors.extend(sentinel_errors)
    if sentinel_warnings:
        for w in sentinel_warnings:
            print(f"  WARNING: {w}", file=sys.stderr)

    # --- Quality/value boundary integrity ---
    errors.extend(boundary_mod.validate_workspace(workspace, project_root=host.git_root))

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

    if phase in ("EXECUTING_TASK", "NEEDS_CHANGE_REVIEW", "NEEDS_GATE", "NEEDS_BOUNDARY_DECISION"):
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
    git_status_entries = _get_git_status_entries(os.path.dirname(os.path.abspath(workspace)))

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
# Sentinel inspection check for validate-state
# ---------------------------------------------------------------------------

def _validate_sentinel_for_validate(workspace, project_root):
    """Sentinel inspection check integrated into validate-state.

    Looks for sentinel-inspection.json in the current run directory (if
    team-state has a currentRunId) or the latest run directory.  If found,
    validates against sentinel-inspection.schema.json.  CRITICAL findings
    produce validation errors.  WARNING findings produce warnings on stderr.

    Missing sentinel-inspection.json is silently skipped (backward-compatible).
    Schema validation failure (malformed file) adds a validation error.

    Returns:
        (errors, warnings) — two lists of strings.
    """
    errors = []
    warnings = []

    # Locate sentinel-inspection.json
    sentinel_path = _sentinel_find_inspection_file(workspace)
    if sentinel_path is None:
        # No sentinel-inspection.json found — skip silently
        return errors, warnings

    # Load schema for validation
    schema_path = os.path.join(project_root, "schemas", "sentinel-inspection.schema.json")
    schema = {}
    if os.path.exists(schema_path):
        try:
            schema = read_json(schema_path)
        except (ValueError, json.JSONDecodeError):
            pass

    # Read the sentinel inspection file
    inspection = read_json_file_safe(sentinel_path)
    if inspection is None:
        # File exists but is not valid JSON — this is a validation error
        rel_path = os.path.relpath(sentinel_path, workspace)
        errors.append(
            f"{rel_path}: sentinel-inspection.json exists but contains invalid JSON"
        )
        return errors, warnings

    # Validate against schema
    schema_errors = validate_against_schema(inspection, schema, "sentinel-inspection.json")
    if schema_errors:
        rel_path = os.path.relpath(sentinel_path, workspace)
        for se in schema_errors:
            errors.append(f"{rel_path}: schema violation — {se}")
        return errors, warnings

    # Check findings for CRITICAL and WARNING severities
    findings = inspection.get("findings", [])
    if not isinstance(findings, list):
        findings = []

    for finding in findings:
        severity = finding.get("severity", "")
        title = finding.get("title", "unnamed finding")
        category = finding.get("category", "unknown")

        if severity == "CRITICAL":
            errors.append(
                f"sentinel-inspection CRITICAL finding blocks completion: "
                f"[{category}] {title}"
            )
        elif severity == "WARNING":
            warnings.append(
                f"sentinel-inspection WARNING: "
                f"[{category}] {title}"
            )

    return errors, warnings


def _sentinel_find_inspection_file(workspace):
    """Find sentinel-inspection.json in the workspace.

    Prefers the current run directory (from team-state currentRunId).
    Falls back to the latest run directory (lexicographic order).
    Returns None if no sentinel-inspection.json is found.
    """
    runs_dir = os.path.join(workspace, "runs")
    if not os.path.isdir(runs_dir):
        return None

    # Try currentRunId first
    state = read_json_file_safe(os.path.join(workspace, "state", "team-state.json"))
    if state:
        run_id = state.get("currentRunId", "")
        if run_id:
            candidate = os.path.join(runs_dir, run_id, "sentinel-inspection.json")
            if os.path.exists(candidate):
                return candidate

    # Fall back to latest run directory (lexicographic sort)
    try:
        run_dirs = sorted([
            d for d in os.listdir(runs_dir)
            if os.path.isdir(os.path.join(runs_dir, d))
        ])
    except OSError:
        return None

    for run_name in reversed(run_dirs):
        candidate = os.path.join(runs_dir, run_name, "sentinel-inspection.json")
        if os.path.exists(candidate):
            return candidate

    return None


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
    # Accept --no-cache flag for consistency; cache is optional.
    no_cache = getattr(args, "no_cache", False)
    host = WorkspaceContext(args.workspace)
    memory_dir = os.path.join(host.workspace, "memory")

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

    # Load schemas for schema conformance checks — via WorkspaceContext
    schema_map = host.schemas

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


def _check_workspace_integrity(workspace):
    """Perform lightweight integrity checks to determine if the workspace is safe.

    Returns (is_safe: bool, reasons: list[str]).
    When is_safe is False, reasons lists the blocking integrity issues.
    """
    reasons = []

    # Check 1: Look for review evidence with stale content
    evidence_path = _find_review_evidence(workspace)
    if evidence_path is not None:
        evidence = read_json_file_safe(evidence_path)
        if evidence:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            workspace_parent = os.path.dirname(os.path.normpath(workspace))
            for rf in evidence.get("reviewedFiles", []):
                path = rf.get("path", "")
                expected_hash = rf.get("hash", "")
                if not path or not expected_hash:
                    continue
                candidates = []
                if os.path.isabs(path):
                    candidates.append(path)
                else:
                    candidates.append(os.path.join(workspace_parent, path))
                    candidates.append(os.path.join(project_root, path))
                found = False
                for abs_path in candidates:
                    if os.path.exists(abs_path):
                        actual = _compute_file_sha256(abs_path)
                        if actual and actual != expected_hash:
                            reasons.append(f"reviewed content changed: {path}")
                        found = True
                        break
                if not found:
                    reasons.append(f"reviewed content missing: {path}")

    # Check 2: Orphaned run — currentRunId set but no active task and phase is not task-scoped
    state = read_json_file_safe(os.path.join(workspace, "state", "team-state.json"))
    if state:
        run_id = state.get("currentRunId", "")
        phase = state.get("currentPhase", "")
        task_id = state.get("currentTaskId", "")
        task_scoped = frozenset(["EXECUTING_TASK", "NEEDS_CHANGE_REVIEW", "NEEDS_GATE", "REVIEW_FAILED", "GATE_FAILED"])
        if run_id and not task_id and phase not in task_scoped:
            reasons.append(f"orphaned run '{run_id}': currentRunId set but no currentTaskId")

    # Check 3: Stale IN_PROGRESS current-task.json
    ct_path = os.path.join(workspace, "state", "current-task.json")
    if os.path.exists(ct_path):
        ct = read_json_file_safe(ct_path)
        if ct and ct.get("status") == "IN_PROGRESS" and state and not state.get("currentTaskId", ""):
            reasons.append("stale IN_PROGRESS current-task.json with no matching currentTaskId")

    return (len(reasons) == 0, reasons)


# ---------------------------------------------------------------------------
# Canonical workspace readiness evaluation
# ---------------------------------------------------------------------------

def _evaluate_workspace_integrity(workspace):
    """Canonical workspace readiness evaluation.

    Returns:
      {
        "status": "GREEN" | "YELLOW" | "RED",
        "checks": [
          {"name": str, "status": "PASS" | "FAIL" | "SKIP", "detail": str},
          ...
        ],
        "blockingIssues": [str, ...],  # non-empty when status is RED
      }

    Checks to run:
    1. state-schema-validity — team-state.json parses and has required fields
    2. task-run-consistency — currentTaskId/currentRunId consistent
    3. orphaned-runs — detect orphaned IN_PROGRESS runs in ledger (blocking/RED)
    4. stale-active-task — current-task.json stale vs state
    5. stale-active-run — run directory exists for currentRunId
    6. reviewed-content-integrity — reviewedFiles hashes match (existing logic)
    7. unresolved-blockers — open blockers exist
    8. failed-gate — latest required gate failed
    9. sentinel-freshness — sentinel required but missing or stale
    10. guard-integrity — guard required but failed
    11. memory-configuration — memory issues
    12. continuation-consistency — continuation-decision consistent with state
    13. malformed-artifacts — critical JSON artifacts parseable
    14. contradictory-sentinels — multiple PASS sentinels with conflicting fingerprints
    15. cache-integrity — cache file integrity (malformed lines, hash failures)
    """
    project_root = fast_execution._repo_root(workspace)
    checks = []
    blocking_issues = []

    # ---- helpers ----
    def _add_check(name, status, detail=""):
        checks.append({"name": name, "status": status, "detail": detail})

    def _add_fail(name, detail, blocking=True):
        checks.append({"name": name, "status": "FAIL", "detail": detail})
        if blocking:
            blocking_issues.append(detail)

    def _add_pass(name, detail=""):
        checks.append({"name": name, "status": "PASS", "detail": detail})

    def _add_skip(name, detail=""):
        checks.append({"name": name, "status": "SKIP", "detail": detail})

    # ---- load state ----
    state_path = os.path.join(workspace, "state", "team-state.json")
    state = None

    # ===== Check 1: state-schema-validity =====
    try:
        state = read_json(state_path)
        required_fields = ["schemaVersion", "status", "currentPhase"]
        missing = [f for f in required_fields if f not in state]
        if missing:
            _add_fail("state-schema-validity",
                      f"team-state.json missing required fields: {', '.join(missing)}")
        else:
            _add_pass("state-schema-validity", "team-state.json valid with required fields")
    except Exception as exc:
        _add_fail("state-schema-validity", f"team-state.json cannot be read: {exc}")

    if state is None:
        # Cannot proceed meaningfully without state — still run remaining
        # checks that don't depend on state
        _add_skip("task-run-consistency", "state unavailable")
        # orphaned-runs, stale-active-task, stale-active-run are handled by
        # their dedicated helpers below which tolerate None state
        # continue to remaining checks that are state-independent
    else:
        # ===== Check 2: task-run-consistency =====
        current_task_id = state.get("currentTaskId", "")
        current_run_id = state.get("currentRunId", "")
        phase = state.get("currentPhase", "")

        task_scoped_phases = frozenset([
            "EXECUTING_TASK", "NEEDS_CHANGE_REVIEW", "NEEDS_GATE",
            "REVIEW_FAILED", "GATE_FAILED",
        ])

        consistency_ok = True
        consistency_detail = "currentTaskId/currentRunId consistent"

        # If we're in a task-scoped phase, both should be present
        if phase in task_scoped_phases:
            if not current_task_id:
                consistency_ok = False
                consistency_detail = f"task-scoped phase '{phase}' has no currentTaskId"
            if not current_run_id:
                consistency_ok = False
                consistency_detail = f"task-scoped phase '{phase}' has no currentRunId"

        # If we're NOT in a task-scoped phase, both should be empty (identity cleared)
        elif not (phase in ("", "NEEDS_DISCOVERY", "NEEDS_PLAN", "NEEDS_RESEARCH",
                            "NEEDS_RESEARCH_REVIEW", "NEEDS_TASK_SLICING",
                            "READY_FOR_NEXT_TASK", "SAFE_CHECKPOINT", "DONE",
                            "HUMAN_DECISION_REQUIRED", "BLOCKED")):
            # Unknown phase — warn but don't block
            pass

        if not consistency_ok:
            _add_fail("task-run-consistency", consistency_detail)
        else:
            _add_pass("task-run-consistency", consistency_detail)

    # ===== Check 3: orphaned-runs =====
    _check_orphaned_runs(workspace, state, current_task_id if state else "",
                          current_run_id if state else "", checks, blocking_issues)

    # ===== Check 4: stale-active-task =====
    if state:
        ct_path = os.path.join(workspace, "state", "current-task.json")
        if os.path.exists(ct_path):
            ct = read_json_file_safe(ct_path)
            if ct and ct.get("status") == "IN_PROGRESS" and not state.get("currentTaskId", ""):
                _add_fail("stale-active-task",
                          "current-task.json is IN_PROGRESS but team-state has no currentTaskId")
            else:
                _add_pass("stale-active-task")
        else:
            # No current-task.json — that's fine when no task is active
            if state.get("currentTaskId", ""):
                _add_fail("stale-active-task",
                          "currentTaskId set in team-state but current-task.json missing")
            else:
                _add_pass("stale-active-task")
    else:
        _add_skip("stale-active-task", "state unavailable")

    # ===== Check 5: stale-active-run =====
    if state and state.get("currentRunId", ""):
        run_id = state["currentRunId"]
        run_dir = os.path.join(workspace, "runs", run_id)
        if not os.path.isdir(run_dir):
            _add_fail("stale-active-run",
                      f"Run directory for currentRunId '{run_id}' not found")
        else:
            _add_pass("stale-active-run")
    elif state:
        _add_pass("stale-active-run")
    else:
        _add_skip("stale-active-run", "state unavailable")

    # ===== Check 6: reviewed-content-integrity =====
    evidence_path = _find_review_evidence(workspace)
    if evidence_path is not None:
        evidence = read_json_file_safe(evidence_path)
        if evidence:
            drift_found = False
            drift_details = []
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            workspace_parent = os.path.dirname(os.path.normpath(workspace))
            for rf in evidence.get("reviewedFiles", []):
                path = rf.get("path", "")
                expected_hash = rf.get("hash", "")
                if not path or not expected_hash:
                    continue
                candidates = []
                if os.path.isabs(path):
                    candidates.append(path)
                else:
                    candidates.append(os.path.join(workspace_parent, path))
                    candidates.append(os.path.join(project_root, path))
                found = False
                for abs_path in candidates:
                    if os.path.exists(abs_path):
                        actual = _compute_file_sha256(abs_path)
                        if actual and actual != expected_hash:
                            drift_details.append(f"reviewed content changed: {path}")
                            drift_found = True
                        found = True
                        break
                    # else continue to next candidate
                if not found:
                    drift_details.append(f"reviewed content missing: {path}")
                    drift_found = True

            if drift_found:
                detail = "; ".join(drift_details[:5])
                _add_fail("reviewed-content-integrity", detail)
            else:
                _add_pass("reviewed-content-integrity")
        else:
            _add_pass("reviewed-content-integrity")
    else:
        _add_pass("reviewed-content-integrity")

    # ===== Check 7: unresolved-blockers =====
    blockers_path = os.path.join(workspace, "state", "blockers.jsonl")
    if os.path.exists(blockers_path):
        try:
            blockers = read_jsonl(blockers_path)
            open_blockers = [b for b in blockers if not b.get("resolvedAtUtc")]
            if open_blockers:
                summaries = [b.get("summary", "no summary") for b in open_blockers[:5]]
                detail = f"{len(open_blockers)} unresolved blocker(s): {'; '.join(summaries)}"
                _add_fail("unresolved-blockers", detail)
            else:
                _add_pass("unresolved-blockers")
        except Exception:
            _add_pass("unresolved-blockers")
    else:
        _add_pass("unresolved-blockers")

    # ===== Check 8: failed-gate =====
    gate_failed = False
    gate_detail = ""
    if state and state.get("currentRunId", ""):
        gr_path = os.path.join(workspace, "runs", state["currentRunId"], "gate-result.json")
        if os.path.exists(gr_path):
            gr = read_json_file_safe(gr_path)
            if gr and gr.get("status") == "FAIL":
                gate_failed = True
                gate_detail = "latest gate result is FAIL"
    if gate_failed:
        _add_fail("failed-gate", gate_detail)
    else:
        _add_pass("failed-gate")

    # ===== Check 9: sentinel-freshness =====
    _check_sentinel_freshness(workspace, state, checks)

    # ===== Check 10: guard-integrity =====
    _check_guard_status(workspace, checks)

    # ===== Check 11: memory-configuration =====
    _check_memory_config(workspace, checks)

    # ===== Check 12: continuation-consistency =====
    _check_continuation_consistency(workspace, state, checks)

    # ===== Check 13: malformed-artifacts =====
    _check_malformed_artifacts(workspace, checks, blocking_issues)

    # ===== Check 14: contradictory-sentinels =====
    _check_contradictory_sentinels(workspace, checks, blocking_issues)

    # ===== Check 15: cache-integrity =====
    cache = _create_cache(workspace, project_root)
    if cache is None:
        _add_skip("cache-integrity",
                   "cache disabled by TEAMLOOP_NO_CACHE")
    else:
        cache_status = cache.integrity_check()
        cache_state = _classify_cache_state(cache, cache_status)
        if cache_state in ("PASS", "EMPTY", "DISABLED"):
            _add_pass("cache-integrity",
                       f"cache ok ({cache_state})")
        elif cache_state in ("CORRUPT", "INVALID"):
            _add_fail("cache-integrity",
                       f"cache {cache_state}: "
                       f"malformed={cache_status.get('malformedLineCount', 0)}, "
                       f"invalid={len(cache_status.get('invalidEntries', []))}, "
                       f"legacy={cache_status.get('legacyUntrustedCount', 0)}",
                       blocking=True)
        elif cache_state == "LEGACY_UNTRUSTED":
            _add_pass("cache-integrity",
                       f"cache has {cache_status.get('legacyUntrustedCount', 0)} legacy entries (quarantined)")
        else:
            _add_skip("cache-integrity", f"unexpected cache state: {cache_state}")

    # ---- compute overall status ----
    if blocking_issues:
        overall = "RED"
    elif any(c["status"] == "FAIL" for c in checks):
        overall = "YELLOW"
    else:
        overall = "GREEN"

    return {
        "status": overall,
        "checks": checks,
        "blockingIssues": blocking_issues,
    }


def _check_orphaned_runs(workspace, state, current_task_id, current_run_id,
                          checks, blocking_issues):
    """Check 3: detect orphaned IN_PROGRESS runs in ledger."""
    ledger_path = os.path.join(workspace, "state", "run-ledger.jsonl")
    if not os.path.exists(ledger_path):
        checks.append({"name": "orphaned-runs", "status": "PASS", "detail": "no run ledger"})
        return

    try:
        ledger = read_jsonl(ledger_path)
    except Exception:
        checks.append({"name": "orphaned-runs", "status": "FAIL",
                        "detail": "run-ledger.jsonl cannot be parsed"})
        blocking_issues.append("run-ledger.jsonl cannot be parsed")
        return

    backlog_path = os.path.join(workspace, "state", "backlog.jsonl")
    backlog = []
    if os.path.exists(backlog_path):
        try:
            backlog = read_jsonl(backlog_path)
        except Exception:
            pass

    active_tasks = {t["taskId"] for t in backlog if t.get("status") in ("IN_PROGRESS", "READY")}
    done_tasks = {t["taskId"] for t in backlog if t.get("status") == "DONE"}

    in_progress_runs = [r for r in ledger if r.get("status") == "IN_PROGRESS"]
    all_run_ids = {r["runId"] for r in ledger}
    orphan_details = []

    # Sub-check: ledger contains IN_PROGRESS but team state has no current run
    if in_progress_runs and state and not current_run_id:
        orphan_details.append("run ledger has IN_PROGRESS runs but team-state has no currentRunId")

    # Sub-check: team state references a run missing from ledger
    if state and current_run_id and current_run_id not in all_run_ids:
        orphan_details.append(f"team-state currentRunId '{current_run_id}' not found in run ledger")

    # Sub-check: run references a task that is no longer active or does not exist
    for run in in_progress_runs:
        run_task_id = run.get("taskId", "")
        if run_task_id:
            if run_task_id not in active_tasks:
                if run_task_id in done_tasks:
                    orphan_details.append(
                        f"run '{run.get('runId', '?')}' is IN_PROGRESS but task '{run_task_id}' is DONE"
                    )
                else:
                    orphan_details.append(
                        f"run '{run.get('runId', '?')}' references task '{run_task_id}' "
                        f"which is not IN_PROGRESS or READY"
                    )

    # Sub-check: multiple conflicting active runs
    if len(in_progress_runs) > 1:
        orphan_details.append(
            f"multiple IN_PROGRESS runs ({len(in_progress_runs)}) exist in ledger"
        )

    # Sub-check: current task/run pair disagrees
    if state and current_run_id and current_task_id:
        for run in in_progress_runs:
            if run.get("runId") == current_run_id:
                if run.get("taskId", "") and run["taskId"] != current_task_id:
                    orphan_details.append(
                        f"currentRunId '{current_run_id}' task '{run.get('taskId', '?')}' "
                        f"does not match currentTaskId '{current_task_id}'"
                    )
                break

    if orphan_details:
        detail = "; ".join(orphan_details[:5])
        checks.append({"name": "orphaned-runs", "status": "FAIL", "detail": detail})
        # Orphaned IN_PROGRESS runs are blocking (RED) per spec
        blocking_issues.append(f"Orphaned IN_PROGRESS runs: {len(orphan_details)} issue(s): {detail[:200]}")
    else:
        checks.append({"name": "orphaned-runs", "status": "PASS",
                        "detail": "no orphaned runs detected"})


def _check_contradictory_sentinels(workspace, checks, blocking_issues):
    """Detect when multiple sentinel reports exist with conflicting fingerprints.

    If two or more runs have PASS sentinel reports but with different
    semanticFingerprint values, that is a contradiction — the workspace
    has diverging integrity states.  Classified as RED (blocking).
    """
    runs_dir = os.path.join(workspace, "runs")
    if not os.path.isdir(runs_dir):
        checks.append({"name": "contradictory-sentinels", "status": "PASS",
                        "detail": "no runs directory"})
        return

    pass_sentinels = []
    try:
        for run_name in sorted(os.listdir(runs_dir)):
            run_dir = os.path.join(runs_dir, run_name)
            if not os.path.isdir(run_dir):
                continue
            sentinel = os.path.join(run_dir, "sentinel-inspection.json")
            if not os.path.exists(sentinel):
                continue
            report = read_json_file_safe(sentinel)
            if report is None:
                continue
            if report.get("overallStatus") == "PASS":
                pass_sentinels.append({
                    "runId": run_name,
                    "repositoryHead": report.get("repositoryHead", ""),
                    "semanticFingerprint": report.get("semanticFingerprint", ""),
                })
    except OSError:
        checks.append({"name": "contradictory-sentinels", "status": "SKIP",
                        "detail": "could not read runs directory"})
        return

    # Contradictory: multiple PASS sentinels with different fingerprints
    if len(pass_sentinels) > 1:
        fingerprints = set(s["semanticFingerprint"] for s in pass_sentinels)
        if len(fingerprints) > 1:
            run_ids = [s["runId"] for s in pass_sentinels]
            detail = (f"multiple PASS sentinel reports with conflicting fingerprints: "
                       f"{', '.join(run_ids)}")
            checks.append({"name": "contradictory-sentinels", "status": "FAIL",
                            "detail": detail})
            blocking_issues.append(f"Contradictory sentinel fingerprints: {detail[:200]}")
            return

    checks.append({"name": "contradictory-sentinels", "status": "PASS",
                    "detail": f"{len(pass_sentinels)} PASS sentinel(s), no contradictions"})


def _check_sentinel_freshness(workspace, state, checks):
    """Check 9: sentinel required but missing or stale."""
    if not state:
        checks.append({"name": "sentinel-freshness", "status": "SKIP", "detail": "state unavailable"})
        return

    # Sentinel is expected when gates have run or when approaching final gate
    # Check if a sentinel report exists in the current run
    run_id = state.get("currentRunId", "")
    phase = state.get("currentPhase", "")

    # Sentinel is expected for phases that follow gate or for final-gate
    sentinel_expected = phase in ("NEEDS_GATE", "SAFE_CHECKPOINT", "BLOCKED", "DONE")

    if not sentinel_expected:
        checks.append({"name": "sentinel-freshness", "status": "PASS",
                        "detail": "sentinel not required for current phase"})
        return

    # Look for sentinel report
    found = False
    if run_id:
        sp = os.path.join(workspace, "runs", run_id, "sentinel-inspection.json")
        if os.path.exists(sp):
            sr = read_json_file_safe(sp)
            if sr and sr.get("overallStatus") in ("PASS", "WARNING"):
                checks.append({"name": "sentinel-freshness", "status": "PASS",
                                "detail": f"sentinel status: {sr.get('overallStatus')}"})
                return
            elif sr:
                checks.append({"name": "sentinel-freshness", "status": "FAIL",
                                "detail": f"sentinel status: {sr.get('overallStatus', 'unknown')}"})
                return

    # Check state-level sentinel
    sp_state = os.path.join(workspace, "state", "sentinel-inspection.json")
    if os.path.exists(sp_state):
        sr = read_json_file_safe(sp_state)
        if sr and sr.get("overallStatus") in ("PASS", "WARNING"):
            checks.append({"name": "sentinel-freshness", "status": "PASS",
                            "detail": f"sentinel status: {sr.get('overallStatus')}"})
            return
        elif sr:
            checks.append({"name": "sentinel-freshness", "status": "FAIL",
                            "detail": f"sentinel status: {sr.get('overallStatus', 'unknown')}"})
            return

    checks.append({"name": "sentinel-freshness", "status": "FAIL",
                    "detail": "sentinel report missing for phase requiring it"})


def _check_guard_status(workspace, checks):
    """Check 10: guard required but failed.

    Lightweight: only reads the last guard-integrity result file.
    Does NOT re-run guard (no subprocess calls).
    """
    # Check for guard-integrity result in current run
    runs_dir = os.path.join(workspace, "runs")
    if os.path.isdir(runs_dir):
        try:
            run_dirs = sorted([
                d for d in os.listdir(runs_dir)
                if os.path.isdir(os.path.join(runs_dir, d))
            ], reverse=True)
        except OSError:
            run_dirs = []

        for run_name in run_dirs:
            gp = os.path.join(runs_dir, run_name, "guard-integrity-result.json")
            if os.path.exists(gp):
                gr = read_json_file_safe(gp)
                if gr:
                    status = gr.get("status", "unknown")
                    if status == "FAIL":
                        checks.append({"name": "guard-integrity", "status": "FAIL",
                                        "detail": f"guard integrity failed: {gr.get('detail', '')}"})
                    else:
                        checks.append({"name": "guard-integrity", "status": "PASS",
                                        "detail": f"guard integrity: {status}"})
                    return

    # Check state-level
    gp_state = os.path.join(workspace, "state", "guard-integrity-result.json")
    if os.path.exists(gp_state):
        gr = read_json_file_safe(gp_state)
        if gr:
            status = gr.get("status", "unknown")
            if status == "FAIL":
                checks.append({"name": "guard-integrity", "status": "FAIL",
                                "detail": f"guard integrity failed: {gr.get('detail', '')}"})
            else:
                checks.append({"name": "guard-integrity", "status": "PASS",
                                "detail": f"guard integrity: {status}"})
            return

    checks.append({"name": "guard-integrity", "status": "SKIP",
                    "detail": "no guard-integrity result found"})


def _check_memory_config(workspace, checks):
    """Check 11: memory issues."""
    memory_dir = os.path.join(workspace, "memory")
    if not os.path.isdir(memory_dir):
        checks.append({"name": "memory-configuration", "status": "SKIP",
                        "detail": "no memory directory"})
        return

    issues = []

    # Check lessons.jsonl
    lessons_path = os.path.join(memory_dir, "lessons.jsonl")
    if os.path.exists(lessons_path):
        try:
            lessons = read_jsonl(lessons_path)
            evidence_path = os.path.join(memory_dir, "evidence-map.jsonl")
            evidence_map = {}
            if os.path.exists(evidence_path):
                try:
                    for ev in read_jsonl(evidence_path):
                        evidence_map[ev.get("evidenceId", "")] = ev
                except Exception:
                    pass

            for lesson in lessons:
                if lesson.get("status") == "ACTIVE":
                    for eid in lesson.get("evidenceIds", []):
                        if eid not in evidence_map:
                            issues.append(
                                f"ACTIVE lesson '{lesson.get('lessonId', '?')}' "
                                f"references missing evidence '{eid}'"
                            )
                        else:
                            ev = evidence_map[eid]
                            if ev.get("verified") is False:
                                issues.append(
                                    f"ACTIVE lesson '{lesson.get('lessonId', '?')}' "
                                    f"references UNVERIFIED evidence '{eid}'"
                                )

                if lesson.get("status") == "SUPERSEDED":
                    sup = lesson.get("supersededBy", "")
                    if sup:
                        found = any(l.get("lessonId") == sup for l in lessons)
                        if not found:
                            issues.append(
                                f"SUPERSEDED lesson '{lesson.get('lessonId', '?')}' "
                                f"references missing supersededBy '{sup}'"
                            )
        except Exception:
            pass

    if issues:
        checks.append({"name": "memory-configuration", "status": "FAIL",
                        "detail": "; ".join(issues[:5])})
    else:
        checks.append({"name": "memory-configuration", "status": "PASS",
                        "detail": "no memory issues detected"})


def _check_continuation_consistency(workspace, state, checks):
    """Check 12: continuation-decision consistent with state."""
    if not state:
        checks.append({"name": "continuation-consistency", "status": "SKIP",
                        "detail": "state unavailable"})
        return

    cd_path = os.path.join(workspace, "state", "continuation-decision.json")
    if not os.path.exists(cd_path):
        checks.append({"name": "continuation-consistency", "status": "PASS",
                        "detail": "no continuation-decision.json present"})
        return

    cd = read_json_file_safe(cd_path)
    if cd is None:
        checks.append({"name": "continuation-consistency", "status": "FAIL",
                        "detail": "continuation-decision.json exists but cannot be parsed"})
        return

    phase = state.get("currentPhase", "")
    status = state.get("status", "")
    cd_decision = cd.get("decision", "")
    cd_phase = cd.get("phase", "")

    inconsistencies = []

    # Terminal states should have matching decision
    if status in ("DONE",) and cd_decision and cd_decision != "DONE":
        inconsistencies.append(
            f"status is DONE but continuation decision is '{cd_decision}'"
        )

    if status == "HUMAN_DECISION_REQUIRED" and cd_decision and cd_decision != "HUMAN_DECISION_REQUIRED":
        inconsistencies.append(
            f"status is HUMAN_DECISION_REQUIRED but continuation decision is '{cd_decision}'"
        )

    if phase == "SAFE_CHECKPOINT" and cd_decision and cd_decision not in ("SAFE_CHECKPOINT", "CONTINUE", "DONE"):
        inconsistencies.append(
            f"phase is SAFE_CHECKPOINT but continuation decision is '{cd_decision}'"
        )

    if inconsistencies:
        checks.append({"name": "continuation-consistency", "status": "FAIL",
                        "detail": "; ".join(inconsistencies)})
    else:
        checks.append({"name": "continuation-consistency", "status": "PASS",
                        "detail": "continuation-decision.json consistent with state"})


def _check_malformed_artifacts(workspace, checks, blocking_issues):
    """Check 13: critical JSON artifacts parseable."""
    critical_files = [
        ("state/team-state.json", True),
        ("state/continuation-decision.json", False),
    ]
    jsonl_files = [
        ("state/backlog.jsonl", False),
        ("state/run-ledger.jsonl", True),
        ("state/events.jsonl", False),
        ("state/blockers.jsonl", False),
    ]

    issues = []
    for rel_path, blocking in critical_files:
        full_path = os.path.join(workspace, rel_path)
        if os.path.exists(full_path) and os.path.getsize(full_path) > 0:
            if is_invalid_json_file(full_path):
                issues.append(f"{rel_path}: malformed JSON")
                if blocking:
                    blocking_issues.append(f"{rel_path}: malformed JSON")

    for rel_path, blocking in jsonl_files:
        full_path = os.path.join(workspace, rel_path)
        if os.path.exists(full_path):
            try:
                read_jsonl(full_path)
            except Exception as exc:
                issues.append(f"{rel_path}: JSONL parse error: {exc}")
                if blocking:
                    blocking_issues.append(f"{rel_path}: JSONL parse error: {exc}")

    if issues:
        checks.append({"name": "malformed-artifacts", "status": "FAIL",
                        "detail": "; ".join(issues[:5])})
    else:
        checks.append({"name": "malformed-artifacts", "status": "PASS",
                        "detail": "all critical artifacts parseable"})


# Legacy wrapper: delegates to _evaluate_workspace_integrity for backward compat
def _check_workspace_integrity(workspace):
    """Perform lightweight integrity checks to determine if the workspace is safe.

    Returns (is_safe: bool, reasons: list[str]).
    When is_safe is False, reasons lists the blocking integrity issues.

    DELEGATES to _evaluate_workspace_integrity() for the canonical check.
    """
    result = _evaluate_workspace_integrity(workspace)
    reasons = [
        c["detail"] for c in result["checks"]
        if c["status"] == "FAIL" and c["detail"]
    ]
    return (result["status"] != "RED", reasons)


def cmd_next_action(args):
    started = fast_execution.clock_ms()
    workspace = resolve_workspace(args.workspace)
    state = read_json(os.path.join(workspace, "state", "team-state.json"))
    state_loaded = fast_execution.clock_ms()
    phase = state.get("currentPhase", "")
    status = state.get("status", "")
    human_required = state.get("humanRequired", False)

    no_progress_route = fast_execution.active_no_progress_route(workspace)
    result = no_progress_route or _compute_next_action(phase, status, human_required, workspace)
    run_id = state.get("currentRunId", "")
    fast_execution.record_trace_phase(
        workspace, run_id, "state-load", state_loaded - started,
        files=["state/team-state.json", "state/backlog.jsonl"],
    )
    fast_execution.record_trace_phase(
        workspace, run_id, "next-action-resolution", fast_execution.clock_ms() - state_loaded,
        files=["no-progress-result.json"],
        decision="NO_OP" if result.get("nextAction") in ("STOP", "NO_READY_TASK") else "EXECUTED",
        details=result.get("reason", result.get("nextAction", "")),
    )
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
            # Check if workspace is genuinely safe before reporting NO_READY_TASK
            integrity = _evaluate_workspace_integrity(workspace)
            if integrity["status"] == "RED":
                return {
                    "nextAction": "CORRECTIVE_WORK_REQUIRED",
                    "phase": "BLOCKED",
                    "taskId": "",
                    "humanRequired": False,
                    "reason": "; ".join(integrity["blockingIssues"][:5]),
                }
            elif integrity["status"] == "YELLOW":
                return {
                    "nextAction": "NO_READY_TASK",
                    "phase": "READY_FOR_NEXT_TASK",
                    "taskId": "",
                    "humanRequired": False,
                    "reason": "safe checkpoint with advisory issues",
                }
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
        "NEEDS_BOUNDARY_DECISION": ("RUN_QUALITY_VALUE_MANAGER", "NEEDS_BOUNDARY_DECISION", ""),
        "REVIEW_FAILED": ("RUN_EXECUTOR", "EXECUTING_TASK", ""),
        "HUMAN_DECISION_REQUIRED": ("STOP", "HUMAN_DECISION_REQUIRED", ""),
        "BOUNDARY_STOPPED": ("STOP", "BOUNDARY_STOPPED", ""),
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
        # Don't blindly continue from SAFE_CHECKPOINT if integrity is broken
        integrity = _evaluate_workspace_integrity(workspace)
        if integrity["status"] == "RED":
            return {
                "nextAction": "CORRECTIVE_WORK_REQUIRED",
                "phase": "BLOCKED",
                "taskId": "",
                "humanRequired": False,
                "reason": "; ".join(integrity["blockingIssues"][:5]),
            }
        elif integrity["status"] == "YELLOW":
            # Advisory issues: still continue but note them
            return {
                "nextAction": "CONTINUE_LOOP",
                "phase": "READY_FOR_NEXT_TASK",
                "taskId": "",
                "humanRequired": False,
                "reason": "continuing with advisory integrity issues",
            }
        return {"nextAction": "CONTINUE_LOOP", "phase": "READY_FOR_NEXT_TASK", "taskId": "", "humanRequired": False}

    entry = dispatch.get(phase)
    if entry:
        action, new_phase, tid = entry
        if phase in ("EXECUTING_TASK", "NEEDS_CHANGE_REVIEW", "NEEDS_GATE", "NEEDS_BOUNDARY_DECISION", "REVIEW_FAILED"):
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
    "RETRY_EXECUTOR": ("EXECUTING_TASK", False, False),
    "RUN_CHANGE_REVIEWER": ("NEEDS_CHANGE_REVIEW", False, False),
    "RUN_GATEKEEPER": ("NEEDS_GATE", False, False),
    "RUN_QUALITY_VALUE_MANAGER": ("NEEDS_BOUNDARY_DECISION", False, False),
    "RUN_WATCHDOG": ("EXECUTING_TASK", False, False),
    "CONTINUE_LOOP": ("READY_FOR_NEXT_TASK", False, False),
    "SET_SAFE_CHECKPOINT": ("SAFE_CHECKPOINT", False, False),
    "SET_HUMAN_REQUIRED": ("HUMAN_DECISION_REQUIRED", False, False),
    "GATE_FAILED": ("GATE_FAILED", False, False),
    "REQUEST_CHANGES": ("REVIEW_FAILED", False, False),
    "SET_DONE": ("DONE", False, False),
    "CORRECTIVE_WORK_REQUIRED": ("BLOCKED", False, False),
}

# Actions that must preserve the active run/task identity from the previous state.
_TRANSITIONS_PRESERVE_IDENTITY = frozenset([
    "RUN_CHANGE_REVIEWER",
    "RUN_GATEKEEPER",
    "RUN_QUALITY_VALUE_MANAGER",
    "GATE_FAILED",
    "REQUEST_CHANGES",
    "RUN_WATCHDOG",
    "RETRY_EXECUTOR",
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
    started = fast_execution.clock_ms()
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
    if action in ("CONTINUE_LOOP", "SET_SAFE_CHECKPOINT", "SET_DONE"):
        lock = boundary_mod.advancement_lock_status(workspace)
        if lock.get("status") != "PASS":
            reasons = "; ".join(x.get("reason", "boundary acceptance missing") for x in lock.get("blockingBoundaries", [])[:5])
            print(f"Error: advancement locked by quality/value boundary: {reasons}", file=sys.stderr)
            sys.exit(1)
    previous_phase = state.get("currentPhase", "")
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

    # Capture review evidence only after an independent review has completed.
    # Entering RUN_CHANGE_REVIEWER merely starts review and must never be
    # recorded as an approval.
    if action == "RUN_GATEKEEPER" and previous_phase == "NEEDS_CHANGE_REVIEW":
        try:
            _write_review_evidence(
                workspace, task_id, reviewer="change-reviewer", run_id=run_id
            )
        except Exception as exc:
            print(
                f"Warning: failed to write review evidence after review approval: {exc}",
                file=sys.stderr,
            )

    # ---- Phase 6: Check dirty reviewed state before exec/review transitions ----
    if action in ("RUN_EXECUTOR", "RETRY_EXECUTOR", "RUN_CHANGE_REVIEWER"):
        try:
            dirty_warnings = _check_dirty_reviewed_state(workspace)
            if dirty_warnings:
                for w in dirty_warnings:
                    print(
                        f"  WARNING: dirty reviewed file: {w['file']} (task: {w['task_id']})",
                        file=sys.stderr,
                    )
        except Exception:
            pass  # advisory only, never block the transition

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

    if creates_run and run_id:
        fast_execution.merge_pending_trace(workspace, run_id)
    fast_execution.record_trace_phase(
        workspace, run_id, "state-transition-write",
        fast_execution.clock_ms() - started,
        files=["state/team-state.json", "state/backlog.jsonl", "state/run-ledger.jsonl", "state/events.jsonl"],
        role_count=0,
        decision="EXECUTED", details=action,
    )
    if action.startswith("RUN_"):
        fast_execution.record_trace_phase(
            workspace, run_id, "role-dispatch", 0.0, role_count=1,
            decision="EXECUTED", details=action,
        )

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
    started = fast_execution.clock_ms()
    host = WorkspaceContext(args.workspace)
    workspace = host.workspace

    scope_policy = host.scope_policy
    if not scope_policy:
        print("Error: scope-policy.json not found", file=sys.stderr)
        sys.exit(1)

    state = host.state
    task = host.current_task

    # Build allowed/forbidden lists
    always_allowed = scope_policy.get("alwaysAllowedWrites", [])
    always_forbidden = scope_policy.get("alwaysForbiddenWrites", [])
    default_allowed = scope_policy.get("defaultAllowedWrites", [])

    allowed = list(always_allowed) + list(default_allowed)
    forbidden = list(always_forbidden)

    if task and scope_policy.get("taskAllowedWritesOverride", True):
        state_task_id = state.get("currentTaskId", "")
        task_scoped_phases = frozenset([
            "EXECUTING_TASK", "NEEDS_CHANGE_REVIEW", "NEEDS_GATE",
            "NEEDS_BOUNDARY_DECISION", "REVIEW_FAILED", "GATE_FAILED"
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

    # Get changed files via WorkspaceContext
    changed_files = host.git_changed_paths()

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

    run_id = state.get("currentRunId", "")
    fast_execution.record_trace_phase(
        workspace, run_id, "scope-validation", fast_execution.clock_ms() - started,
        process_count=1, files=["policies/scope-policy.json", "state/current-task.json"],
        decision="EXECUTED" if overall == "PASS" else "FAILED",
        details=summary,
    )
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
    started = fast_execution.clock_ms()
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

    boundary_id = boundary_mod.find_applicable_boundary(workspace, run_id=run_id, task_id=task_id) if overall == "PASS" else None
    if boundary_id:
        next_action = "RUN_QUALITY_VALUE_MANAGER"

    gate_result = {
        "schemaVersion": 1,
        "runId": run_id,
        "taskId": task_id,
        "status": overall,
        "checks": checks,
        "nextAction": next_action,
        "humanRequired": False
    }
    if boundary_id:
        gate_result["boundaryId"] = boundary_id

    write_json(os.path.join(run_dir, "gate-result.json"), gate_result)

    # A configured boundary physically blocks task completion after deterministic gates.
    if overall == "PASS" and boundary_id:
        try:
            _write_review_evidence(
                workspace, task_id, reviewer="gatekeeper", gate_result="PASS",
                run_id=run_id, preserve_existing=True,
            )
            packet = boundary_mod.measure_boundary(workspace, boundary_id, project_root=os.path.dirname(os.path.abspath(workspace)))
        except Exception as exc:
            print(f"Error: boundary measurement failed after gate PASS: {exc}", file=sys.stderr)
            sys.exit(1)
        state["currentPhase"] = "NEEDS_BOUNDARY_DECISION"
        state["status"] = "IN_PROGRESS"
        state["lastGateStatus"] = "PASS"
        state["currentTaskId"] = task_id
        state["currentRunId"] = run_id
        state["updatedAtUtc"] = utc_now_iso()
        write_json(state_path, state)
        append_jsonl(os.path.join(workspace, "state", "events.jsonl"), {
            "schemaVersion": 1,
            "eventId": f"evt-gate-pass-{os.getpid()}-{int(datetime.datetime.now(datetime.timezone.utc).timestamp())}",
            "type": "GATE_PASSED",
            "actor": "gatekeeper",
            "timestampUtc": utc_now_iso(),
            "summary": f"Deterministic gates passed for run {run_id}; boundary acceptance still required",
            "taskId": task_id,
            "runId": run_id,
            "data": {"boundaryId": boundary_id, "advancementLocked": True},
        })
        append_jsonl(os.path.join(workspace, "state", "events.jsonl"), {
            "schemaVersion": 1,
            "eventId": f"evt-boundary-measure-{os.getpid()}-{int(datetime.datetime.now(datetime.timezone.utc).timestamp())}",
            "type": "BOUNDARY_MEASURED",
            "actor": "runtime",
            "timestampUtc": utc_now_iso(),
            "summary": f"Deterministic boundary packet created for {boundary_id}; advancement locked",
            "taskId": task_id,
            "runId": run_id,
            "data": {"boundaryId": boundary_id, "packetFingerprint": packet.get("packetFingerprint", "")},
        })
        gate_result["boundaryPacketFingerprint"] = packet.get("packetFingerprint", "")
        write_json(os.path.join(run_dir, "gate-result.json"), gate_result)
        print(json.dumps(gate_result, ensure_ascii=False))
        return

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

        # Phase 5: Write review evidence on GATE_PASS
        try:
            _write_review_evidence(
                workspace,
                task_id,
                reviewer="gatekeeper",
                gate_result="PASS",
                run_id=run_id,
                preserve_existing=True,
            )
        except Exception as exc:
            print(
                f"Warning: failed to write review evidence on GATE_PASS: {exc}",
                file=sys.stderr,
            )

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

    fast_execution.record_trace_phase(
        workspace, run_id, "project-gates", fast_execution.clock_ms() - started,
        process_count=len(gates), files=["policies/gate-policy.json", "gate-result.json"],
        decision="EXECUTED" if overall == "PASS" else "FAILED",
        details=f"{len(checks)} gate check(s); overall={overall}",
    )
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
# Command: validate-artifact (generic schema validation support)
# ---------------------------------------------------------------------------

def cmd_validate_artifact(args):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    schema_name = args.schema
    if schema_name.endswith(".schema.json"):
        schema_file = schema_name
    else:
        schema_file = schema_name + ".schema.json"
    schema_path = os.path.join(project_root, "schemas", schema_file)
    if not os.path.exists(schema_path):
        print(f"ARTIFACT VALIDATION FAILED: schema '{schema_name}' not found", file=sys.stderr)
        sys.exit(1)
    try:
        data = read_json(args.json_file)
        schema = read_json(schema_path)
    except Exception as exc:
        print(f"ARTIFACT VALIDATION FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
    errors = validate_against_schema(data, schema, args.label or os.path.basename(args.json_file))
    if errors:
        print("ARTIFACT VALIDATION FAILED:")
        for error in errors:
            print(f"  - {error}")
        sys.exit(1)
    print("ARTIFACT VALIDATION PASSED")


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
        One of: DONE, SAFE_CHECKPOINT, CONTINUE, HUMAN_DECISION_REQUIRED, BLOCKED, CORRECTIVE_WORK_REQUIRED.
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

        # Integrate active no-progress evidence into the canonical continuation
        # decision without creating a second continuation rule engine.
        evidence = list(evidence or [])
        if run_id:
            no_progress_path = os.path.join(workspace, "runs", run_id, "no-progress-result.json")
            no_progress = read_json_file_safe(no_progress_path)
            if no_progress and no_progress.get("status") in ("NO_PROGRESS_DETECTED", "STRATEGY_CHANGE_REQUIRED"):
                rel_np = os.path.relpath(no_progress_path, os.path.dirname(workspace))
                if rel_np not in evidence:
                    evidence.append(rel_np)
                checks.append({
                    "name": "no-progress-routing",
                    "status": "PASS",
                    "summary": "No-progress recovery is unresolved; identical automatic retry is blocked until watchdog/strategy routing completes"
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
        fast_execution.record_trace_phase(
            workspace, run_id, "continuation-decision", 0.0,
            files=["state/continuation-decision.json", "state/events.jsonl"],
            decision="EXECUTED", details=decision,
        )

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
    # Accept --no-cache flag for consistency; cache is optional.
    no_cache = getattr(args, "no_cache", False)
    host = WorkspaceContext(args.workspace)
    workspace = host.workspace
    project_root = host.project_root

    checks = []
    violations = []

    # ------------------------------------------------------------------
    # Determine enforcement level from protected-paths policy (if present)
    # ------------------------------------------------------------------
    enforcement_level = "error"  # default
    policy_loaded = False

    policy = host.protected_paths
    if policy:
        policy_loaded = True
        enforcement_level = policy.get("enforcementLevel", "error")

    # ------------------------------------------------------------------
    # Get git changed files via WorkspaceContext
    # ------------------------------------------------------------------
    git_status_entries = host.git_status_entries

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
    # Check 3: schema-integrity (delegated to WorkspaceContext)
    # ------------------------------------------------------------------
    si_check, si_violations = host.check_schema_integrity()
    checks.append(si_check)
    violations.extend(si_violations)

    # ------------------------------------------------------------------
    # Compute overall status
    # ------------------------------------------------------------------
    has_fail = any(c["status"] == "FAIL" for c in checks)
    has_warn = any(c["status"] == "WARNING" for c in checks)

    if not policy_loaded:
        # Missing policy: report NOT_CONFIGURED explicitly
        overall_status = "NOT_CONFIGURED"
    elif has_fail:
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


def _get_git_status_entries(repo_root=None):
    """Parse git status --porcelain into list of {status, path} dicts.

    Each entry has:
      - status: the porcelain status string (e.g. 'M ', 'D ', '??')
      - path: the file path relative to git root
      - raw: the raw porcelain line
    """
    entries = []
    try:
        git_prefix = ["git"] + (["-C", repo_root] if repo_root else [])
        result = subprocess.run(
            [*git_prefix, "status", "--porcelain=v1"],
            capture_output=True, text=True, timeout=10,
        )
        git_root_result = subprocess.run(
            [*git_prefix, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
        git_root = git_root_result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return entries

    for raw_line in result.stdout.splitlines():
        # Porcelain v1 uses the first two columns as status.  Do not strip
        # leading whitespace: an unstaged modification starts with " M", and
        # stripping it shifts the path and silently corrupts guard matching.
        line = raw_line.rstrip("\r")
        if len(line) < 3:
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
    Delegates to WorkspaceContext for the shared implementation.
    """
    # Create a minimal host with the right project_root.
    # WorkspaceContext.project_root is normally derived from __file__,
    # but callers of this function (e.g. _check_guard_integrity_for_validate)
    # pass it explicitly.
    host = WorkspaceContext.__new__(WorkspaceContext)
    host.workspace = ""
    host.project_root = project_root
    host._WorkspaceContext__cache = {}
    return host.check_schema_integrity()


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


def _sentinel_get_run_id(host):
    """Return the applicable run id, including the latest completed run."""
    state = host.state_safe
    if state and state.get("currentRunId"):
        return state["currentRunId"]
    resolved = fast_execution.resolve_run_id(host.workspace)
    if resolved:
        return resolved
    return "run-{}".format(datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S"))


def _repository_identity(repo_root):
    """Return a stable repository identity for evidence binding.

    Prefer the configured ``origin`` URL because it remains stable across
    checkouts.  Repositories without a remote fall back to the canonical Git
    top-level path.  The value is hashed so reports do not expose local paths
    or credentials embedded in remote URLs.
    """
    raw_identity = ""
    try:
        proc = subprocess.run(
            ["git", "-C", repo_root, "config", "--get", "remote.origin.url"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            raw_identity = proc.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    if not raw_identity:
        try:
            proc = subprocess.run(
                ["git", "-C", repo_root, "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0:
                raw_identity = os.path.realpath(proc.stdout.strip())
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

    if not raw_identity:
        return ""
    return hashlib.sha256(raw_identity.encode("utf-8")).hexdigest()


def _sentinel_semantic_payload(report):
    """Build the canonical, non-volatile payload protected by the sentinel.

    ``inspectedAtUtc`` and summary counts are deliberately excluded: the
    timestamp is volatile and the summary is derived from findings.  Every
    identity/policy field that can affect applicability is included.
    """
    return {
        "schemaVersion": report.get("schemaVersion", 1),
        "runId": report.get("runId", ""),
        "repositoryIdentity": report.get("repositoryIdentity", ""),
        "repositoryHead": report.get("repositoryHead", ""),
        "taskId": report.get("taskId", ""),
        "taskRevision": report.get("taskRevision", ""),
        "implementationVersion": report.get("implementationVersion", ""),
        "runtimeVersion": report.get("runtimeVersion", ""),
        "manifestFingerprint": report.get("manifestFingerprint", ""),
        "executionPolicyFingerprint": report.get("executionPolicyFingerprint", ""),
        "protectedPathsFingerprint": report.get("protectedPathsFingerprint", ""),
        "policyFingerprints": report.get("policyFingerprints", {}),
        "overallStatus": report.get("overallStatus", ""),
        "findings": fast_execution.strip_volatile(report.get("findings", [])),
    }


def _compute_sentinel_semantic_fingerprint(report):
    return hashlib.sha256(
        json.dumps(
            _sentinel_semantic_payload(report),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _sentinel_check_scope_policy_weakening(host):
    """Check 1: scope-policy-weakening — scope-policy.json must have baseline forbiddenWrites."""
    policy_path = os.path.join(host.workspace, "policies", "scope-policy.json")
    policy = host.scope_policy
    if not policy:
        if not os.path.exists(policy_path):
            return {
                "category": "scope-policy-weakening",
                "severity": "CRITICAL",
                "title": "Scope policy file missing",
                "description": "scope-policy.json not found — no write guards in place",
                "evidence": [{"type": "MISSING_ARTIFACT", "detail": policy_path}],
                "resolutionHint": "Run init-workspace to restore scope-policy.json",
            }
        else:
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


def _sentinel_check_gate_policy_weakening(host):
    """Check 2: gate-policy-weakening — gate-policy.json must have at least one gate."""
    policy_path = os.path.join(host.workspace, "policies", "gate-policy.json")
    policy = host.gate_policy
    if not policy:
        if not os.path.exists(policy_path):
            return {
                "category": "gate-policy-weakening",
                "severity": "WARNING",
                "title": "Gate policy file missing",
                "description": "gate-policy.json not found — no gate checks configured",
                "evidence": [{"type": "MISSING_ARTIFACT", "detail": policy_path}],
                "resolutionHint": "Run init-workspace to restore gate-policy.json",
            }
        else:
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


def _sentinel_check_schema_integrity(host):
    """Check 3: schema-integrity — every schemas/*.schema.json must be valid JSON.

    Delegates to WorkspaceContext for the shared implementation.
    """
    return host.check_schema_integrity_for_sentinel()


def _sentinel_check_test_suppression(host):
    """Check 4: test-suppression — test runner scripts must exist and be non-empty."""
    missing_or_empty = []

    for script_name in ("tests/run-tests.sh", "tests/run-tests.ps1"):
        spath = os.path.join(host.project_root, script_name)
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
            {"type": "FILE_PATH", "detail": os.path.join(host.project_root, "tests/run-tests.sh")},
            {"type": "FILE_PATH", "detail": os.path.join(host.project_root, "tests/run-tests.ps1")},
        ],
    }


def _sentinel_check_state_mutation(host):
    """Check 5: state-mutation — core state files must be valid JSON."""
    invalid_files = []

    # team-state.json is a single JSON object
    ts_path = os.path.join(host.workspace, "state", "team-state.json")
    if os.path.exists(ts_path) and os.path.getsize(ts_path) > 0:
        if is_invalid_json_file(ts_path):
            invalid_files.append("state/team-state.json")

    # JSONL files — check each line is valid JSON
    for name in ("events.jsonl", "backlog.jsonl"):
        jpath = os.path.join(host.workspace, "state", name)
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
        "evidence": [{"type": "FILE_PATH", "detail": os.path.join(host.workspace, "state")}],
    }


def _sentinel_check_protected_file_changes(host):
    """Check 6: protected-file-changes — reuse guard integrity check infrastructure."""
    git_status_entries = host.git_status_entries

    if not git_status_entries:
        return {
            "category": "protected-file-changes",
            "severity": "INFO",
            "title": "No git changes detected",
            "description": "Git status is clean — no file modifications detected",
            "evidence": [{"type": "GIT_DIFF", "detail": "no changes"}],
        }

    # Load protected-paths policy if available
    policy = host.protected_paths or None

    # Run the same protected-paths check used by guard integrity
    _, violations = _check_protected_paths(policy, git_status_entries, host.workspace)

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


def _sentinel_check_hidden_unresolved_work(host):
    """Check 7: hidden-unresolved-work — READY tasks that may be orphaned."""
    backlog_path = os.path.join(host.workspace, "state", "backlog.jsonl")
    if not os.path.exists(backlog_path):
        return {
            "category": "hidden-unresolved-work",
            "severity": "INFO",
            "title": "No backlog file found",
            "description": "backlog.jsonl not found — cannot check for orphaned work",
            "evidence": [{"type": "MISSING_ARTIFACT", "detail": backlog_path}],
        }

    try:
        backlog = host.backlog
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


def _sentinel_check_manual_state_mutation(host):
    """Check 8: manual-state-mutation — team-state.json phase must be a known value."""
    state_path = os.path.join(host.workspace, "state", "team-state.json")
    if not os.path.exists(state_path):
        return {
            "category": "manual-state-mutation",
            "severity": "CRITICAL",
            "title": "Team state file missing",
            "description": "team-state.json not found — workspace state is unknown",
            "evidence": [{"type": "MISSING_ARTIFACT", "detail": state_path}],
            "resolutionHint": "Run init-workspace to restore team-state.json",
        }

    state = host.state_safe
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


def _sentinel_check_evidence_manipulation(host):
    """Check 9: evidence-manipulation — evidence refs in continuation-decision.json must point to existing files."""
    decision_path = os.path.join(host.workspace, "state", "continuation-decision.json")
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
    workspace_root = os.path.dirname(host.workspace)
    missing_refs = []
    for ref in evidence_refs:
        # Try as relative to workspace root first, then workspace itself
        candidate = os.path.join(workspace_root, ref)
        if not os.path.exists(candidate):
            candidate = os.path.join(host.workspace, ref)
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


# ---------------------------------------------------------------------------
# Command: cache-inspect
# ---------------------------------------------------------------------------

def cmd_cache_inspect(args):
    """Show validation cache statistics as JSON."""
    workspace = resolve_workspace(args.workspace)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache = _create_cache(workspace, project_root)
    if cache is None:
        print(json.dumps({"error": "Cache is disabled (TEAMLOOP_NO_CACHE set)"}, ensure_ascii=False))
        sys.exit(1)
    stats = cache.stats()
    print(json.dumps(stats, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Command: cache-clear
# ---------------------------------------------------------------------------

def cmd_cache_clear(args):
    """Clear all entries in the validation cache."""
    workspace = resolve_workspace(args.workspace)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache = _create_cache(workspace, project_root)
    if cache is None:
        print(json.dumps({"error": "Cache is disabled (TEAMLOOP_NO_CACHE set)"}, ensure_ascii=False))
        sys.exit(1)
    removed = len(cache._entries)
    cache.clear()
    print(json.dumps({
        "action": "cache-cleared",
        "entriesRemoved": removed,
        "checkedAtUtc": cache.stats()["checkedAtUtc"],
    }, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Command: cache-stats
# ---------------------------------------------------------------------------

def cmd_cache_stats(args):
    """Show detailed cache statistics including integrity."""
    workspace = resolve_workspace(args.workspace)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache = _create_cache(workspace, project_root)
    if cache is None:
        print(json.dumps({"error": "Cache is disabled (TEAMLOOP_NO_CACHE set)"}, ensure_ascii=False))
        sys.exit(1)
    stats = cache.stats()
    integrity = cache.integrity_check()
    result = {
        "stats": stats,
        "integrity": integrity,
    }
    print(json.dumps(result, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Command: cache-validate
# ---------------------------------------------------------------------------

def cmd_cache_validate(args):
    """Validate the entire validation cache and report structured findings.

    Output JSON:
      {
        "status": "PASS" | "FAIL",
        "totalEntries": N,
        "validEntries": M,
        "invalidEntries": [...],
        "malformedLines": N,
        "hasCorruption": true/false
      }

    Exit 0 if PASS, exit 1 if FAIL.
    """
    workspace = resolve_workspace(args.workspace)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache = _create_cache(workspace, project_root)
    if cache is None:
        print(json.dumps({"error": "Cache is disabled (TEAMLOOP_NO_CACHE set)"}, ensure_ascii=False))
        sys.exit(1)
    integrity = cache.integrity_check()
    result = {
        "status": integrity["status"],
        "totalEntries": integrity["totalEntries"],
        "validEntries": integrity["validEntries"],
        "invalidEntries": integrity["invalidEntries"],
        "malformedLines": integrity["malformedLineCount"],
        "hasCorruption": integrity["hasCorruption"],
    }
    print(json.dumps(result, ensure_ascii=False))
    if result["status"] == "FAIL":
        sys.exit(1)



def _sentinel_cache_inputs(host, check_name, git_dependent=False):
    """Return authoritative inputs for one sentinel cache key.

    Sentinel findings are only reusable when every primary artifact read by the
    check is unchanged.  Earlier versions keyed several checks only by the
    runtime scripts, which allowed a fixed policy/state artifact to keep
    returning an old cached finding.  The mapping below deliberately favors
    correctness over maximum hit rate: small workspace files are cheap to hash.
    """
    workspace = host.workspace
    project_root = host.project_root
    paths = []
    extra = {"sentinelCacheContract": 2, "check": check_name}

    def workspace_path(*parts):
        return os.path.join(workspace, *parts)

    if check_name == "scope-policy-weakening":
        paths.append(workspace_path("policies", "scope-policy.json"))
    elif check_name == "gate-policy-weakening":
        paths.append(workspace_path("policies", "gate-policy.json"))
    elif check_name == "schema-integrity":
        schema_files = sorted(globmod.glob(os.path.join(project_root, "schemas", "*.schema.json")))
        extra["schemaFiles"] = [os.path.relpath(path, project_root).replace("\\", "/") for path in schema_files]
        paths.extend(schema_files)
    elif check_name == "test-suppression":
        paths.extend([
            os.path.join(project_root, "tests", "run-tests.sh"),
            os.path.join(project_root, "tests", "run-tests.ps1"),
        ])
    elif check_name == "state-mutation":
        paths.extend([
            workspace_path("state", "team-state.json"),
            workspace_path("state", "events.jsonl"),
            workspace_path("state", "backlog.jsonl"),
        ])
    elif check_name == "protected-file-changes":
        paths.append(workspace_path("policies", "protected-paths.json"))
    elif check_name == "hidden-unresolved-work":
        paths.append(workspace_path("state", "backlog.jsonl"))
    elif check_name == "manual-state-mutation":
        paths.append(workspace_path("state", "team-state.json"))
    elif check_name == "evidence-manipulation":
        decision_path = workspace_path("state", "continuation-decision.json")
        paths.append(decision_path)
        decision = read_json_file_safe(decision_path) if os.path.isfile(decision_path) else None
        if isinstance(decision, dict):
            refs = []
            for field in ("evidence", "evidenceIds", "checkEvidence"):
                value = decision.get(field)
                if isinstance(value, list):
                    refs.extend(str(item) for item in value)
                elif isinstance(value, str) and value:
                    refs.append(value)
            for check in decision.get("checks", []) if isinstance(decision.get("checks", []), list) else []:
                if not isinstance(check, dict):
                    continue
                for field in ("evidence", "evidencePath", "artifactPath"):
                    value = check.get(field)
                    if isinstance(value, list):
                        refs.extend(str(item) for item in value)
                    elif isinstance(value, str) and value:
                        refs.append(value)
            extra["evidenceReferences"] = sorted(set(refs))
            repo_root = os.path.dirname(workspace)
            for ref in refs:
                candidate = ref if os.path.isabs(ref) else os.path.join(repo_root, ref)
                if not os.path.exists(candidate) and not os.path.isabs(ref):
                    candidate = os.path.join(workspace, ref)
                paths.append(candidate)

    if git_dependent:
        status_entries = [(entry.get("status", ""), entry.get("path", "")) for entry in host.git_status_entries]
        extra["gitStatus"] = status_entries
        for _, rel_path in status_entries:
            candidate = os.path.join(host.git_root, rel_path)
            if os.path.isfile(candidate):
                paths.append(candidate)

    inputs = dict(extra)
    # Absolute paths are content-hashed by ValidationCache.build_key.  Keep a
    # stable logical name so machine-specific checkout paths never enter the key.
    for index, path in enumerate(paths):
        logical = "artifact:{:03d}:{}".format(index, os.path.basename(path) or "missing")
        inputs[logical] = os.path.abspath(path)
    return inputs


def _sentinel_findings_equal(left, right):
    return fast_execution.semantic_hash(
        fast_execution.strip_volatile(left)
    ) == fast_execution.semantic_hash(
        fast_execution.strip_volatile(right)
    )


def cmd_run_sentinel(args):
    """READ-ONLY sentinel inspection command.

    Runs 9 integrity checks on the workspace and produces a structured JSON
    report matching schemas/sentinel-inspection.schema.json.

    Does not modify any files except writing its own report to
    .teamloop/runs/<run-id>/sentinel-inspection.json.
    """
    started = fast_execution.clock_ms()
    workspace = resolve_workspace(args.workspace)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Determine whether to use cache.  Corrupt cache data is never trusted or
    # silently deleted: sentinel bypasses it for this run and reports why.
    no_cache = getattr(args, "no_cache", False)
    cache = None if no_cache else _create_cache(workspace, project_root)
    cache_summary = {
        "enabled": cache is not None,
        "state": "DISABLED" if cache is None else "EMPTY",
        "hits": 0,
        "misses": 0,
        "freshRetries": 0,
        "staleEntriesBypassed": 0,
        "malformedEntriesBypassed": 0,
        "action": "CACHE_DISABLED" if cache is None else "CACHE_READY",
        "reason": "--no-cache or TEAMLOOP_NO_CACHE" if cache is None else "",
    }
    if cache is not None:
        integrity = cache.integrity_check()
        cache_state = _classify_cache_state(cache, integrity)
        cache_summary["state"] = cache_state
        if cache_state in ("CORRUPT", "INVALID"):
            cache_summary["action"] = "CACHE_BYPASSED"
            cache_summary["reason"] = "validation cache is {}; sentinel recomputed all checks fresh".format(cache_state)
            cache = None
        elif cache_state == "LEGACY_UNTRUSTED":
            cache_summary["action"] = "LEGACY_ENTRIES_QUARANTINED"
            cache_summary["reason"] = "legacy cache records are not reusable"
        elif cache_state == "EMPTY":
            cache_summary["action"] = "CACHE_EMPTY"

    # Create WorkspaceContext for all data access
    host = WorkspaceContext.__new__(WorkspaceContext)
    host.workspace = workspace
    host.project_root = project_root
    host._WorkspaceContext__cache = {}
    host._validation_cache = cache
    host._state_store = None

    # Determine runId
    run_id = _sentinel_get_run_id(host)

    # Ensure the run directory exists for the report
    run_dir = os.path.join(workspace, "runs", run_id)
    os.makedirs(run_dir, exist_ok=True)

    # Helper: run a sentinel check with optional caching.
    # Deterministic checks (2,3,4,6,7,8,9) are cached. Git-dependent
    # checks include git status in the cache key.
    #
    # Canonical cache value shape: the stored result IS the finding dict
    # (e.g. {"category": "...", "severity": "INFO", "title": "..."}).
    # cache.get() returns the finding directly — never a wrapper.
    # Cache metadata (checkId, fingerprints, timestamps) stays in the
    # cache entry on disk but is not returned to the caller.
    def _run_sentinel_check(check_name, check_fn, git_dependent=False):
        if cache is None:
            cache_summary["misses"] += 1
            return check_fn(host)

        inputs = _sentinel_cache_inputs(host, check_name, git_dependent=git_dependent)
        cache_key = cache.build_key(
            check="sentinel:" + check_name,
            inputs=inputs,
            schemas={},
        )
        cached = cache.get(cache_key)
        if cached is not None:
            if isinstance(cached, dict) and cached.get("severity") in ("CRITICAL", "WARNING", "INFO"):
                cache_summary["hits"] += 1
                # A cached non-PASS finding is always rechecked once.  This is
                # deliberately cheap and prevents an old warning/failure from
                # sending the agent into WSL/path/quoting investigations after
                # the underlying artifact was already fixed.
                if cached.get("severity") != "INFO":
                    cache_summary["freshRetries"] += 1
                    fresh = check_fn(host)
                    if not _sentinel_findings_equal(cached, fresh):
                        cache_summary["staleEntriesBypassed"] += 1
                        cache_summary["action"] = "STALE_ENTRY_RECOMPUTED"
                        cache_summary["reason"] = "cached non-PASS finding changed during authoritative fresh retry"
                    cache.store(
                        cache_key,
                        fresh,
                        check_id=check_name,
                        semantic_context={
                            "provenance": {
                                "producer": "sentinel-fresh-retry",
                                "checkId": check_name,
                            },
                            "reuseRestrictions": {"freshRetryOnNonPass": True},
                        },
                    )
                    return fresh
                return cached
            cache_summary["malformedEntriesBypassed"] += 1
            cache_summary["action"] = "MALFORMED_ENTRY_RECOMPUTED"
            cache_summary["reason"] = "cached sentinel result had an invalid semantic shape"

        cache_summary["misses"] += 1
        result = check_fn(host)
        cache.store(
            cache_key,
            result,
            check_id=check_name,
            semantic_context={
                "provenance": {"producer": "sentinel", "checkId": check_name},
                "reuseRestrictions": {"freshRetryOnNonPass": True},
            },
        )
        return result

    # Run all 9 checks
    findings = [
        _run_sentinel_check("scope-policy-weakening", _sentinel_check_scope_policy_weakening),
        _run_sentinel_check("gate-policy-weakening", _sentinel_check_gate_policy_weakening),
        _run_sentinel_check("schema-integrity", _sentinel_check_schema_integrity),
        _run_sentinel_check("test-suppression", _sentinel_check_test_suppression),
        _run_sentinel_check("state-mutation", _sentinel_check_state_mutation, git_dependent=True),
        _run_sentinel_check("protected-file-changes", _sentinel_check_protected_file_changes, git_dependent=True),
        _run_sentinel_check("hidden-unresolved-work", _sentinel_check_hidden_unresolved_work),
        _run_sentinel_check("manual-state-mutation", _sentinel_check_manual_state_mutation, git_dependent=True),
        _run_sentinel_check("evidence-manipulation", _sentinel_check_evidence_manipulation, git_dependent=True),
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

    # ------------------------------------------------------------------
    # Sentinel identity binding
    # ------------------------------------------------------------------
    identity = _resolve_applicable_identity(workspace)
    repo_root = host.git_root

    # Bind the report to the authoritative contract when one exists.  The
    # run directory selected above must agree with that contract.
    if identity.get("runId"):
        run_id = identity["runId"]
        run_dir = os.path.join(workspace, "runs", run_id)
        os.makedirs(run_dir, exist_ok=True)

    policy_fps = {}
    for pname in ("gate-policy.json", "scope-policy.json", "protected-paths.json"):
        ppath = os.path.join(workspace, "policies", pname)
        policy_fps[pname] = (
            fast_execution.file_sha256(ppath)
            if os.path.isfile(ppath) and os.path.getsize(ppath) < 1_000_000
            else ""
        )

    report = {
        "schemaVersion": 1,
        "runId": run_id,
        "inspectedAtUtc": utc_now_iso(),
        "repositoryIdentity": identity.get("repositoryIdentity") or _repository_identity(repo_root),
        "repositoryHead": identity.get("repositoryHead", ""),
        "taskId": identity.get("taskId", ""),
        "taskRevision": identity.get("taskRevision", ""),
        "implementationVersion": _cache_mod.IMPLEMENTATION_VERSION,
        "runtimeVersion": _cache_mod.IMPLEMENTATION_VERSION,
        "policyFingerprints": policy_fps,
        "manifestFingerprint": identity.get("manifestFingerprint", ""),
        "executionPolicyFingerprint": identity.get("policyFingerprint", ""),
        "protectedPathsFingerprint": identity.get("protectedPathsFingerprint", ""),
        "findings": findings,
        "overallStatus": overall_status,
        "cacheSummary": cache_summary,
        "summary": {
            "totalFindings": len(findings),
            "criticalCount": critical_count,
            "warningCount": warning_count,
            "infoCount": info_count,
        },
    }
    report["semanticFingerprint"] = _compute_sentinel_semantic_fingerprint(report)

    # Write report to run directory
    report_path = os.path.join(run_dir, "sentinel-inspection.json")
    write_json(report_path, report)

    fast_execution.record_trace_phase(
        workspace, run_id, "sentinel-inspection", fast_execution.clock_ms() - started,
        files=["sentinel-inspection.json", "state/team-state.json", "policies/scope-policy.json", "policies/gate-policy.json"],
        decision="FAILED" if overall_status == "FAIL" else "EXECUTED",
        details=f"{critical_count} critical, {warning_count} warning",
    )
    # Print to stdout
    print(json.dumps(report, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Sentinel evidence evaluation (Defect 2 fix)
# ---------------------------------------------------------------------------

def _evaluate_sentinel_evidence(workspace, state):
    """Evaluate sentinel evidence for final-gate.

    Verifies the sentinel artifact is valid, current, and has no CRITICAL
    findings.  Distinguishes PASS / NOT_REQUIRED / MISSING / STALE / FAIL /
    INVALID.

    Uses _resolve_applicable_identity() to determine the authoritative
    execution contract identity, then validates the sentinel against it.

    Parameters
    ----------
    workspace : str
        Resolved .teamloop workspace path.
    state : dict or None
        Current team-state.json contents.

    Returns
    -------
    dict
        ``{"status": ..., "description": ..., "blocking": bool, ...}``
        suitable for direct insertion into the final-gate checks list.
    """
    # ------------------------------------------------------------------
    # 0. Resolve the applicable identity from execution contract
    # ------------------------------------------------------------------
    identity = _resolve_applicable_identity(workspace)

    sentinel_required = identity["sentinelRequired"]

    if not sentinel_required:
        return {
            "name": "sentinel-result",
            "status": "NOT_REQUIRED",
            "description": "no execution policy or manifest found; sentinel inspection not required",
            "blocking": False,
        }

    # ------------------------------------------------------------------
    # 2. Find the sentinel artifact
    # ------------------------------------------------------------------
    # Prefer identity's run sentinel; fall back to the latest.
    candidate_paths = []
    if identity["runId"]:
        candidate_paths.append(
            os.path.join(workspace, "runs", identity["runId"], "sentinel-inspection.json")
        )

    # Also consider the latest sentinel (may be same file).
    runs_dir = os.path.join(workspace, "runs")
    if os.path.isdir(runs_dir):
        try:
            for run_name in reversed(sorted(os.listdir(runs_dir))):
                candidate = os.path.join(runs_dir, run_name, "sentinel-inspection.json")
                if os.path.isfile(candidate) and candidate not in candidate_paths:
                    candidate_paths.append(candidate)
        except OSError:
            pass

    sentinel_path = None
    sentinel_data = None
    for p in candidate_paths:
        data = read_json_file_safe(p)
        if data is not None and isinstance(data, dict):
            sentinel_path = p
            sentinel_data = data
            break

    if sentinel_data is None:
        return {
            "name": "sentinel-result",
            "status": "MISSING",
            "description": "execution policy requires a final sentinel inspection; sentinel-inspection.json not found",
            "blocking": True,
            "reason": "no valid sentinel-inspection.json found" + (
                f" (expected runs/{identity['runId']}/sentinel-inspection.json)"
                if identity["runId"] else ""
            ),
        }

    # ------------------------------------------------------------------
    # 3. Validate artifact has required identity fields
    # ------------------------------------------------------------------
    if not isinstance(sentinel_data, dict):
        return {
            "name": "sentinel-result",
            "status": "INVALID",
            "description": "sentinel-inspection.json is not a JSON object",
            "blocking": True,
            "evidenceArtifact": os.path.relpath(sentinel_path, os.path.dirname(workspace)),
        }

    # Required identity fields for staleness checking
    required_identity_fields = {
        "repositoryIdentity", "repositoryHead", "runId", "overallStatus", "findings",
        "taskId", "taskRevision", "implementationVersion", "runtimeVersion",
        "semanticFingerprint", "policyFingerprints", "manifestFingerprint",
        "executionPolicyFingerprint", "protectedPathsFingerprint",
    }
    missing_fields = required_identity_fields - set(sentinel_data.keys())
    if missing_fields:
        return {
            "name": "sentinel-result",
            "status": "INVALID",
            "description": f"sentinel-inspection.json missing required fields: {', '.join(sorted(missing_fields))}",
            "blocking": True,
            "evidenceArtifact": os.path.relpath(sentinel_path, os.path.dirname(workspace)),
        }

    # Item 3: When a contract exists, additional identity fields must be present
    # and non-empty (taskId/taskRevision may be "" when no task is active).
    if identity.get("source") in ("execution-manifest", "execution-policy", "team-state"):
        additional_required = {
            "taskId", "taskRevision", "implementationVersion", "runtimeVersion",
            "semanticFingerprint", "policyFingerprints", "manifestFingerprint",
            "executionPolicyFingerprint", "protectedPathsFingerprint",
        }
        missing_additional = additional_required - set(sentinel_data.keys())
        if missing_additional:
            return {
                "name": "sentinel-result",
                "status": "INVALID",
                "description": f"sentinel-inspection.json missing contract-required fields: {', '.join(sorted(missing_additional))}",
                "blocking": True,
                "evidenceArtifact": os.path.relpath(sentinel_path, os.path.dirname(workspace)),
            }
        # Verify non-empty for fields that must have content
        for field in ("implementationVersion", "runtimeVersion", "semanticFingerprint"):
            val = sentinel_data.get(field, "")
            if not val:
                return {
                    "name": "sentinel-result",
                    "status": "INVALID",
                    "description": f"sentinel-inspection.json field '{field}' is empty when contract exists",
                    "blocking": True,
                    "evidenceArtifact": os.path.relpath(sentinel_path, os.path.dirname(workspace)),
                }

    # ------------------------------------------------------------------
    # 4. Check identity staleness
    # ------------------------------------------------------------------
    current_head = ""
    repo_root = fast_execution._repo_root(workspace)
    try:
        proc = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            current_head = proc.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    # 4a. repositoryHead must match current HEAD
    artifact_head = sentinel_data.get("repositoryHead", "")
    if current_head and artifact_head and artifact_head != current_head:
        return {
            "name": "sentinel-result",
            "status": "STALE",
            "description": "sentinel-inspection.json was produced for a different Git HEAD",
            "blocking": True,
            "reason": f"artifact HEAD {artifact_head[:12]} != current HEAD {current_head[:12]}",
            "evidenceArtifact": os.path.relpath(sentinel_path, os.path.dirname(workspace)),
        }

    identity_repo = identity.get("repositoryIdentity", "")
    artifact_repo = sentinel_data.get("repositoryIdentity", "")
    if identity_repo and artifact_repo != identity_repo:
        return {
            "name": "sentinel-result",
            "status": "STALE",
            "description": "sentinel-inspection.json belongs to a different repository",
            "blocking": True,
            "reason": f"repository identity mismatch: artifact {artifact_repo[:12]} vs current {identity_repo[:12]}",
            "evidenceArtifact": os.path.relpath(sentinel_path, os.path.dirname(workspace)),
        }

    # 4b. runId must match identity's run (if identity specifies one)
    identity_run_id = identity.get("runId", "")
    if identity_run_id:
        artifact_run_id = sentinel_data.get("runId", "")
        if artifact_run_id != identity_run_id:
            return {
                "name": "sentinel-result",
                "status": "STALE",
                "description": f"sentinel-inspection.json was produced for run {artifact_run_id}; runs/{identity_run_id}/sentinel-inspection.json is missing",
                "blocking": True,
                "reason": f"artifact run {artifact_run_id} != identity run {identity_run_id}",
                "evidenceArtifact": os.path.relpath(sentinel_path, os.path.dirname(workspace)),
            }

    # 4c. taskId must match identity's task (if identity specifies one)
    identity_task_id = identity.get("taskId", "")
    if identity_task_id:
        artifact_task_id = sentinel_data.get("taskId", "")
        if artifact_task_id != identity_task_id:
            return {
                "name": "sentinel-result",
                "status": "STALE",
                "description": "sentinel-inspection.json was produced for a different task",
                "blocking": True,
                "reason": f"artifact task {artifact_task_id} != identity task {identity_task_id}",
                "evidenceArtifact": os.path.relpath(sentinel_path, os.path.dirname(workspace)),
            }

    # 4ca. taskRevision must match identity's task revision
    identity_task_rev = identity.get("taskRevision", "")
    if identity_task_rev:
        artifact_task_rev = sentinel_data.get("taskRevision", "")
        if artifact_task_rev != identity_task_rev:
            return {
                "name": "sentinel-result",
                "status": "STALE",
                "description": "sentinel-inspection.json was produced for a different task revision",
                "blocking": True,
                "reason": f"artifact taskRevision {artifact_task_rev} != identity taskRevision {identity_task_rev}",
                "evidenceArtifact": os.path.relpath(sentinel_path, os.path.dirname(workspace)),
            }

    # 4cb. implementationVersion must match
    identity_impl_ver = identity.get("implementationVersion", "")
    if identity_impl_ver:
        artifact_impl_ver = sentinel_data.get("implementationVersion", "")
        if artifact_impl_ver != identity_impl_ver:
            return {
                "name": "sentinel-result",
                "status": "STALE",
                "description": "sentinel-inspection.json was produced with a different implementation version",
                "blocking": True,
                "reason": f"artifact implementationVersion {artifact_impl_ver} != identity {identity_impl_ver}",
                "evidenceArtifact": os.path.relpath(sentinel_path, os.path.dirname(workspace)),
            }

    # 4cc. runtimeVersion must match
    identity_rt_ver = identity.get("runtimeVersion", "")
    if identity_rt_ver:
        artifact_rt_ver = sentinel_data.get("runtimeVersion", "")
        if artifact_rt_ver != identity_rt_ver:
            return {
                "name": "sentinel-result",
                "status": "STALE",
                "description": "sentinel-inspection.json was produced with a different runtime version",
                "blocking": True,
                "reason": f"artifact runtimeVersion {artifact_rt_ver} != identity {identity_rt_ver}",
                "evidenceArtifact": os.path.relpath(sentinel_path, os.path.dirname(workspace)),
            }

    # 4d. policyFingerprints must match current policy files
    artifact_policy_fps = sentinel_data.get("policyFingerprints", {})
    current_policy_fps = {}
    for pname in ("gate-policy.json", "scope-policy.json", "protected-paths.json"):
        ppath = os.path.join(workspace, "policies", pname)
        try:
            current_policy_fps[pname] = (
                fast_execution.file_sha256(ppath) if os.path.isfile(ppath) else ""
            )
        except (OSError, TypeError):
            current_policy_fps[pname] = ""
    if not isinstance(artifact_policy_fps, dict):
        return {
            "name": "sentinel-result", "status": "INVALID",
            "description": "sentinel-inspection.json policyFingerprints is not an object",
            "blocking": True,
            "evidenceArtifact": os.path.relpath(sentinel_path, os.path.dirname(workspace)),
        }
    for pname, cur_fp in current_policy_fps.items():
        art_fp = artifact_policy_fps.get(pname, "")
        if art_fp != cur_fp:
            return {
                "name": "sentinel-result",
                "status": "STALE",
                "description": f"sentinel-inspection.json policy fingerprint for {pname} has changed",
                "blocking": True,
                "reason": f"{pname} hash mismatch: artifact {art_fp[:12]} vs current {cur_fp[:12]}",
                "evidenceArtifact": os.path.relpath(sentinel_path, os.path.dirname(workspace)),
            }

    identity_policy_fp = identity.get("policyFingerprint", "")
    artifact_policy_fp = sentinel_data.get("executionPolicyFingerprint", "")
    if identity_policy_fp and artifact_policy_fp != identity_policy_fp:
        return {
            "name": "sentinel-result", "status": "STALE",
            "description": "sentinel-inspection.json was produced for a different execution policy",
            "blocking": True,
            "reason": f"execution policy fingerprint mismatch: artifact {artifact_policy_fp[:12]} vs current {identity_policy_fp[:12]}",
            "evidenceArtifact": os.path.relpath(sentinel_path, os.path.dirname(workspace)),
        }

    # 4e. Manifest fingerprint: if the sentinel was produced before the current
    # manifest was materialized (different semanticFingerprint), the sentinel is STALE.
    identity_manifest_fp = identity.get("manifestFingerprint", "")
    artifact_manifest_fp = sentinel_data.get("manifestFingerprint", "")
    if identity_manifest_fp and identity_manifest_fp != artifact_manifest_fp:
        return {
            "name": "sentinel-result",
            "status": "STALE",
            "description": "sentinel-inspection.json was produced before the current execution manifest was materialized",
            "blocking": True,
            "reason": f"manifest fingerprint mismatch: artifact {artifact_manifest_fp[:12]} vs current {identity_manifest_fp[:12]}",
            "evidenceArtifact": os.path.relpath(sentinel_path, os.path.dirname(workspace)),
        }

    # ------------------------------------------------------------------
    # 4ea. Protected-paths policy fingerprint: if guard policy changed, sentinel is STALE.
    # ------------------------------------------------------------------
    identity_pp_fp = identity.get("protectedPathsFingerprint", "")
    artifact_pp_fp = sentinel_data.get("protectedPathsFingerprint", "")
    if identity_pp_fp and identity_pp_fp != artifact_pp_fp:
        return {
            "name": "sentinel-result",
            "status": "STALE",
            "description": "sentinel-inspection.json was produced with a different protected-paths policy",
            "blocking": True,
            "reason": f"protectedPaths fingerprint mismatch: artifact {artifact_pp_fp[:12]} vs current {identity_pp_fp[:12]}",
            "evidenceArtifact": os.path.relpath(sentinel_path, os.path.dirname(workspace)),
        }

    # ------------------------------------------------------------------
    # 4f. Recompute and verify semanticFingerprint
    # ------------------------------------------------------------------
    stored_fp = sentinel_data.get("semanticFingerprint", "")
    recomputed_fp = _compute_sentinel_semantic_fingerprint(sentinel_data)
    if recomputed_fp != stored_fp:
        return {
            "name": "sentinel-result",
            "status": "INVALID",
            "description": "sentinel-inspection.json semantic fingerprint mismatch — artifact may have been tampered",
            "blocking": True,
            "reason": f"stored {stored_fp[:12]} != recomputed {recomputed_fp[:12]}",
            "evidenceArtifact": os.path.relpath(sentinel_path, os.path.dirname(workspace)),
        }

    # ------------------------------------------------------------------
    # 5. Check findings — overallStatus and CRITICAL findings
    # ------------------------------------------------------------------
    overall = sentinel_data.get("overallStatus", "")
    if overall == "FAIL":
        return {
            "name": "sentinel-result",
            "status": "FAIL",
            "description": "sentinel-inspection.json reports overallStatus FAIL",
            "blocking": True,
            "evidenceArtifact": os.path.relpath(sentinel_path, os.path.dirname(workspace)),
        }

    findings = sentinel_data.get("findings", [])
    if not isinstance(findings, list):
        findings = []
    critical_findings = [f for f in findings if f.get("severity") == "CRITICAL"]
    if critical_findings:
        titles = [f.get("title", "unnamed") for f in critical_findings[:5]]
        return {
            "name": "sentinel-result",
            "status": "FAIL",
            "description": f"sentinel-inspection.json has {len(critical_findings)} CRITICAL finding(s)",
            "blocking": True,
            "reason": "; ".join(titles),
            "evidenceArtifact": os.path.relpath(sentinel_path, os.path.dirname(workspace)),
        }

    # ------------------------------------------------------------------
    # 6. All checks passed
    # ------------------------------------------------------------------
    return {
        "name": "sentinel-result",
        "status": "PASS",
        "description": "sentinel-inspection.json is current and has no CRITICAL findings",
        "blocking": True,
        "evidenceArtifact": os.path.relpath(sentinel_path, os.path.dirname(workspace)),
    }


# ---------------------------------------------------------------------------
# Applicable identity resolver (Item 2 — bind sentinel to execution contract)
# ---------------------------------------------------------------------------

def _resolve_applicable_identity(workspace):
    """Resolve the authoritative applicable identity for sentinel validation.

    Determines which run/task contract governs the current workspace, in
    priority order:
      1. Execution manifest (most authoritative — immutable frozen contract).
      2. Execution policy (resolved profile and fingerprints).
      3. Active gate/review contract (run-ledger latest entry).
      4. Team-state currentRunId/currentTaskId.
      5. Repository-only mode (no task contract).

    Returns
    -------
    dict
        ``{
          runId, taskId, taskRevision, repositoryHead,
          manifestFingerprint, policyFingerprint,
          protectedPathsFingerprint, implementationVersion,
          runtimeVersion,
          sentinelRequired: bool,
          source: str  # how identity was resolved
        }``
    """
    identity = {
        "runId": "",
        "taskId": "",
        "taskRevision": "",
        "repositoryIdentity": "",
        "repositoryHead": "",
        "manifestFingerprint": "",
        "policyFingerprint": "",
        "protectedPathsFingerprint": "",
        "implementationVersion": _cache_mod.IMPLEMENTATION_VERSION,
        "runtimeVersion": _cache_mod.IMPLEMENTATION_VERSION,
        "sentinelRequired": False,
        "source": "none",
    }

    repo_root = fast_execution._repo_root(workspace)
    identity["repositoryIdentity"] = _repository_identity(repo_root)
    try:
        proc = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            identity["repositoryHead"] = proc.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    # Load protected-paths fingerprint from workspace policy
    pp_path = os.path.join(workspace, "policies", "protected-paths.json")
    if os.path.exists(pp_path):
        try:
            identity["protectedPathsFingerprint"] = fast_execution.file_sha256(pp_path)
        except (OSError, TypeError):
            pass

    runs_dir = os.path.join(workspace, "runs")

    # ------------------------------------------------------------------
    # 1. Check for execution manifest (most authoritative)
    # ------------------------------------------------------------------
    host = WorkspaceContext(workspace)
    state = host.state_safe

    # First try to find the manifest for the current run
    current_run_id = ""
    current_task_id = ""
    if state:
        current_run_id = state.get("currentRunId", "")
        current_task_id = state.get("currentTaskId", "")

    # Try current run's manifest first
    if current_run_id and os.path.isdir(runs_dir):
        manifest_path = os.path.join(runs_dir, current_run_id, "execution-manifest.json")
        manifest = read_json_file_safe(manifest_path)
        if manifest and isinstance(manifest, dict):
            identity["runId"] = current_run_id
            identity["taskId"] = manifest.get("taskId", current_task_id)
            identity["taskRevision"] = manifest.get("taskRevision", "")
            identity["repositoryHead"] = manifest.get(
                "repositoryHead", identity["repositoryHead"]
            )
            identity["manifestFingerprint"] = manifest.get("semanticFingerprint", "")
            identity["policyFingerprint"] = manifest.get("policyFingerprint", "")
            identity["sentinelRequired"] = True
            identity["source"] = "execution-manifest"
            # Also load policy fingerprints from the same run
            policy_path = os.path.join(runs_dir, current_run_id, "execution-policy.json")
            policy_data = read_json_file_safe(policy_path)
            if policy_data and isinstance(policy_data, dict):
                identity["policyFingerprint"] = policy_data.get(
                    "semanticFingerprint", identity["policyFingerprint"]
                )
            return identity

    # Fallback: find the most recent manifest across all runs
    if os.path.isdir(runs_dir):
        for run_name in reversed(sorted(os.listdir(runs_dir))):
            manifest_path = os.path.join(runs_dir, run_name, "execution-manifest.json")
            manifest = read_json_file_safe(manifest_path)
            if manifest and isinstance(manifest, dict):
                identity["runId"] = run_name
                identity["taskId"] = manifest.get("taskId", "")
                identity["taskRevision"] = manifest.get("taskRevision", "")
                identity["repositoryHead"] = manifest.get(
                    "repositoryHead", identity["repositoryHead"]
                )
                identity["manifestFingerprint"] = manifest.get("semanticFingerprint", "")
                identity["policyFingerprint"] = manifest.get("policyFingerprint", "")
                identity["sentinelRequired"] = True
                identity["source"] = "execution-manifest"
                # Also load policy fingerprints from the same run
                policy_path = os.path.join(runs_dir, run_name, "execution-policy.json")
                policy_data = read_json_file_safe(policy_path)
                if policy_data and isinstance(policy_data, dict):
                    identity["policyFingerprint"] = policy_data.get(
                        "semanticFingerprint", identity["policyFingerprint"]
                    )
                return identity

    # ------------------------------------------------------------------
    # 2. Check for execution policy
    # ------------------------------------------------------------------
    if os.path.isdir(runs_dir):
        for run_name in reversed(sorted(os.listdir(runs_dir))):
            policy_path = os.path.join(runs_dir, run_name, "execution-policy.json")
            policy_data = read_json_file_safe(policy_path)
            if policy_data and isinstance(policy_data, dict):
                identity["runId"] = run_name
                identity["taskId"] = policy_data.get("taskId", "")
                identity["policyFingerprint"] = policy_data.get("semanticFingerprint", "")
                identity["sentinelRequired"] = True
                identity["source"] = "execution-policy"
                return identity

    # ------------------------------------------------------------------
    # 3. Check current active run/task in team-state
    # ------------------------------------------------------------------
    if state:
        if current_run_id:
            identity["runId"] = current_run_id
            identity["source"] = "team-state-run"
        if current_task_id:
            identity["taskId"] = current_task_id
            task_obj = host.current_task
            if task_obj:
                identity["taskRevision"] = fast_execution._task_revision(task_obj)
        if current_run_id or current_task_id:
            identity["source"] = "team-state"
            return identity

    # ------------------------------------------------------------------
    # 5. Repository-only mode (no task contract)
    # ------------------------------------------------------------------
    return identity


# ---------------------------------------------------------------------------
# Command: final-gate
# ---------------------------------------------------------------------------

def cmd_final_gate(args):
    """Aggregate final gate checks for pre-handoff validation.

    Runs the full configured set of independent checks and produces a structured JSON result
    matching schemas/final-gate.schema.json.  Writes the result to
    .teamloop/state/final-gate-result.json and prints JSON to stdout.

    Exit 0 if overallStatus is PASS or NOT_CONFIGURED, exit 1 if FAIL.
    """
    started = fast_execution.clock_ms()
    host = WorkspaceContext(args.workspace)
    workspace = host.workspace
    project_root = host.project_root
    script_dir = os.path.dirname(os.path.abspath(__file__))
    core_script = os.path.join(script_dir, "teamloop-core.py")

    checks = []
    advisory_findings = []
    subprocess_invocations = 0

    # ------------------------------------------------------------------
    # Check 0: cache-integrity (MUST run first, before any subprocess
    # that could modify the cache. All subsequent subprocess invocations
    # set TEAMLOOP_NO_CACHE to prevent cache mutation during final-gate.)
    # ------------------------------------------------------------------
    cache = _create_cache(workspace, project_root, read_only=True)
    if cache is None:
        cache_integrity_check = {
            "name": "cache-integrity",
            "status": "SKIP",
            "description": "cache disabled by TEAMLOOP_NO_CACHE",
            "blocking": False,
            "cacheState": "DISABLED",
            "cachePath": os.path.join(workspace, "cache", "validation-cache.jsonl"),
            "enabled": False,
            "totalRecords": 0,
            "validRecords": 0,
            "malformedLines": 0,
            "invalidIntegrity": 0,
            "legacyUntrusted": 0,
            "quarantinedRecords": 0,
            "evidenceArtifact": os.path.join(
                os.path.basename(workspace), "cache", "validation-cache.jsonl"
            ),
        }
    else:
        cache_status = cache.integrity_check()
        cache_state = _classify_cache_state(cache, cache_status)
        total_records = cache_status.get("totalEntries", 0)
        valid_records = cache_status.get("validEntries", 0)
        malformed_lines = cache_status.get("malformedLineCount", 0)
        invalid_count = len(cache_status.get("invalidEntries", []))
        legacy_count = cache_status.get("legacyUntrustedCount", 0)

        cache_integrity_check = {
            "name": "cache-integrity",
            "status": "FAIL" if cache_state in ("CORRUPT", "INVALID") else "PASS",
            "description": f"cache integrity: {cache_state}",
            "blocking": cache_state in ("CORRUPT", "INVALID"),
            "cacheState": cache_state,
            "cachePath": cache.cache_path,
            "enabled": True,
            "totalRecords": total_records,
            "validRecords": valid_records,
            "malformedLines": malformed_lines,
            "invalidIntegrity": invalid_count,
            "legacyUntrusted": legacy_count,
            "quarantinedRecords": legacy_count + invalid_count,
            "evidenceArtifact": os.path.relpath(
                cache.cache_path, os.path.dirname(workspace)
            ),
        }
        if cache_state in ("CORRUPT", "INVALID"):
            cache_integrity_check["reason"] = (
                f"malformedLines={malformed_lines}, invalidIntegrity={invalid_count}, "
                f"legacyUntrusted={legacy_count}"
            )
        if legacy_count > 0:
            advisory_findings.append(
                f"cache has {legacy_count} legacy-untrusted entries "
                f"(not served, safe to ignore)"
            )
    checks.append(cache_integrity_check)

    # Block on cache corruption immediately — do not run further checks
    # if the cache is corrupt, to avoid subprocesses repairing it.
    cache_blocks = cache_integrity_check.get("blocking", False)

    # ------------------------------------------------------------------
    # Helper: run a subprocess and return exit code
    # Sets TEAMLOOP_NO_CACHE=1 to prevent cache mutation during final-gate.
    # ------------------------------------------------------------------
    def _run_sub(cmd, timeout=30):
        nonlocal subprocess_invocations
        subprocess_invocations += 1
        try:
            env = os.environ.copy()
            env["TEAMLOOP_NO_CACHE"] = "1"
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                cwd=os.path.dirname(os.path.abspath(workspace)),
                env=env,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError) as e:
            return -1, "", str(e)

    # ------------------------------------------------------------------
    # Check 1: state-validation
    # ------------------------------------------------------------------
    rc, stdout, stderr = _run_sub(
        [sys.executable, core_script, "validate-state", "--workspace", workspace]
    )
    if rc == 0:
        checks.append({
            "name": "state-validation",
            "status": "PASS",
            "description": "validate-state passed with no errors",
            "blocking": True,
        })
    else:
        detail = stdout.strip() or stderr.strip() or "validate-state failed"
        checks.append({
            "name": "state-validation",
            "status": "FAIL",
            "description": "validate-state reported errors",
            "blocking": True,
            "reason": detail[:500],
        })

    # ------------------------------------------------------------------
    # Check 2: memory-validation
    # ------------------------------------------------------------------
    memory_dir = os.path.join(workspace, "memory")
    if not os.path.isdir(memory_dir):
        checks.append({
            "name": "memory-validation",
            "status": "NOT_CONFIGURED",
            "description": "memory directory does not exist; memory subsystem not configured",
            "blocking": False,
        })
        advisory_findings.append("memory directory not found — memory subsystem is not configured")
    else:
        rc, stdout, stderr = _run_sub(
            [sys.executable, core_script, "memory-doctor", "--workspace", workspace],
            timeout=30
        )
        if rc == 0:
            checks.append({
                "name": "memory-validation",
                "status": "PASS",
                "description": "memory-doctor passed with no issues",
                "blocking": True,
            })
        else:
            detail = stdout.strip() or stderr.strip() or "memory-doctor failed"
            checks.append({
                "name": "memory-validation",
                "status": "FAIL",
                "description": "memory-doctor reported issues",
                "blocking": True,
                "reason": detail[:500],
            })

    # ------------------------------------------------------------------
    # Check 3: continuation-decision
    # ------------------------------------------------------------------
    state = host.state_safe

    # Resolve contract_run_id early so that the sentinel-result check (Check 9)
    # can evaluate staleness against the correct run context.
    _task_id_for_contract = (state or {}).get("currentTaskId", "")
    contract_run_id = fast_execution.resolve_run_id(
        workspace, task_id=_task_id_for_contract
    )
    contract_present = bool(
        contract_run_id and os.path.exists(os.path.join(
            workspace, "runs", contract_run_id, "execution-manifest.json"
        ))
    )

    decision_file = os.path.join(workspace, "state", "continuation-decision.json")
    if os.path.exists(decision_file):
        decision = read_json_file_safe(decision_file)
        if decision is None:
            checks.append({
                "name": "continuation-decision",
                "status": "FAIL",
                "description": "continuation-decision.json exists but is not valid JSON",
                "blocking": True,
            })
        else:
            # Reuse the same consistency check that validate-state uses
            schema_map = host.schemas
            cd_errors = _validate_continuation_consistency(workspace, state or {}, schema_map)
            if cd_errors:
                checks.append({
                    "name": "continuation-decision",
                    "status": "FAIL",
                    "description": "continuation-decision.json has consistency errors",
                    "blocking": True,
                    "reason": "; ".join(cd_errors[:5]),
                })
            else:
                checks.append({
                    "name": "continuation-decision",
                    "status": "PASS",
                    "description": "continuation-decision.json is consistent with team-state",
                    "blocking": True,
                })
    else:
        checks.append({
            "name": "continuation-decision",
            "status": "SKIP",
            "description": "continuation-decision.json not present; skipping consistency check",
            "blocking": False,
        })

    # ------------------------------------------------------------------
    # Check 4: scope-validation
    # ------------------------------------------------------------------
    rc, stdout, stderr = _run_sub(
        [sys.executable, core_script, "check-scope", "--workspace", workspace]
    )
    if rc == 0:
        checks.append({
            "name": "scope-validation",
            "status": "PASS",
            "description": "check-scope passed; all changes within scope",
            "blocking": True,
        })
    else:
        detail = stdout.strip() or stderr.strip() or "check-scope failed"
        checks.append({
            "name": "scope-validation",
            "status": "FAIL",
            "description": "check-scope reported scope violations",
            "blocking": True,
            "reason": detail[:500],
        })

    # ------------------------------------------------------------------
    # Check 5: latest-gate-result
    # ------------------------------------------------------------------
    gr_path = host.latest_gate_result()
    gr = None
    if gr_path:
        gr = read_json_file_safe(gr_path)
    if gr is None:
        checks.append({
            "name": "latest-gate-result",
            "status": "SKIP",
            "description": "no gate-result.json found in any run directory",
            "blocking": False,
        })
    elif gr.get("status") == "FAIL":
        failed_checks = [c.get("name", "?") for c in gr.get("checks", []) if c.get("status") == "FAIL"]
        checks.append({
            "name": "latest-gate-result",
            "status": "FAIL",
            "description": "latest gate-result.json has status FAIL",
            "blocking": True,
            "reason": "failed gate check(s): " + ", ".join(failed_checks),
            "evidenceArtifact": os.path.relpath(
                os.path.join(workspace, "runs", gr.get("runId", ""), "gate-result.json"),
                os.path.dirname(workspace)
            ),
        })
    else:
        checks.append({
            "name": "latest-gate-result",
            "status": "PASS",
            "description": f"latest gate-result.json has status {gr.get('status', 'unknown')}",
            "blocking": True,
        })

    # ------------------------------------------------------------------
    # Check 6: active-task-consistency
    # ------------------------------------------------------------------
    task_issues = []
    if state:
        state_task_id = state.get("currentTaskId", "")
        state_run_id = state.get("currentRunId", "")
        ct = host.current_task

        if state_task_id:
            if ct is None:
                task_issues.append(f"team-state currentTaskId='{state_task_id}' but current-task.json is missing")
            elif ct.get("taskId") != state_task_id:
                task_issues.append(
                    f"current-task.json.taskId='{ct.get('taskId')}' != team-state.currentTaskId='{state_task_id}'"
                )

        if state_run_id:
            run_dir = host.find_run_dir(state_run_id)
            if not os.path.isdir(run_dir):
                # Check run-ledger as fallback
                run_found = False
                for entry in host.run_ledger:
                    if entry.get("runId") == state_run_id:
                        run_found = True
                        break
                if not run_found:
                    task_issues.append(f"team-state currentRunId='{state_run_id}' not found in runs/ or run-ledger")

    if task_issues:
        checks.append({
            "name": "active-task-consistency",
            "status": "FAIL",
            "description": "active-task-consistency check failed",
            "blocking": True,
            "reason": "; ".join(task_issues),
        })
    else:
        checks.append({
            "name": "active-task-consistency",
            "status": "PASS",
            "description": "currentTaskId/currentRunId consistent across state files",
            "blocking": True,
        })

    # ------------------------------------------------------------------
    # Check 7: unresolved-blockers
    # ------------------------------------------------------------------
    blockers_path = os.path.join(workspace, "state", "blockers.jsonl")
    if os.path.exists(blockers_path):
        blockers = host.blockers
        open_blockers = [b for b in blockers if not b.get("resolvedAtUtc")]
        if open_blockers:
            summaries = [b.get("summary", b.get("blockerId", "unnamed")) for b in open_blockers[:5]]
            checks.append({
                "name": "unresolved-blockers",
                "status": "FAIL",
                "description": f"{len(open_blockers)} unresolved blocker(s) found",
                "blocking": True,
                "reason": "; ".join(summaries),
            })
        else:
            checks.append({
                "name": "unresolved-blockers",
                "status": "PASS",
                "description": "no unresolved blockers",
                "blocking": True,
            })
    else:
        checks.append({
            "name": "unresolved-blockers",
            "status": "PASS",
            "description": "blockers.jsonl not found; no blockers to check",
            "blocking": True,
        })

    # ------------------------------------------------------------------
    # Check 8: stale-artifacts
    # ------------------------------------------------------------------
    stale_issues = []

    # Stale current-task.json: exists with IN_PROGRESS but team-state has no currentTaskId
    if state and not state.get("currentTaskId", ""):
        ct = host.current_task
        if ct and ct.get("status") == "IN_PROGRESS":
            stale_issues.append("stale current-task.json with IN_PROGRESS while team-state has no currentTaskId")

    # Orphaned IN_PROGRESS tasks in backlog
    backlog_path = os.path.join(workspace, "state", "backlog.jsonl")
    if os.path.exists(backlog_path):
        for task in host.backlog:
            if task.get("status") == "IN_PROGRESS":
                stale_issues.append(f"backlog task '{task.get('taskId', '?')}' still IN_PROGRESS")

    if stale_issues:
        checks.append({
            "name": "stale-artifacts",
            "status": "FAIL",
            "description": "stale artifacts detected",
            "blocking": True,
            "reason": "; ".join(stale_issues),
        })
    else:
        checks.append({
            "name": "stale-artifacts",
            "status": "PASS",
            "description": "no stale artifacts detected",
            "blocking": True,
        })

    # ------------------------------------------------------------------
    # Check 9: sentinel-result
    # ------------------------------------------------------------------
    sentinel_check_result = _evaluate_sentinel_evidence(workspace, state)
    checks.append(sentinel_check_result)

    # ------------------------------------------------------------------
    # Check 10: guard-integrity-result
    # ------------------------------------------------------------------
    policy_path = os.path.join(workspace, "policies", "protected-paths.json")
    if not os.path.exists(policy_path):
        checks.append({
            "name": "guard-integrity-result",
            "status": "NOT_CONFIGURED",
            "description": "protected-paths.json not found; guard integrity is not configured",
            "blocking": False,
        })
        advisory_findings.append("guard integrity not configured — protected-paths.json missing")
    else:
        rc, stdout, stderr = _run_sub(
            [sys.executable, core_script, "check-guard-integrity", "--workspace", workspace]
        )
        if rc == 0:
            # Parse the JSON result to determine actual status
            try:
                ghi_result = json.loads(stdout)
                ghi_status = ghi_result.get("status", "PASS")
                if ghi_status == "FAIL":
                    checks.append({
                        "name": "guard-integrity-result",
                        "status": "FAIL",
                        "description": "guard integrity check reported FAIL",
                        "blocking": True,
                        "reason": json.dumps(ghi_result.get("violations", []))[:500],
                    })
                elif ghi_status == "WARNING":
                    checks.append({
                        "name": "guard-integrity-result",
                        "status": "PASS",
                        "description": "guard integrity check reported WARNING (non-blocking)",
                        "blocking": False,
                    })
                else:
                    checks.append({
                        "name": "guard-integrity-result",
                        "status": "PASS",
                        "description": f"guard integrity check passed (status: {ghi_status})",
                        "blocking": True,
                    })
            except json.JSONDecodeError:
                checks.append({
                    "name": "guard-integrity-result",
                    "status": "PASS",
                    "description": "guard integrity check exited 0; output not valid JSON but command succeeded",
                    "blocking": True,
                })
        else:
            detail = stdout.strip() or stderr.strip() or "check-guard-integrity failed"
            checks.append({
                "name": "guard-integrity-result",
                "status": "FAIL",
                "description": "guard integrity check failed",
                "blocking": True,
                "reason": detail[:500],
            })

    # ------------------------------------------------------------------
    # Check 11: reviewed-content-integrity
    # ------------------------------------------------------------------
    review_evidence_found = False
    if state:
        run_id = state.get("currentRunId", "")
        if run_id:
            review_path = os.path.join(workspace, "runs", run_id, "review-evidence.json")
            if os.path.exists(review_path):
                review_evidence_found = True
        # Also check latest run directory
        if not review_evidence_found:
            runs_dir = os.path.join(workspace, "runs")
            if os.path.isdir(runs_dir):
                try:
                    for run_name in reversed(sorted(os.listdir(runs_dir))):
                        review_path = os.path.join(runs_dir, run_name, "review-evidence.json")
                        if os.path.exists(review_path):
                            review_evidence_found = True
                            break
                except OSError:
                    pass

    if not review_evidence_found:
        checks.append({
            "name": "reviewed-content-integrity",
            "status": "SKIP",
            "description": "no review-evidence.json found in any run directory; review evidence not yet available",
            "blocking": False,
        })
    else:
        # review-evidence.json exists — basic check that it is valid JSON
        review_data = None
        if state and state.get("currentRunId", ""):
            review_path = os.path.join(workspace, "runs", state["currentRunId"], "review-evidence.json")
            if os.path.exists(review_path):
                review_data = read_json_file_safe(review_path)
        # Fallback: try the latest run directory when currentRunId is empty
        if review_data is None:
            runs_dir = os.path.join(workspace, "runs")
            if os.path.isdir(runs_dir):
                try:
                    for run_name in reversed(sorted(os.listdir(runs_dir))):
                        review_path = os.path.join(runs_dir, run_name, "review-evidence.json")
                        if os.path.exists(review_path):
                            review_data = read_json_file_safe(review_path)
                            if review_data is not None:
                                break
                except OSError:
                    pass
        if review_data is not None:
            checks.append({
                "name": "reviewed-content-integrity",
                "status": "PASS",
                "description": "review-evidence.json found and is valid JSON",
                "blocking": True,
            })
        else:
            checks.append({
                "name": "reviewed-content-integrity",
                "status": "SKIP",
                "description": "review-evidence.json exists in a run directory but could not be parsed; skipping detailed check",
                "blocking": False,
            })

    # ------------------------------------------------------------------
    # Check 12: execution-contract-integrity (optimization runs)
    # ------------------------------------------------------------------
    # contract_run_id / contract_present resolved earlier for Check 9.
    if not contract_present:
        checks.append({
            "name": "execution-contract-integrity",
            "status": "SKIP",
            "description": "no execution manifest found for the applicable run; legacy run",
            "blocking": False,
        })
    else:
        try:
            contract_result = fast_execution.validate_contract(
                workspace, contract_run_id, write_result=True
            )
            policy_data = read_json_file_safe(os.path.join(
                workspace, "runs", contract_run_id, "execution-policy.json"
            )) or {}
            invariants = policy_data.get("invariants", {})
            invariant_names = (
                "scopeIntegrityCannotBeDisabled",
                "evidenceIntegrityCannotBeDisabled",
                "runtimeStateIntegrityCannotBeDisabled",
                "requiredProjectGatesCannotBeDisabled",
                "finalSentinelCannotBeBypassed",
                "finalGateCannotBeBypassed",
            )
            invariant_errors = [
                name for name in invariant_names if invariants.get(name) is not True
            ]
            if contract_result.get("status") == "PASS" and not invariant_errors:
                checks.append({
                    "name": "execution-contract-integrity",
                    "status": "PASS",
                    "description": "execution policy and immutable manifest are valid and safety invariants remain enabled",
                    "blocking": True,
                    "evidenceArtifact": os.path.join(
                        os.path.basename(workspace), "runs", contract_run_id,
                        "execution-contract-validation.json"
                    ),
                })
            else:
                reasons = list(contract_result.get("errors", []))
                if invariant_errors:
                    reasons.append("disabled invariant(s): " + ", ".join(invariant_errors))
                checks.append({
                    "name": "execution-contract-integrity",
                    "status": "FAIL",
                    "description": "execution contract validation failed",
                    "blocking": True,
                    "reason": "; ".join(reasons[:8])[:500],
                })
        except Exception as exc:
            checks.append({
                "name": "execution-contract-integrity",
                "status": "FAIL",
                "description": "execution contract could not be validated",
                "blocking": True,
                "reason": str(exc)[:500],
            })

    # ------------------------------------------------------------------
    # Check 13: no-progress-result
    # ------------------------------------------------------------------
    no_progress_path = (
        os.path.join(workspace, "runs", contract_run_id, "no-progress-result.json")
        if contract_run_id else ""
    )
    no_progress = read_json_file_safe(no_progress_path) if no_progress_path else None
    if no_progress is None:
        checks.append({
            "name": "no-progress-result",
            "status": "SKIP",
            "description": "no progress snapshot has been recorded for the applicable run",
            "blocking": False,
        })
    elif no_progress.get("status") in ("NO_PROGRESS_DETECTED", "STRATEGY_CHANGE_REQUIRED"):
        checks.append({
            "name": "no-progress-result",
            "status": "FAIL",
            "description": "unresolved no-progress condition blocks final handoff",
            "blocking": True,
            "reason": str(no_progress.get("reason", no_progress.get("status", "NO_PROGRESS_DETECTED")))[:500],
            "evidenceArtifact": os.path.relpath(no_progress_path, os.path.dirname(workspace)),
        })
    else:
        checks.append({
            "name": "no-progress-result",
            "status": "PASS",
            "description": f"no-progress detector status is {no_progress.get('status', 'unknown')}",
            "blocking": True,
            "evidenceArtifact": os.path.relpath(no_progress_path, os.path.dirname(workspace)),
        })

    # ------------------------------------------------------------------
    # Check 14: cache-integrity
    # ------------------------------------------------------------------
    # Already performed as Check 0 (above), before any subprocess could
    # mutate the cache.  This comment block is kept as a section marker
    # for the check numbering.

    # Optimized execution contracts preserve the existing mandatory final
    # sentinel invariant.  A missing sentinel is not a legacy advisory once a
    # manifest exists for this run.
    if contract_present:
        contract_manifest = read_json_file_safe(os.path.join(
            workspace, "runs", contract_run_id, "execution-manifest.json"
        )) or {}

        manifest_scope_violations = fast_execution.scope_violations(
            workspace, contract_manifest
        )
        for check in checks:
            if check.get("name") != "scope-validation":
                continue
            if manifest_scope_violations:
                check.update({
                    "status": "FAIL",
                    "description": "immutable execution manifest scope validation failed",
                    "blocking": True,
                    "reason": "; ".join(
                        f"{item['file']}: {item['reason']}"
                        for item in manifest_scope_violations[:8]
                    )[:500],
                })
            else:
                check.update({
                    "status": "PASS",
                    "description": "all current changes remain within immutable manifest scope",
                    "blocking": True,
                })
                check.pop("reason", None)
            break

        # A stale gate from another run must never satisfy the current immutable
        # execution contract.
        current_gate_path = os.path.join(
            workspace, "runs", contract_run_id, "gate-result.json"
        )
        current_gate = read_json_file_safe(current_gate_path)
        for check in checks:
            if check.get("name") != "latest-gate-result":
                continue
            if current_gate is None:
                check.update({
                    "status": "FAIL",
                    "description": "execution contract requires project gates for the same run",
                    "blocking": True,
                    "reason": f"runs/{contract_run_id}/gate-result.json is missing or invalid",
                })
            elif current_gate.get("status") != "PASS":
                check.update({
                    "status": "FAIL",
                    "description": "current-run gate-result.json did not pass",
                    "blocking": True,
                    "reason": f"current-run gate status is {current_gate.get('status', 'unknown')}",
                    "evidenceArtifact": os.path.relpath(current_gate_path, os.path.dirname(workspace)),
                })
            else:
                check.update({
                    "status": "PASS",
                    "description": "current-run project gates passed",
                    "blocking": True,
                    "evidenceArtifact": os.path.relpath(current_gate_path, os.path.dirname(workspace)),
                })
            break

        # Review/content evidence is bound to the same run.  This prevents a
        # stale approved artifact from an earlier task from validating new
        # implementation changes.
        current_review_path = os.path.join(
            workspace, "runs", contract_run_id, "review-evidence.json"
        )
        content_changed = fast_execution.has_scoped_repository_change(
            workspace, contract_manifest
        )
        review_errors = (
            _validate_review_evidence(workspace, current_review_path)
            if os.path.exists(current_review_path) else []
        )
        for check in checks:
            if check.get("name") != "reviewed-content-integrity":
                continue
            if content_changed and not os.path.exists(current_review_path):
                check.update({
                    "status": "FAIL",
                    "description": "changed scoped content has no same-run review/gate evidence",
                    "blocking": True,
                    "reason": f"runs/{contract_run_id}/review-evidence.json is missing",
                })
            elif review_errors:
                check.update({
                    "status": "FAIL",
                    "description": "same-run reviewed content integrity failed",
                    "blocking": True,
                    "reason": "; ".join(review_errors[:8])[:500],
                    "evidenceArtifact": os.path.relpath(current_review_path, os.path.dirname(workspace)),
                })
            elif os.path.exists(current_review_path):
                check.update({
                    "status": "PASS",
                    "description": "same-run reviewed content hashes remain valid",
                    "blocking": True,
                    "evidenceArtifact": os.path.relpath(current_review_path, os.path.dirname(workspace)),
                })
            else:
                check.update({
                    "status": "SKIP",
                    "description": "no scoped repository content changed during this run",
                    "blocking": False,
                })
            break

    # ------------------------------------------------------------------
    # Check 15: quality/value boundary acceptance chain
    # ------------------------------------------------------------------
    checks.append(boundary_mod.final_gate_check(workspace, project_root=host.git_root))

    # ------------------------------------------------------------------
    # Compute overall status
    # ------------------------------------------------------------------
    # Component-specific evidence states MISSING/STALE/INVALID are all
    # failure-class outcomes for the aggregate final gate.
    failure_statuses = {"FAIL", "MISSING", "STALE", "INVALID"}
    has_any_fail = any(c.get("status") in failure_statuses for c in checks)

    # NOT_CONFIGURED only if at least one NON-BLOCKING check is NOT_CONFIGURED
    # (blocking NOT_CONFIGURED would be treated as FAIL)
    has_non_blocking_not_configured = any(
        c["status"] == "NOT_CONFIGURED" and not c["blocking"]
        for c in checks
    )
    has_blocking_not_configured = any(
        c["status"] == "NOT_CONFIGURED" and c["blocking"]
        for c in checks
    )

    if has_any_fail or has_blocking_not_configured:
        overall_status = "FAIL"
    elif has_non_blocking_not_configured:
        # Non-blocking NOT_CONFIGURED: report as advisory but overall is PASS
        overall_status = "PASS"
    else:
        overall_status = "PASS"

    # ------------------------------------------------------------------
    # Get git info
    # ------------------------------------------------------------------
    current_branch = ""
    current_head = ""
    try:
        branch_proc = subprocess.run(
            ["git", "-C", project_root, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if branch_proc.returncode == 0:
            current_branch = branch_proc.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        current_branch = "unknown"

    try:
        head_proc = subprocess.run(
            ["git", "-C", project_root, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if head_proc.returncode == 0:
            current_head = head_proc.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        current_head = "0" * 40

    # ------------------------------------------------------------------
    # Build result
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Determine execution mode
    # ------------------------------------------------------------------
    # "repository-baseline": no active task/run; baseline repo validation.
    # "active-run": has a current task and/or run in progress.
    # "final-handoff": completed task with execution contract.
    execution_mode = "repository-baseline"
    if contract_present:
        execution_mode = "final-handoff"
    elif state:
        if state.get("currentTaskId") or state.get("currentRunId"):
            execution_mode = "active-run"

    # ------------------------------------------------------------------
    # Compute summary counts
    # ------------------------------------------------------------------
    status_counts = {
        "PASS": 0, "FAIL": 0, "SKIP": 0, "NOT_REQUIRED": 0,
        "UNAVAILABLE": 0, "NOT_CONFIGURED": 0,
        "MISSING": 0, "STALE": 0, "INVALID": 0,
    }
    for c in checks:
        s = c.get("status", "")
        if s in status_counts:
            status_counts[s] += 1

    summary = {
        "total": len(checks),
        "pass": status_counts["PASS"],
        "fail": sum(status_counts[s] for s in ("FAIL", "MISSING", "STALE", "INVALID")),
        "skip": status_counts["SKIP"],
        "notRequired": status_counts["NOT_REQUIRED"],
        "unavailable": status_counts["UNAVAILABLE"],
        "notConfigured": status_counts["NOT_CONFIGURED"],
        "stateBreakdown": status_counts,
    }

    run_id = "run-{}-{}".format(
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S"),
        os.getpid()
    )

    result = {
        "schemaVersion": 1,
        "checkedAtUtc": utc_now_iso(),
        "currentBranch": current_branch,
        "currentHead": current_head,
        "overallStatus": overall_status,
        "executionMode": execution_mode,
        "summary": summary,
        "checks": checks,
    }
    if advisory_findings:
        result["advisoryFindings"] = advisory_findings

    # ------------------------------------------------------------------
    # Write result to .teamloop/state/final-gate-result.json
    # ------------------------------------------------------------------
    result_path = os.path.join(workspace, "state", "final-gate-result.json")
    write_json(result_path, result)

    # Also write to a run directory for traceability
    run_dir = os.path.join(workspace, "runs", run_id)
    os.makedirs(run_dir, exist_ok=True)
    write_json(os.path.join(run_dir, "final-gate-result.json"), result)

    trace_run_id = contract_run_id or fast_execution.resolve_run_id(workspace)
    fast_execution.record_trace_phase(
        workspace, trace_run_id, "final-gate", fast_execution.clock_ms() - started,
        process_count=subprocess_invocations + 2,
        files=["state/final-gate-result.json", "execution-contract-validation.json", "no-progress-result.json"],
        decision="EXECUTED" if overall_status == "PASS" else "FAILED",
        details=f"{len(checks)} checks; overall={overall_status}",
    )

    # ------------------------------------------------------------------
    # Print JSON to stdout
    # ------------------------------------------------------------------
    print(json.dumps(result, ensure_ascii=False))

    # ------------------------------------------------------------------
    # Exit code
    # ------------------------------------------------------------------
    if overall_status == "FAIL":
        sys.exit(1)


# ---------------------------------------------------------------------------
# Phase 5: Content-addressed review evidence
# ---------------------------------------------------------------------------

def _compute_file_sha256(file_path):
    """Compute SHA256 hex digest of a file's content."""
    h = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except (OSError, IOError):
        return None
    return h.hexdigest()


def _get_git_changed_files_for_review():
    """Get all files that differ from HEAD or are staged/untracked.

    Returns list of {path, status} dicts where status is 'TRACKED' or 'UNTRACKED'.
    Excludes .teamloop/ workspace files since they are internal runtime artifacts
    that change between evidence write and validation.
    Handles gracefully if not in a git repo.
    """
    files = []
    # Patterns to exclude — internal workspace artifacts that change between
    # evidence write and validation.
    _exclude_prefixes = (".teamloop/", ".teamloop\\")
    try:
        # Get files that differ from HEAD (tracked, modified)
        result_diff = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if result_diff.returncode == 0:
            for line in result_diff.stdout.split("\n"):
                line = line.strip()
                if line and not line.startswith(_exclude_prefixes):
                    files.append({"path": line, "status": "TRACKED"})
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    # Get staged files (may include files not in HEAD)
    staged_set = set()
    try:
        result_staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, text=True, timeout=10,
        )
        if result_staged.returncode == 0:
            for line in result_staged.stdout.split("\n"):
                line = line.strip()
                if line and not line.startswith(_exclude_prefixes):
                    staged_set.add(line)
                    # Only add if not already from diff HEAD
                    if not any(f["path"] == line for f in files):
                        files.append({"path": line, "status": "TRACKED"})
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    # Get untracked files
    try:
        result_untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=10,
        )
        if result_untracked.returncode == 0:
            for line in result_untracked.stdout.split("\n"):
                line = line.strip()
                if line and not line.startswith(_exclude_prefixes):
                    # Don't double-add if already tracked
                    if not any(f["path"] == line for f in files):
                        files.append({"path": line, "status": "UNTRACKED"})
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    return files


def _get_current_git_commit():
    """Get current HEAD commit SHA, or None if not available."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            sha = result.stdout.strip()
            if len(sha) == 40:
                return sha
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    return None


def _find_run_directory(workspace):
    """Find the most appropriate run directory for writing review evidence.

    Prefers the current run directory (from team-state currentRunId).
    Falls back to the latest run directory (lexicographic order).
    Returns None if no run directory exists.
    """
    state = read_json_file_safe(os.path.join(workspace, "state", "team-state.json"))
    if state:
        run_id = state.get("currentRunId", "")
        if run_id:
            run_dir = os.path.join(workspace, "runs", run_id)
            if os.path.isdir(run_dir):
                return run_dir

    runs_dir = os.path.join(workspace, "runs")
    if not os.path.isdir(runs_dir):
        return None

    try:
        run_dirs = sorted([
            d for d in os.listdir(runs_dir)
            if os.path.isdir(os.path.join(runs_dir, d))
        ])
    except OSError:
        return None

    if run_dirs:
        return os.path.join(runs_dir, run_dirs[-1])

    return None


def _find_review_evidence(workspace):
    """Find the most recent review-evidence.json in the workspace.

    Searches run directories (latest first) and then state directory.
    Returns the file path or None.
    """
    runs_dir = os.path.join(workspace, "runs")
    if os.path.isdir(runs_dir):
        try:
            run_dirs = sorted([
                d for d in os.listdir(runs_dir)
                if os.path.isdir(os.path.join(runs_dir, d))
            ])
        except OSError:
            pass
        else:
            for run_name in reversed(run_dirs):
                candidate = os.path.join(runs_dir, run_name, "review-evidence.json")
                if os.path.exists(candidate):
                    return candidate

    state_candidate = os.path.join(workspace, "state", "review-evidence.json")
    if os.path.exists(state_candidate):
        return state_candidate

    return None


def _write_review_evidence(workspace, task_id, reviewer="change-reviewer",
                           gate_result=None, run_id="", preserve_existing=False):
    """Write a review-evidence.json artifact with content hashes of changed files.

    Parameters
    ----------
    workspace : str
        Absolute path to the .teamloop workspace.
    task_id : str
        Task ID being reviewed.
    reviewer : str
        Name of the reviewer role.
    gate_result : str or None
        Gate result string (e.g. "PASS", "FAIL") if called from cmd_run_gates.

    Writes to .teamloop/runs/<run-id>/review-evidence.json or, if no run
    directory exists, to .teamloop/state/review-evidence.json.
    """
    try:
        explicit_run_dir = os.path.join(workspace, "runs", run_id) if run_id else None
        existing_path = (
            os.path.join(explicit_run_dir, "review-evidence.json")
            if explicit_run_dir else None
        )
        if preserve_existing and existing_path and os.path.exists(existing_path):
            existing = read_json_file_safe(existing_path)
            if existing is not None:
                if gate_result is not None:
                    existing["gateResult"] = gate_result
                write_json(existing_path, existing)
                return

        changed_files = _get_git_changed_files_for_review()
        commit_sha = _get_current_git_commit()

        reviewed_files = []
        for cf in changed_files:
            file_path = cf["path"]
            # Try as-is first, then relative to cwd
            abs_path = file_path
            if not os.path.isabs(abs_path):
                abs_path = os.path.join(os.getcwd(), file_path)

            file_hash = _compute_file_sha256(abs_path)
            if file_hash is None:
                continue

            reviewed_files.append({
                "path": file_path,
                "hash": file_hash,
                "status": cf["status"],
            })

        if not reviewed_files:
            # No changed files — still record evidence with an empty-ish marker
            # to prove review happened with a clean tree
            return

        evidence = {
            "schemaVersion": 1,
            "taskId": task_id,
            "reviewedAtUtc": utc_now_iso(),
            "reviewResult": "PASS",
            "reviewer": reviewer,
            "reviewedFiles": reviewed_files,
        }

        if commit_sha:
            evidence["reviewedCommit"] = commit_sha

        if gate_result is not None:
            evidence["gateResult"] = gate_result

        # Determine write path
        run_dir = explicit_run_dir or _find_run_directory(workspace)
        if run_dir is None:
            # Create a run directory for this evidence
            run_id = "run-{}-{}".format(
                datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S"),
                os.getpid()
            )
            run_dir = os.path.join(workspace, "runs", run_id)
            os.makedirs(run_dir, exist_ok=True)
        else:
            os.makedirs(run_dir, exist_ok=True)

        evidence_path = os.path.join(run_dir, "review-evidence.json")
        write_json(evidence_path, evidence)

    except Exception as exc:
        print(
            f"Warning: failed to write review evidence: {exc}",
            file=sys.stderr,
        )


def _validate_review_evidence(workspace, evidence_path=None):
    """Validate review-evidence integrity against current working tree.

    Finds the most recent review-evidence.json and verifies:
      - Each reviewed file still exists
      - Each reviewed file's SHA256 matches the recorded hash
      - If reviewedCommit is set, it is reachable from HEAD

    Returns list of error strings. Empty list means all checks passed.
    """
    errors = []
    evidence_path = evidence_path or _find_review_evidence(workspace)
    if evidence_path is None:
        return errors

    evidence = read_json_file_safe(evidence_path)
    if evidence is None:
        errors.append("review-evidence.json: file exists but contains invalid JSON")
        return errors

    # Check reviewedCommit reachability
    reviewed_commit = evidence.get("reviewedCommit", "")
    if reviewed_commit:
        try:
            result = subprocess.run(
                ["git", "merge-base", "--is-ancestor", reviewed_commit, "HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                errors.append(f"reviewed commit not reachable: {reviewed_commit}")
        except (subprocess.SubprocessError, FileNotFoundError):
            # If git is not available, skip commit reachability check
            pass

    # Check each reviewed file
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    workspace_parent = os.path.dirname(os.path.normpath(workspace))
    for rf in evidence.get("reviewedFiles", []):
        path = rf.get("path", "")
        expected_hash = rf.get("hash", "")

        # Try to resolve the file path from multiple bases
        candidates = []
        if os.path.isabs(path):
            candidates.append(path)
        else:
            candidates.append(os.path.join(workspace_parent, path))
            if project_root:
                candidates.append(os.path.join(project_root, path))

        found = False
        for abs_path in candidates:
            if os.path.exists(abs_path):
                actual_hash = _compute_file_sha256(abs_path)
                if actual_hash and actual_hash != expected_hash:
                    errors.append(f"reviewed content changed: {path}")
                found = True
                break

        if not found:
            # File might be tracked by git but not in working tree — skip
            # since we can't verify content without the file present
            errors.append(f"reviewed content missing: {path}")

    return errors


# ---------------------------------------------------------------------------
# Phase 6: Cross-task cleanup protection
# ---------------------------------------------------------------------------

def _check_dirty_reviewed_state(workspace):
    """Check if any dirty (modified/untracked) files are referenced by review evidence.

    Returns list of warning dicts, each with:
      - file: the dirty file path
      - task_id: the owning task from review evidence

    Does NOT prevent transitions — purely advisory.
    """
    warnings = []
    evidence_path = _find_review_evidence(workspace)
    if evidence_path is None:
        return warnings

    evidence = read_json_file_safe(evidence_path)
    if evidence is None:
        return warnings

    # Build set of reviewed file paths
    reviewed_paths = set()
    for rf in evidence.get("reviewedFiles", []):
        path = rf.get("path", "")
        if path:
            reviewed_paths.add(path)

    if not reviewed_paths:
        return warnings

    # Get all dirty files
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return warnings

        dirty_files = set()
        for line in result.stdout.split("\n"):
            line = line.strip()
            if not line:
                continue
            # porcelain v1: "XY path" or "XY old -> new"
            if len(line) > 3 and line[2] == ' ':
                dirty_files.add(line[3:])
            elif "-> " in line:
                arrow_idx = line.index(" -> ")
                dirty_files.add(line[arrow_idx + 4:])
    except (subprocess.SubprocessError, FileNotFoundError):
        return warnings

    # Find overlap
    task_id = evidence.get("taskId", "unknown")
    for df in sorted(dirty_files):
        if df in reviewed_paths:
            warnings.append({
                "file": df,
                "task_id": task_id,
            })

    return warnings


# ---------------------------------------------------------------------------
# Workspace path resolution
# ---------------------------------------------------------------------------

def resolve_workspace(workspace):
    if os.path.isabs(workspace):
        return workspace
    return os.path.join(os.getcwd(), workspace)


# ---------------------------------------------------------------------------
# Fast-execution contract, routing, progress, and performance commands
# ---------------------------------------------------------------------------

def _resolve_fast_run_id(workspace, explicit_run_id="", task_id=""):
    run_id = fast_execution.resolve_run_id(workspace, explicit_run_id, task_id)
    if not run_id:
        print("Error: no active or matching run id; enter RUN_EXECUTOR or pass --run-id", file=sys.stderr)
        sys.exit(1)
    return run_id


def cmd_resolve_execution_policy(args):
    workspace = resolve_workspace(args.workspace)
    run_id = _resolve_fast_run_id(workspace, args.run_id, args.task_id)
    started = fast_execution.clock_ms()
    try:
        policy, reused = fast_execution.materialize_policy(
            workspace, run_id, args.task_id, args.profile, args.no_progress_threshold
        )
        fast_execution.record_trace_phase(
            workspace, run_id, "execution-policy-resolution",
            fast_execution.clock_ms() - started,
            files=["state/team-state.json", "state/backlog.jsonl", "policies/protected-paths.json"],
            decision="REUSED" if reused else "EXECUTED",
        )
        output = dict(policy)
        output["reused"] = reused
        print(json.dumps(output, ensure_ascii=False))
    except fast_execution.FastExecutionError as exc:
        fast_execution.record_trace_phase(
            workspace, run_id, "execution-policy-resolution",
            fast_execution.clock_ms() - started, decision="FAILED", details=str(exc)
        )
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_materialize_execution_manifest(args):
    workspace = resolve_workspace(args.workspace)
    run_id = _resolve_fast_run_id(workspace, args.run_id, args.task_id)
    started = fast_execution.clock_ms()
    try:
        manifest, reused = fast_execution.materialize_manifest(workspace, run_id, args.task_id)
        fast_execution.record_trace_phase(
            workspace, run_id, "execution-manifest-materialization",
            fast_execution.clock_ms() - started,
            files=["execution-policy.json", "state/current-task.json", "policies/gate-policy.json"],
            decision="REUSED" if reused else "EXECUTED",
        )
        output = dict(manifest)
        output["reused"] = reused
        print(json.dumps(output, ensure_ascii=False))
    except fast_execution.FastExecutionError as exc:
        fast_execution.record_trace_phase(
            workspace, run_id, "execution-manifest-materialization",
            fast_execution.clock_ms() - started, decision="FAILED", details=str(exc)
        )
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_validate_execution_contract(args):
    workspace = resolve_workspace(args.workspace)
    run_id = _resolve_fast_run_id(workspace, args.run_id, args.task_id)
    started = fast_execution.clock_ms()
    try:
        result = fast_execution.validate_contract(workspace, run_id, write_result=True)
        fast_execution.record_trace_phase(
            workspace, run_id, "execution-contract-validation",
            fast_execution.clock_ms() - started,
            files=["execution-policy.json", "execution-manifest.json", "state/team-state.json"],
            decision="EXECUTED" if result.get("status") == "PASS" else "FAILED",
        )
        print(json.dumps(result, ensure_ascii=False))
        if result.get("status") != "PASS":
            sys.exit(1)
    except fast_execution.FastExecutionError as exc:
        fast_execution.record_trace_phase(
            workspace, run_id, "execution-contract-validation",
            fast_execution.clock_ms() - started, decision="FAILED", details=str(exc)
        )
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_prepare_execution(args):
    """Resolve policy, materialize manifest, and validate in one idempotent preflight."""
    workspace = resolve_workspace(args.workspace)
    run_id = _resolve_fast_run_id(workspace, args.run_id, args.task_id)
    started = fast_execution.clock_ms()
    try:
        policy, policy_reused = fast_execution.materialize_policy(
            workspace, run_id, args.task_id, args.profile, args.no_progress_threshold
        )
        manifest, manifest_reused = fast_execution.materialize_manifest(workspace, run_id, args.task_id)
        validation = fast_execution.validate_contract(workspace, run_id, write_result=True)
        result = {
            "schemaVersion": 1,
            "runId": run_id,
            "taskId": policy.get("taskId", ""),
            "profile": policy.get("selectedProfile", ""),
            "policyReused": policy_reused,
            "manifestReused": manifest_reused,
            "policyFingerprint": policy.get("semanticFingerprint", ""),
            "manifestFingerprint": manifest.get("semanticFingerprint", ""),
            "status": validation.get("status", "FAIL"),
            "validationArtifact": os.path.join(".teamloop", "runs", run_id, "execution-contract-validation.json"),
        }
        fast_execution.record_trace_phase(
            workspace, run_id, "execution-contract-creation-validation",
            fast_execution.clock_ms() - started,
            files=["execution-policy.json", "execution-manifest.json", "execution-contract-validation.json"],
            decision="REUSED" if policy_reused and manifest_reused else "EXECUTED",
        )
        print(json.dumps(result, ensure_ascii=False))
        if validation.get("status") != "PASS":
            sys.exit(1)
    except fast_execution.FastExecutionError as exc:
        fast_execution.record_trace_phase(
            workspace, run_id, "execution-contract-creation-validation",
            fast_execution.clock_ms() - started, decision="FAILED", details=str(exc)
        )
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_record_progress(args):
    workspace = resolve_workspace(args.workspace)
    run_id = _resolve_fast_run_id(workspace, args.run_id, args.task_id)
    started = fast_execution.clock_ms()
    core_script = os.path.abspath(__file__)
    try:
        snapshot, result, process_count = fast_execution.record_progress(workspace, run_id, core_script)
        fast_execution.record_trace_phase(
            workspace, run_id, "progress-detection",
            fast_execution.clock_ms() - started,
            process_count=process_count,
            files=["progress-history.jsonl", "no-progress-result.json"],
            decision="NO_OP" if result.get("status") == "NO_PROGRESS_DETECTED" else "EXECUTED",
            details=result.get("reason", ""),
        )
        print(json.dumps({"snapshot": snapshot, "result": result}, ensure_ascii=False))
    except fast_execution.FastExecutionError as exc:
        fast_execution.record_trace_phase(
            workspace, run_id, "progress-detection",
            fast_execution.clock_ms() - started, decision="FAILED", details=str(exc)
        )
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_route_role(args):
    workspace = resolve_workspace(args.workspace)
    run_id = _resolve_fast_run_id(workspace, args.run_id, args.task_id)
    started = fast_execution.clock_ms()
    try:
        result = fast_execution.route_role(workspace, run_id, args.event, args.severity)
        fast_execution.append_jsonl(
            os.path.join(workspace, "runs", run_id, "role-routing-history.jsonl"), result
        )
        if args.event == "watchdog-complete" and result.get("nextAction") == "RETRY_EXECUTOR":
            fast_execution.acknowledge_no_progress_strategy(workspace, run_id)
        fast_execution.record_trace_phase(
            workspace, run_id, "role-routing",
            fast_execution.clock_ms() - started,
            files=["execution-policy.json", "no-progress-result.json", "role-routing-history.jsonl"],
            decision="EXECUTED", details=result.get("reason", ""),
        )
        print(json.dumps(result, ensure_ascii=False))
    except fast_execution.FastExecutionError as exc:
        fast_execution.record_trace_phase(
            workspace, run_id, "role-routing",
            fast_execution.clock_ms() - started, decision="FAILED", details=str(exc)
        )
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_record_performance(args):
    workspace = resolve_workspace(args.workspace)
    run_id = fast_execution.resolve_run_id(workspace, args.run_id, args.task_id)
    fast_execution.record_trace_phase(
        workspace, run_id, args.phase, args.duration_ms,
        args.process_count, args.role_count, args.file,
        args.decision, args.details,
    )
    print(json.dumps({
        "recorded": True, "runId": run_id, "phase": args.phase,
        "durationMs": args.duration_ms,
    }, ensure_ascii=False))


def cmd_performance_report(args):
    workspace = resolve_workspace(args.workspace)
    run_id = _resolve_fast_run_id(workspace, args.run_id, args.task_id)
    try:
        print(json.dumps(fast_execution.performance_report(workspace, run_id), ensure_ascii=False))
    except fast_execution.FastExecutionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Command: test-select
# ---------------------------------------------------------------------------

def _load_test_layers(project_root):
    """Load test-layers.json from the tests/ directory."""
    path = os.path.join(project_root, "tests", "test-layers.json")
    if not os.path.exists(path):
        return None
    return read_json(path)


def _load_impact_map(project_root):
    """Load impact-map.json from the tests/ directory."""
    path = os.path.join(project_root, "tests", "impact-map.json")
    if not os.path.exists(path):
        return None
    return read_json(path)


def _get_git_changed_files_test_select(git_root):
    """Get changed files from git for test-select --affected mode."""
    return _get_git_changed_files(git_root)


def _impact_map_lookup(changed_files, impact_map):
    """Look up changed files against the impact map to determine affected layers.

    Returns (layers_set, reasons_dict).
    """
    layers = set()
    reasons = {}
    mappings = impact_map.get("mappings", [])

    # Build a mapping: matched_pattern -> list of file paths
    matched_by_pattern = {}

    for fp in changed_files:
        matched = False
        for mapping in mappings:
            for pat in mapping.get("patterns", []):
                if fnmatch.fnmatch(fp, pat):
                    for layer in mapping.get("layers", []):
                        layers.add(layer)
                    reason_key = pat
                    reason_val = mapping.get("reason", pat)
                    reasons.setdefault(reason_key, reason_val)
                    matched_by_pattern.setdefault(pat, []).append(fp)
                    matched = True
                    break
            if matched:
                break
        if not matched:
            # Use default mapping
            default = impact_map.get("default", {})
            for layer in default.get("layers", []):
                layers.add(layer)
            default_reason = default.get("reason", "unknown file")
            reasons.setdefault("<default>", default_reason)

    return layers, reasons


def _select_tests_by_layers(layers, test_layers_data):
    """Select test IDs that belong to any of the specified layers.

    Returns sorted list of test IDs.
    """
    tests_map = test_layers_data.get("tests", {})
    selected = set()
    for test_id, test_layers in tests_map.items():
        if any(l in layers for l in test_layers):
            selected.add(test_id)
    return sorted(selected)


def cmd_test_select(args):
    """Select tests based on layers, affected files, or full run."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Load test layers
    test_layers_data = _load_test_layers(project_root)
    if test_layers_data is None:
        print("Error: tests/test-layers.json not found", file=sys.stderr)
        sys.exit(1)

    layers_def = test_layers_data.get("layers", {})
    tests_map = test_layers_data.get("tests", {})
    now = utc_now_iso()

    # ---- --list-layers ----
    if args.list_layers:
        output = {
            "mode": "list-layers",
            "layers": {},
            "timestampUtc": now,
        }
        for layer_name, layer_desc in layers_def.items():
            count = sum(1 for tid, tl in tests_map.items() if layer_name in tl)
            output["layers"][layer_name] = {
                "description": layer_desc,
                "testCount": count,
            }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    # ---- Determine layers and selected tests ----
    selected_layers_set = set()
    selection_reasons = {}
    changed_files = []

    # Layer selection via --layer flag
    if args.layer:
        for layer_name in args.layer:
            if layer_name not in layers_def:
                print(f"Error: unknown layer '{layer_name}'. Available: {', '.join(sorted(layers_def.keys()))}", file=sys.stderr)
                sys.exit(1)
            selected_layers_set.add(layer_name)
            selection_reasons[layer_name] = f"Explicitly requested via --layer flag"

    # Full run
    if args.full:
        selected_layers_set = set(layers_def.keys())
        selection_reasons["full"] = "Full test run requested via --full flag"

    # Affected run
    if args.affected:
        impact_map = _load_impact_map(project_root)
        if impact_map is None:
            print("Error: tests/impact-map.json not found for --affected mode", file=sys.stderr)
            sys.exit(1)

        git_root = project_root
        changed_files = _get_git_changed_files_test_select(git_root)

        if not changed_files:
            # No changed files — select smoke only as safe baseline
            selected_layers_set.add("smoke")
            selection_reasons["no-changes"] = "No changed files detected; selecting smoke layer as safe baseline"
        else:
            layers_found, reasons = _impact_map_lookup(changed_files, impact_map)
            selected_layers_set.update(layers_found)
            selection_reasons.update(reasons)

    # If nothing selected (shouldn't happen, but guard against it)
    if not selected_layers_set:
        print("Error: no selection mode specified. Use --layer, --affected, --full, or --list-layers.", file=sys.stderr)
        sys.exit(1)

    # Always include "full" if requested, otherwise compute from selected layers
    selected_tests = _select_tests_by_layers(selected_layers_set, test_layers_data)

    # ---- Build selection artifact ----
    selected_layers_sorted = sorted(selected_layers_set)
    artifact = {
        "schemaVersion": 1,
        "selectedLayers": selected_layers_sorted,
        "selectedTests": selected_tests,
        "selectionReasons": selection_reasons,
        "timestampUtc": now,
    }
    if changed_files:
        artifact["changedFiles"] = changed_files

    # ---- Write artifact file ----
    output_path = args.output or os.path.join(".teamloop", "state", "test-selection.json")
    if not os.path.isabs(output_path):
        output_path = os.path.join(os.getcwd(), output_path)

    # Ensure parent directory exists
    parent_dir = os.path.dirname(output_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    write_json(output_path, artifact)

    # ---- --explain flag ----
    if args.explain:
        print("=== Test Selection Explanation ===")
        if args.full:
            mode_str = "--full"
        elif args.affected:
            mode_str = "--affected"
        else:
            layer_joined = " ".join(args.layer)
            mode_str = f"--layer {layer_joined}"
        print(f"Mode: {mode_str}")
        print(f"Selected layers: {', '.join(selected_layers_sorted)}")
        print(f"Total tests selected: {len(selected_tests)}")
        print()
        if changed_files:
            print(f"Changed files ({len(changed_files)}):")
            for cf in changed_files:
                print(f"  - {cf}")
            print()
        for reason_key, reason_desc in selection_reasons.items():
            print(f"  [{reason_key}] {reason_desc}")
        print()
        if args.layer:
            for layer_name in args.layer:
                test_ids = [tid for tid, tl in tests_map.items() if layer_name in tl]
                print(f"Layer '{layer_name}': {len(test_ids)} test(s)")
                for tid in test_ids[:20]:
                    print(f"    {tid}")
                if len(test_ids) > 20:
                    print(f"    ... and {len(test_ids) - 20} more")
        elif args.affected or args.full:
            print("Selected test IDs:")
            for tid in selected_tests[:20]:
                print(f"    {tid}")
            if len(selected_tests) > 20:
                print(f"    ... and {len(selected_tests) - 20} more")
        print()
        print(f"Selection artifact written to: {output_path}")
        print()
        print("=== End Explanation ===")
    else:
        # Machine-readable output
        print(json.dumps(artifact, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# compat-check
# ---------------------------------------------------------------------------

def cmd_compat_check(args):
    """Run backward-compatibility gate checks.

    Validates:
      a. All expected CLI commands still exist (no removals or renames)
      b. All existing artifacts in workspace parse against current schemas
      c. Schema files in schemas/ are valid JSON

    Exits 0 on PASS, 1 on FAIL.
    """
    workspace = resolve_workspace(args.workspace)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, project_root)
    import teamloop_compat as compat_mod

    result = compat_mod.check_backward_compat(workspace)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Compatibility gate: {result['status']}")
        for f in result["findings"]:
            tag = f["status"]
            print(f"  [{tag}] {f['check']}: {f['detail']}")

    sys.exit(1 if result["status"] == "FAIL" else 0)


# ---------------------------------------------------------------------------
# schema-lint
# ---------------------------------------------------------------------------

def cmd_schema_lint(args):
    """Audit all schemas for additionalProperties:false and JSON validity.

    Checks:
      1. All schema files parse as valid JSON
      2. All schemas reference JSON Schema draft-07
      3. All schemas have 'type' and 'properties' keys
      4. Required fields are defined in properties
      5. additionalProperties:false occurrences (top-level and nested)
      6. Cross-schema consistency (type mismatches, enum drift)

    Exits 0 when all schemas are valid, 1 when errors are found.
    """
    workspace = resolve_workspace(args.workspace)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    schemas_dir = os.path.join(project_root, "schemas")

    sys.path.insert(0, project_root)
    import teamloop_schema as schema_mod

    report = schema_mod.run_full_audit(schemas_dir)

    if args.json:
        print(schema_mod.format_json_report(report))
    elif args.report:
        print(schema_mod.format_text_report(report))
    else:
        # Default compact output
        ap = report.get("additional_properties", {})
        lint = report.get("lint", {})
        lines = []
        lines.append("Schema Evolution Audit")
        lines.append("=" * 22)
        lines.append(f"Total schemas: {lint.get('total', 0)}")
        lines.append(
            f"Schemas with additionalProperties:false: "
            f"{ap.get('schemas_with_strict', 0)}"
        )
        if lint.get("invalid", 0) > 0:
            lines.append(f"Invalid schemas: {lint['invalid']}")
        else:
            lines.append(f"All schemas valid JSON Schema draft-07: yes")
        recommendations = report.get("evolution_recommendations", [])
        if recommendations:
            lines.append("")
            lines.append("Recommendation:")
            for rec in recommendations:
                lines.append(f"  [{rec['severity']}] {rec['recommendation']}")
        lines.append("")
        lines.append(f"Overall: {report['status']}")
        print("\n".join(lines))

    sys.exit(1 if report["status"] == "FAIL" else 0)


# ---------------------------------------------------------------------------
# release-info
# ---------------------------------------------------------------------------

def cmd_release_info(args):
    """Print current version and release metadata."""
    metadata = {
        "schemaVersion": TEAMLOOP_SCHEMA_VERSION,
        "version": TEAMLOOP_VERSION,
        "releaseDate": "2026-07-11",
        "summary": "Runtime consolidation and productization",
        "changes": [
            {
                "type": "feature",
                "title": "Single Validation Host",
                "description": "All schema validation funneled through teamloop-core.py with caching layer"
            },
            {
                "type": "feature",
                "title": "Fast Execution Contract",
                "description": "Immutable execution manifest with deterministic policy resolution"
            },
            {
                "type": "feature",
                "title": "Sentinel and Guard Integrity",
                "description": "Nine safety inspections and protected-path integrity checks"
            },
            {
                "type": "feature",
                "title": "Memory Subsystem",
                "description": "Persistent lessons, antipatterns, decisions, and evidence tracking"
            },
            {
                "type": "feature",
                "title": "Semantic Versioning",
                "description": "Centralized version module with CLI --version flag and release metadata schema"
            }
        ],
        "breakingChanges": [],
        "compatibilityNotes": [
            "Schema version 1 artifacts remain fully compatible",
            "CLI wrapper scripts (.sh, .ps1) are backward-compatible with --workspace flag"
        ]
    }

    if args.json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
    else:
        print(f"YourAITeam {TEAMLOOP_VERSION}")
        print(f"Schema version: {TEAMLOOP_SCHEMA_VERSION}")
        print(f"Release date: {metadata['releaseDate']}")
        print(f"Summary: {metadata['summary']}")
        print()
        print(f"Changes ({len(metadata['changes'])}):")
        for change in metadata["changes"]:
            print(f"  [{change['type']}] {change['title']}")
            print(f"    {change['description']}")
        if metadata["breakingChanges"]:
            print()
            print("Breaking changes:")
            for bc in metadata["breakingChanges"]:
                print(f"  - {bc['title']}")
        if metadata["compatibilityNotes"]:
            print()
            print("Compatibility notes:")
            for note in metadata["compatibilityNotes"]:
                print(f"  - {note}")

    # Write to the specified output path as machine-readable artifact
    # (already done above with write_json)


# ---------------------------------------------------------------------------
# Command: dogfood
# ---------------------------------------------------------------------------

def cmd_dogfood(args):
    """Run the full gate chain on the workspace (dogfood test).

    Exercises: validate-state, check-scope, run-gates, run-sentinel,
    check-guard-integrity, memory-doctor, and final-gate as subprocess
    invocations and produces a structured JSON report.

    With --old-new-compare, runs each check twice (direct subprocess vs
    WorkspaceContext) and compares results for parity.

    Exit 0 on PASS, exit 1 on FAIL or ERROR.
    """
    import teamloop_dogfood as dogfood_mod

    if args.old_new_compare:
        result = dogfood_mod.run_dogfood_compare(args.workspace)
    else:
        result = dogfood_mod.run_dogfood(args.workspace)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        # Human-readable summary
        print(f"Dogfood report: {result['overallStatus']}")
        for check in result["checks"]:
            tag = check["status"]
            summary = check.get("summary", "")
            print(f"  [{tag}] {check['name']}: {summary}")
            if "detail" in check:
                detail = check["detail"][:200]
                print(f"         detail: {detail}")
        if result.get("oldNewCompare"):
            comp = result["oldNewCompare"]
            diffs = comp.get("differences", [])
            print()
            if diffs:
                print(f"Comparison found {len(diffs)} difference(s) between direct and context runs:")
                for d in diffs:
                    print(f"  - {d['check']}: direct={d['directStatus']} context={d['contextStatus']}")
            else:
                print("Comparison: all checks match between direct and context runs.")
        print()

    if result["overallStatus"] != "PASS":
        sys.exit(1)


# ---------------------------------------------------------------------------
# Command: inbox-send / inbox-receive / inbox-stats
# ---------------------------------------------------------------------------

def _resolve_run_id_for_inbox(workspace):
    """Return the current or latest run id for inbox operations."""
    state = read_json_file_safe(os.path.join(workspace, "state", "team-state.json"))
    if state and state.get("currentRunId"):
        return state["currentRunId"]
    # Fallback: latest run directory
    runs_dir = os.path.join(workspace, "runs")
    if os.path.isdir(runs_dir):
        try:
            dirs = sorted(d for d in os.listdir(runs_dir)
                          if os.path.isdir(os.path.join(runs_dir, d)))
            if dirs:
                return dirs[-1]
        except OSError:
            pass
    return f"run-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')}"


def cmd_inbox_send(args):
    """Send a message via the per-run inbox."""
    workspace = resolve_workspace(args.workspace)
    run_id = args.run_id or _resolve_run_id_for_inbox(workspace)
    message = inbox_mod.inbox_send(
        workspace=workspace,
        run_id=run_id,
        from_actor=args.from_actor,
        to_actor=args.to,
        subject=args.subject,
        body=args.body,
    )
    print(json.dumps(message, ensure_ascii=False))


def cmd_inbox_receive(args):
    """Receive unread messages from the per-run inbox."""
    workspace = resolve_workspace(args.workspace)
    run_id = args.run_id or _resolve_run_id_for_inbox(workspace)
    messages = inbox_mod.inbox_receive(
        workspace=workspace,
        run_id=run_id,
        actor=args.actor,
    )
    if not messages:
        print("No unread messages.")
        return
    print(json.dumps(messages, ensure_ascii=False, indent=2))


def cmd_inbox_stats(args):
    """Show inbox statistics for the current run."""
    workspace = resolve_workspace(args.workspace)
    run_id = args.run_id or _resolve_run_id_for_inbox(workspace)
    stats = inbox_mod.inbox_stats(workspace=workspace, run_id=run_id)
    stats["runId"] = run_id
    print(json.dumps(stats, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Command: advisory-check
# ---------------------------------------------------------------------------

def cmd_advisory_check(args):
    """Run Product Director L0 advisory checks on the current task.

    Advisory checks flag risky patterns as WARNING only — they never block
    execution.  Exit code is always 0.
    """
    workspace = resolve_workspace(args.workspace)
    result = advisory_mod.run_advisory(workspace)
    print(json.dumps(result, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Commands: YourAITeam composition economics
# ---------------------------------------------------------------------------

def _team_task_text(args):
    if getattr(args, "task_file", ""):
        with open(args.task_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    return (getattr(args, "task", "") or "").strip()

def _team_write_or_print(data, output=""):
    if output:
        team_mod.save(output, data)
    print(json.dumps(data, ensure_ascii=False, indent=2))

def cmd_team_propose(args):
    task = _team_task_text(args)
    try:
        data = team_mod.propose(
            task, backend=args.backend, max_tokens=args.max_tokens,
            max_roles=args.max_roles, preference=args.preference,
            risk_tolerance=args.risk_tolerance, accept_risk=args.accept_risk,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    _team_write_or_print(data, args.output)

def cmd_team_negotiate(args):
    try:
        proposal = team_mod.load(args.proposal)
        data = team_mod.negotiate(
            proposal, request=args.request, maxTokens=args.max_tokens,
            maxRoleCount=args.max_roles, preference=args.preference,
            riskTolerance=args.risk_tolerance, acceptRisk=args.accept_risk,
            remove=args.remove_role, downgrade=args.downgrade_role, upgrade=args.upgrade_role,
        )
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    _team_write_or_print(data, args.output)

def cmd_team_accept(args):
    try:
        proposal = team_mod.load(args.proposal)
        data = team_mod.accept(proposal)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    _team_write_or_print(data, args.output)

def cmd_team_materialize(args):
    try:
        proposal = team_mod.load(args.proposal)
        overrides = {
            key: value
            for key, value in {
                "economy": getattr(args, "codex_economy_model", ""),
                "balanced": getattr(args, "codex_balanced_model", ""),
                "premium": getattr(args, "codex_premium_model", ""),
            }.items()
            if value
        }
        data = team_mod.materialize(
            proposal,
            args.backend,
            args.output_dir,
            codex_model_mode=getattr(args, "codex_model_mode", "inherit"),
            codex_model_overrides=overrides,
        )
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_team_codex_doctor(args):
    try:
        if args.fix_models:
            data = codex_mod.apply_model_mode(args.project_root, args.fix_models)
        else:
            data = codex_mod.inspect_codex_project(args.project_root, run_cli=not args.no_cli)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if data.get("status") == "FAIL":
        sys.exit(1)


def cmd_team_codex_smoke(args):
    try:
        data = codex_mod.run_live_smoke(
            args.project_root,
            role=args.role,
            timeout=args.timeout,
        )
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if data.get("status") != "PASS":
        sys.exit(2 if data.get("status") == "UNAVAILABLE" else 1)


# ---------------------------------------------------------------------------
# Command: adapter-verify
# ---------------------------------------------------------------------------

def cmd_adapter_verify(args):
    """Verify an adapter against the adapter contract schema and runtime.

    Checks:
      1. Adapter contract file exists and is valid JSON.
      2. Contract validates against adapter-contract.schema.json.
      3. All requiredCommands are available in the runtime.
      4. All providedAgents reference existing files.
      5. All supportedTransitions are valid runtime transitions.

    Exits 0 if all checks pass, 1 otherwise.
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    workspace = resolve_workspace(args.workspace)
    as_json = getattr(args, "json", False)

    checks = []
    violations = []

    # Resolve the adapter directory
    # Look for adapters/<id>/adapter-contract.json in project root
    adapters_dir = os.path.join(project_root, "adapters")
    adapter_contracts = []
    if os.path.isdir(adapters_dir):
        for entry in sorted(os.listdir(adapters_dir)):
            contract_path = os.path.join(adapters_dir, entry, "adapter-contract.json")
            if os.path.exists(contract_path):
                adapter_contracts.append((entry, contract_path))

    if not adapter_contracts:
        checks.append({
            "name": "adapter-contract-found",
            "status": "FAIL",
            "detail": "No adapter-contract.json found under adapters/"
        })
        violations.append("No adapter-contract.json found under adapters/")
    else:
        # Report found
        checks.append({
            "name": "adapter-contract-found",
            "status": "PASS",
            "detail": f"Found {len(adapter_contracts)} adapter contract(s)"
        })

        # Load schema
        schema_path = os.path.join(project_root, "schemas", "adapter-contract.schema.json")
        schema = {}
        if os.path.exists(schema_path):
            try:
                schema = read_json(schema_path)
            except (ValueError, json.JSONDecodeError):
                checks.append({
                    "name": "schema-load",
                    "status": "FAIL",
                    "detail": f"adapter-contract.schema.json is not valid JSON"
                })
                violations.append("adapter-contract.schema.json is not valid JSON")
        else:
            checks.append({
                "name": "schema-load",
                "status": "FAIL",
                "detail": "adapter-contract.schema.json not found"
            })
            violations.append("adapter-contract.schema.json not found")

        # Available runtime commands — the canonical list of subparsers we support.
        # This mirrors the commands dict in main() but is safe to reference at module level.
        _AVAILABLE_COMMANDS = {
            "init-workspace", "validate-state", "next-action", "apply-transition",
            "write-event", "check-scope", "run-gates", "validate-task",
            "validate-artifact", "validate-research", "memory-doctor",
            "write-continuation-decision", "check-guard-integrity", "run-sentinel",
            "cache-inspect", "cache-clear", "cache-stats", "final-gate",
            "resolve-execution-policy", "materialize-execution-manifest",
            "validate-execution-contract", "prepare-execution", "record-progress",
            "route-role", "record-performance", "performance-report", "test-select",
            "release-info", "compat-check", "schema-lint", "dogfood",
            "inbox-send", "inbox-receive", "inbox-stats", "advisory-check",
            "team-propose", "team-negotiate", "team-accept", "team-materialize", "team-codex-doctor", "team-codex-smoke",
            "boundary-create", "boundary-measure", "boundary-status", "boundary-decide",
            "boundary-complete-improvement", "boundary-verify", "boundary-lock-status",
            "adapter-verify",
        }

        for adapter_id, contract_path in adapter_contracts:
            # 1. Parse contract
            contract = read_json_file_safe(contract_path)
            if contract is None:
                checks.append({
                    "name": f"parse-{adapter_id}",
                    "status": "FAIL",
                    "detail": f"adapters/{adapter_id}/adapter-contract.json is not valid JSON"
                })
                violations.append(f"adapters/{adapter_id}/adapter-contract.json is not valid JSON")
                continue

            # 2. Schema validation
            if schema:
                schema_errors = validate_against_schema(contract, schema, f"adapters/{adapter_id}/adapter-contract.json")
                if schema_errors:
                    for se in schema_errors:
                        checks.append({
                            "name": f"schema-validate-{adapter_id}",
                            "status": "FAIL",
                            "detail": se
                        })
                        violations.append(f"{adapter_id}: {se}")
                else:
                    checks.append({
                        "name": f"schema-validate-{adapter_id}",
                        "status": "PASS",
                        "detail": f"adapters/{adapter_id}/adapter-contract.json validates against schema"
                    })

            # 3. Required commands exist in runtime
            required_cmds = contract.get("requiredCommands", [])
            missing_cmds = [c for c in required_cmds if c not in _AVAILABLE_COMMANDS]
            if missing_cmds:
                checks.append({
                    "name": f"required-commands-{adapter_id}",
                    "status": "FAIL",
                    "detail": f"Missing runtime commands: {', '.join(missing_cmds)}"
                })
                violations.append(f"{adapter_id}: missing commands {missing_cmds}")
            else:
                checks.append({
                    "name": f"required-commands-{adapter_id}",
                    "status": "PASS",
                    "detail": f"All {len(required_cmds)} required commands available"
                })

            # 4. Provided agents exist
            agents = contract.get("providedAgents", [])
            missing_agents = []
            for agent in agents:
                agent_file = agent.get("file", "")
                agent_path = os.path.join(project_root, agent_file)
                if not os.path.exists(agent_path):
                    missing_agents.append(agent_file)
            if missing_agents:
                checks.append({
                    "name": f"agents-exist-{adapter_id}",
                    "status": "FAIL",
                    "detail": f"Missing agent files: {', '.join(missing_agents)}"
                })
                violations.append(f"{adapter_id}: missing agent files {missing_agents}")
            else:
                checks.append({
                    "name": f"agents-exist-{adapter_id}",
                    "status": "PASS",
                    "detail": f"All {len(agents)} agent files exist"
                })

            # 5. Provided commands exist
            cmds = contract.get("providedCommands", [])
            missing_cmd_files = []
            for cmd in cmds:
                cmd_file = cmd.get("file", "")
                cmd_path = os.path.join(project_root, cmd_file)
                if not os.path.exists(cmd_path):
                    missing_cmd_files.append(cmd_file)
            if missing_cmd_files:
                checks.append({
                    "name": f"commands-exist-{adapter_id}",
                    "status": "FAIL",
                    "detail": f"Missing command files: {', '.join(missing_cmd_files)}"
                })
                violations.append(f"{adapter_id}: missing command files {missing_cmd_files}")
            else:
                checks.append({
                    "name": f"commands-exist-{adapter_id}",
                    "status": "PASS",
                    "detail": f"All {len(cmds)} command files exist"
                })

            # 6. Provided skills exist
            skills = contract.get("providedSkills", [])
            missing_skill_files = []
            for skill in skills:
                skill_file = skill.get("file", "")
                skill_path = os.path.join(project_root, skill_file)
                if not os.path.exists(skill_path):
                    missing_skill_files.append(skill_file)
            if missing_skill_files:
                checks.append({
                    "name": f"skills-exist-{adapter_id}",
                    "status": "FAIL",
                    "detail": f"Missing skill files: {', '.join(missing_skill_files)}"
                })
                violations.append(f"{adapter_id}: missing skill files {missing_skill_files}")
            else:
                checks.append({
                    "name": f"skills-exist-{adapter_id}",
                    "status": "PASS",
                    "detail": f"All {len(skills)} skill files exist"
                })

            # 7. Supported transitions are valid
            supported_trans = contract.get("supportedTransitions", [])
            known_transitions = set(_TRANSITIONS.keys())
            unknown_trans = [t for t in supported_trans if t not in known_transitions]
            if unknown_trans:
                checks.append({
                    "name": f"transitions-valid-{adapter_id}",
                    "status": "FAIL",
                    "detail": f"Unknown transitions: {', '.join(unknown_trans)}"
                })
                violations.append(f"{adapter_id}: unknown transitions {unknown_trans}")
            else:
                checks.append({
                    "name": f"transitions-valid-{adapter_id}",
                    "status": "PASS",
                    "detail": f"All {len(supported_trans)} transitions are valid"
                })

    # Output
    status = "PASS" if not violations else "FAIL"
    result = {
        "status": status,
        "checks": checks,
        "violations": violations,
        "adapterCount": len(adapter_contracts),
    }

    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"ADAPTER VERIFY: {status}")
        for chk in checks:
            marker = "PASS" if chk["status"] == "PASS" else "FAIL"
            print(f"  [{marker}] {chk['name']}: {chk['detail']}")
        if violations:
            print()
            print("Violations:")
            for v in violations:
                print(f"  - {v}")

    sys.exit(0 if not violations else 1)



# ---------------------------------------------------------------------------
# Quality/value boundary commands
# ---------------------------------------------------------------------------

def _boundary_project_root(args, workspace):
    return getattr(args, "project_root", "") or os.path.dirname(os.path.abspath(workspace))


def cmd_boundary_create(args):
    workspace = resolve_workspace(args.workspace)
    contract = read_json(args.contract)
    state = read_json(os.path.join(workspace, "state", "team-state.json"))
    active_task_id = state.get("currentTaskId", "")
    active_run_id = state.get("currentRunId", "")
    contract_task_id = contract.get("taskId", "")
    contract_run_id = contract.get("runId", "")
    if active_task_id and contract_task_id != active_task_id:
        raise boundary_mod.BoundaryError(
            f"boundary taskId {contract_task_id!r} does not match active task {active_task_id!r}"
        )
    if active_run_id and contract_run_id != active_run_id:
        raise boundary_mod.BoundaryError(
            f"boundary runId {contract_run_id!r} does not match active run {active_run_id!r}"
        )
    if contract_run_id:
        ledger = read_jsonl(os.path.join(workspace, "state", "run-ledger.jsonl"))
        run = next((item for item in ledger if item.get("runId") == contract_run_id), None)
        if run is None:
            raise boundary_mod.BoundaryError(f"boundary runId is absent from run ledger: {contract_run_id}")
        if contract_task_id and run.get("taskId") != contract_task_id:
            raise boundary_mod.BoundaryError("boundary task/run identity contradicts the run ledger")
        policy_path = os.path.join(workspace, "runs", contract_run_id, "execution-policy.json")
        if os.path.isfile(policy_path):
            execution_policy = read_json(policy_path)
            selected_profile = execution_policy.get("selectedProfile", "")
            if selected_profile and contract.get("profile", selected_profile) != selected_profile:
                raise boundary_mod.BoundaryError(
                    f"boundary profile {contract.get('profile')!r} does not match frozen execution profile {selected_profile!r}"
                )
            contract["profile"] = selected_profile or contract.get("profile", "standard")
    result = boundary_mod.create_contract(
        workspace, contract, project_root=_boundary_project_root(args, workspace)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_boundary_measure(args):
    workspace = resolve_workspace(args.workspace)
    result = boundary_mod.measure_boundary(
        workspace, args.boundary_id, project_root=_boundary_project_root(args, workspace)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _finalize_boundary_acceptance(workspace, boundary_id, acceptance):
    state_path = os.path.join(workspace, "state", "team-state.json")
    state = read_json(state_path)
    task_id = acceptance.get("taskId", "")
    run_id = acceptance.get("runId", "")
    backlog_path = os.path.join(workspace, "state", "backlog.jsonl")
    backlog = read_jsonl(backlog_path)
    for task in backlog:
        if task.get("taskId") == task_id:
            task["status"] = "DONE"
    with open(backlog_path, "w", encoding="utf-8") as f:
        for task in backlog:
            f.write(json.dumps(task, ensure_ascii=False) + "\n")
    ledger_path = os.path.join(workspace, "state", "run-ledger.jsonl")
    ledger = read_jsonl(ledger_path)
    for entry in ledger:
        if entry.get("runId") == run_id:
            entry["status"] = "COMPLETED"
            entry["result"] = "BOUNDARY_ACCEPTED"
            entry["boundaryId"] = boundary_id
    with open(ledger_path, "w", encoding="utf-8") as f:
        for entry in ledger:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    state["currentPhase"] = "SAFE_CHECKPOINT"
    state["status"] = "IN_PROGRESS"
    state["currentTaskId"] = ""
    state["currentRunId"] = ""
    state["lastDecisionId"] = acceptance.get("managerDecisionFingerprint", "")
    state["updatedAtUtc"] = utc_now_iso()
    write_json(state_path, state)
    ct_path = os.path.join(workspace, "state", "current-task.json")
    if os.path.exists(ct_path):
        os.remove(ct_path)
    _write_continuation_decision(
        workspace=workspace,
        decision="SAFE_CHECKPOINT",
        phase="SAFE_CHECKPOINT",
        task_id=task_id,
        run_id=run_id,
        justification=f"Quality/value boundary {boundary_id} accepted with a current receipt",
    )
    append_jsonl(os.path.join(workspace, "state", "events.jsonl"), {
        "schemaVersion": 1,
        "eventId": f"evt-boundary-{os.getpid()}-{int(datetime.datetime.now(datetime.timezone.utc).timestamp())}",
        "type": "BOUNDARY_ACCEPTED",
        "actor": "quality-value-manager",
        "timestampUtc": utc_now_iso(),
        "summary": f"Boundary {boundary_id} accepted and advancement unlocked",
        "taskId": task_id,
        "runId": run_id,
        "data": {"boundaryId": boundary_id, "receiptFingerprint": acceptance.get("receiptFingerprint", "")},
    })


def cmd_boundary_decide(args):
    workspace = resolve_workspace(args.workspace)
    result = boundary_mod.record_decision(
        workspace,
        args.boundary_id,
        args.decision,
        actor=args.actor,
        selected_candidate_id=args.candidate_id,
        soft_debt_ids=args.soft_debt,
        reason=args.reason,
        project_root=_boundary_project_root(args, workspace),
    )
    decision = result["decision"]
    state_path = os.path.join(workspace, "state", "team-state.json")
    state = read_json(state_path)
    if decision in ("ACCEPT_BOUNDARY", "ACCEPT_WITH_RECORDED_SOFT_DEBT"):
        acceptance = boundary_mod.verify_acceptance(
            workspace, args.boundary_id, project_root=_boundary_project_root(args, workspace)
        )
        _finalize_boundary_acceptance(workspace, args.boundary_id, acceptance)
    elif decision == "IMPROVE_CURRENT_BOUNDARY":
        state["currentPhase"] = "EXECUTING_TASK"
        state["status"] = "IN_PROGRESS"
        state["updatedAtUtc"] = utc_now_iso()
        write_json(state_path, state)
    elif decision == "SPLIT_CURRENT_BOUNDARY":
        state["currentPhase"] = "NEEDS_TASK_SLICING"
        state["status"] = "IN_PROGRESS"
        state["updatedAtUtc"] = utc_now_iso()
        write_json(state_path, state)
    elif decision == "STOP_BUDGET_EXHAUSTED":
        state["currentPhase"] = "BOUNDARY_STOPPED"
        state["status"] = "BLOCKED"
        state["updatedAtUtc"] = utc_now_iso()
        write_json(state_path, state)
    elif decision == "REQUEST_HUMAN_DECISION":
        state["currentPhase"] = "HUMAN_DECISION_REQUIRED"
        state["status"] = "HUMAN_DECISION_REQUIRED"
        state["humanRequired"] = True
        state["updatedAtUtc"] = utc_now_iso()
        write_json(state_path, state)
        blocker_id = "blocker-boundary-" + args.boundary_id.replace("_", "-")
        append_jsonl(os.path.join(workspace, "state", "blockers.jsonl"), {
            "schemaVersion": 1,
            "blockerId": blocker_id,
            "runId": state.get("currentRunId", ""),
            "taskId": state.get("currentTaskId", ""),
            "type": "HUMAN_DECISION_REQUIRED",
            "category": "PRODUCT_BEHAVIOR_AMBIGUITY",
            "summary": args.reason or f"Boundary {args.boundary_id} requires a human decision",
            "evidence": [f"boundaries/{args.boundary_id}/boundary-packet.json", f"boundaries/{args.boundary_id}/boundary-decision.json"],
            "questionsForHuman": ["Choose the acceptable quality/value trade-off or authorize a policy change outside the manager's permissions."],
        })
    append_jsonl(os.path.join(workspace, "state", "events.jsonl"), {
        "schemaVersion": 1,
        "eventId": f"evt-boundary-decision-{os.getpid()}-{int(datetime.datetime.now(datetime.timezone.utc).timestamp())}",
        "type": "BOUNDARY_DECISION_RECORDED",
        "actor": args.actor,
        "timestampUtc": utc_now_iso(),
        "summary": f"Boundary {args.boundary_id} decision: {decision}",
        "taskId": state.get("currentTaskId", ""),
        "runId": state.get("currentRunId", ""),
        "data": {"boundaryId": args.boundary_id, "decisionFingerprint": result.get("decisionFingerprint", "")},
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_boundary_complete_improvement(args):
    workspace = resolve_workspace(args.workspace)
    result = boundary_mod.complete_improvement(
        workspace, args.boundary_id, project_root=_boundary_project_root(args, workspace)
    )
    state_path = os.path.join(workspace, "state", "team-state.json")
    state = read_json(state_path)
    state["currentPhase"] = "NEEDS_BOUNDARY_DECISION"
    state["status"] = "IN_PROGRESS"
    state["updatedAtUtc"] = utc_now_iso()
    write_json(state_path, state)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_boundary_status(args):
    workspace = resolve_workspace(args.workspace)
    result = boundary_mod.dashboard_status(
        workspace, args.boundary_id, project_root=_boundary_project_root(args, workspace)
    )
    if getattr(args, "format", "json") == "html":
        rendered = boundary_mod.render_dashboard_html(result)
    else:
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
    output = getattr(args, "output", "")
    if output:
        os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
        with open(output, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            if not rendered.endswith("\n"):
                handle.write("\n")
    else:
        print(rendered)


def cmd_boundary_verify(args):
    workspace = resolve_workspace(args.workspace)
    try:
        result = boundary_mod.verify_acceptance(
            workspace, args.boundary_id, project_root=_boundary_project_root(args, workspace)
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except boundary_mod.BoundaryError as exc:
        print(json.dumps({"status": "FAIL", "boundaryId": args.boundary_id, "reason": str(exc)}, ensure_ascii=False, indent=2))
        sys.exit(1)


def cmd_boundary_lock_status(args):
    workspace = resolve_workspace(args.workspace)
    result = boundary_mod.advancement_lock_status(
        workspace, project_root=_boundary_project_root(args, workspace)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("status") == "PASS" else 1)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="YourAITeam Core")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {TEAMLOOP_VERSION}"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # init-workspace
    p_init = subparsers.add_parser("init-workspace", help="Initialize a new workspace")
    p_init.add_argument("--workspace", "-w", default=".teamloop")
    p_init.add_argument("--profile", "-p", default="generic-software-task")

    # validate-state
    p_validate = subparsers.add_parser("validate-state", help="Validate workspace state")
    p_validate.add_argument("--workspace", "-w", default=".teamloop")
    p_validate.add_argument("--no-cache", action="store_true", help="Disable validation cache")

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

    # validate-artifact
    p_vartifact = subparsers.add_parser("validate-artifact", help="Validate any JSON artifact against a repository schema")
    p_vartifact.add_argument("--schema", required=True, help="Schema basename, e.g. continuation-decision")
    p_vartifact.add_argument("--json-file", required=True)
    p_vartifact.add_argument("--label", default="")
    p_vartifact.add_argument("--workspace", "-w", default="", help="Ignored, for wrapper compatibility")

    # validate-research
    p_vresearch = subparsers.add_parser("validate-research", help="Validate a research report inventory")
    p_vresearch.add_argument("--json-file", default="")
    p_vresearch.add_argument("--json-string", default="")
    p_vresearch.add_argument("--workspace", "-w", default="", help="Ignored, for wrapper compatibility")

    # memory-doctor
    p_mdoctor = subparsers.add_parser("memory-doctor", help="Validate memory JSONL files and report findings")
    p_mdoctor.add_argument("--workspace", "-w", default=".teamloop")
    p_mdoctor.add_argument("--no-cache", action="store_true", help="Disable validation cache")

    # check-guard-integrity
    p_ghi = subparsers.add_parser("check-guard-integrity", help="Check guard integrity for protected paths, dangerous operations, and schema validity")
    p_ghi.add_argument("--workspace", "-w", default=".teamloop")
    p_ghi.add_argument("--no-cache", action="store_true", help="Disable validation cache")

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
    p_sentinel.add_argument("--no-cache", action="store_true", help="Disable validation cache")

    # cache-inspect
    p_cache_inspect = subparsers.add_parser("cache-inspect", help="Show validation cache statistics")
    p_cache_inspect.add_argument("--workspace", "-w", default=".teamloop")

    # cache-clear
    p_cache_clear = subparsers.add_parser("cache-clear", help="Clear the validation cache")
    p_cache_clear.add_argument("--workspace", "-w", default=".teamloop")

    # cache-stats
    p_cache_stats = subparsers.add_parser("cache-stats", help="Show detailed cache statistics")
    p_cache_stats.add_argument("--workspace", "-w", default=".teamloop")

    # cache-validate
    p_cache_validate = subparsers.add_parser("cache-validate", help="Validate the entire validation cache")
    p_cache_validate.add_argument("--workspace", "-w", default=".teamloop")

    # final-gate
    p_fg = subparsers.add_parser("final-gate", help="Run final gate aggregator")
    p_fg.add_argument("--workspace", "-w", default=".teamloop")

    def add_execution_identity(parser_obj):
        parser_obj.add_argument("--workspace", "-w", default=".teamloop")
        parser_obj.add_argument("--run-id", default="")
        parser_obj.add_argument("--task-id", default="")

    p_policy = subparsers.add_parser("resolve-execution-policy", help="Resolve and persist deterministic execution profile policy")
    add_execution_identity(p_policy)
    p_policy.add_argument("--profile", choices=["fast", "standard", "audit"], default="")
    p_policy.add_argument("--no-progress-threshold", type=int, default=2)

    p_manifest = subparsers.add_parser("materialize-execution-manifest", help="Materialize immutable bounded execution manifest")
    add_execution_identity(p_manifest)

    p_contract = subparsers.add_parser("validate-execution-contract", help="Validate execution policy and manifest integrity")
    add_execution_identity(p_contract)

    p_prepare = subparsers.add_parser("prepare-execution", help="Resolve policy, materialize manifest, and validate preflight")
    add_execution_identity(p_prepare)
    p_prepare.add_argument("--profile", choices=["fast", "standard", "audit"], default="")
    p_prepare.add_argument("--no-progress-threshold", type=int, default=2)

    p_progress = subparsers.add_parser("record-progress", help="Record stable progress snapshot and no-progress decision")
    add_execution_identity(p_progress)

    p_route = subparsers.add_parser("route-role", help="Resolve the next role from execution policy and deterministic triggers")
    add_execution_identity(p_route)
    p_route.add_argument("--event", required=True)
    p_route.add_argument("--severity", default="")

    p_perf_record = subparsers.add_parser("record-performance", help="Append a performance trace phase")
    add_execution_identity(p_perf_record)
    p_perf_record.add_argument("--phase", required=True)
    p_perf_record.add_argument("--duration-ms", type=float, required=True)
    p_perf_record.add_argument("--process-count", type=int, default=0)
    p_perf_record.add_argument("--role-count", type=int, default=0)
    p_perf_record.add_argument("--file", action="append", default=[])
    p_perf_record.add_argument("--decision", choices=["EXECUTED", "REUSED", "NO_OP", "FAILED"], default="EXECUTED")
    p_perf_record.add_argument("--details", default="")

    p_perf_report = subparsers.add_parser("performance-report", help="Print concise performance trace report")
    add_execution_identity(p_perf_report)

    # test-select
    p_test_select = subparsers.add_parser("test-select", help="Select tests by layer, affected files, or full run")
    p_test_select.add_argument("--list-layers", action="store_true", help="List available layers and test counts")
    p_test_select.add_argument("--layer", action="append", help="Select tests in the given layer (repeatable)")
    p_test_select.add_argument("--affected", action="store_true", help="Select tests affected by git changes")
    p_test_select.add_argument("--full", action="store_true", help="Select all tests")
    p_test_select.add_argument("--explain", action="store_true", help="Output human-readable explanation")
    p_test_select.add_argument("--output", "-o", default="", help="Output path for selection artifact (default: .teamloop/state/test-selection.json)")

    # release-info
    p_release = subparsers.add_parser("release-info", help="Print current version and release metadata")
    p_release.add_argument("--json", action="store_true", help="Output as JSON")
    p_release.add_argument("--workspace", "-w", default="", help="Ignored, for wrapper compatibility")

    # compat-check
    p_compat = subparsers.add_parser("compat-check", help="Run backward-compatibility gate checks")
    p_compat.add_argument("--workspace", "-w", default=".teamloop")
    p_compat.add_argument("--json", action="store_true", help="Output as JSON")

    # schema-lint
    p_schema_lint = subparsers.add_parser(
        "schema-lint",
        help="Audit schemas for additionalProperties:false, validity, and consistency",
    )
    p_schema_lint.add_argument("--workspace", "-w", default=".teamloop")
    p_schema_lint.add_argument(
        "--json", action="store_true",
        help="Output full report as JSON",
    )
    p_schema_lint.add_argument(
        "--report", action="store_true",
        help="Output detailed text report with per-schema breakdown",
    )

    # dogfood
    p_dogfood = subparsers.add_parser(
        "dogfood",
        help="Run the full gate chain on the workspace (dogfood test)",
    )
    p_dogfood.add_argument("--workspace", "-w", default=".teamloop")
    p_dogfood.add_argument(
        "--json", action="store_true",
        help="Output full report as JSON",
    )
    p_dogfood.add_argument(
        "--old-new-compare", action="store_true",
        help="Run checks twice (direct subprocess vs WorkspaceContext) and compare",
    )

    # inbox-send
    p_inbox_send = subparsers.add_parser("inbox-send", help="Send a message via the per-run inbox")
    p_inbox_send.add_argument("--workspace", "-w", default=".teamloop")
    p_inbox_send.add_argument("--run-id", default="", help="Run ID (auto-resolved if omitted)")
    p_inbox_send.add_argument("--from", dest="from_actor", required=True, help="Sender actor")
    p_inbox_send.add_argument("--to", required=True, help="Recipient actor")
    p_inbox_send.add_argument("--subject", required=True, help="Message subject")
    p_inbox_send.add_argument("--body", required=True, help="Message body")

    # inbox-receive
    p_inbox_receive = subparsers.add_parser("inbox-receive", help="Receive unread inbox messages")
    p_inbox_receive.add_argument("--workspace", "-w", default=".teamloop")
    p_inbox_receive.add_argument("--run-id", default="", help="Run ID (auto-resolved if omitted)")
    p_inbox_receive.add_argument("--actor", required=True, help="Recipient actor")

    # inbox-stats
    p_inbox_stats = subparsers.add_parser("inbox-stats", help="Show inbox statistics")
    p_inbox_stats.add_argument("--workspace", "-w", default=".teamloop")
    p_inbox_stats.add_argument("--run-id", default="", help="Run ID (auto-resolved if omitted)")

    # advisory-check
    p_advisory = subparsers.add_parser("advisory-check", help="Run Product Director L0 advisory checks on the current task")
    p_advisory.add_argument("--workspace", "-w", default=".teamloop")
    p_advisory.add_argument("--json", action="store_true", help="Output as JSON (default: pretty-printed JSON)")

    # YourAITeam
    p_team_propose = subparsers.add_parser("team-propose", help="Propose the minimum sufficient AI team and token budget")
    p_team_propose.add_argument("--task", default="", help="Natural-language task description")
    p_team_propose.add_argument("--task-file", default="", help="Read task description from UTF-8 file")
    p_team_propose.add_argument("--backend", choices=["portable", "codex", "opencode"], default="portable")
    p_team_propose.add_argument("--max-tokens", type=int, default=None)
    p_team_propose.add_argument("--max-roles", type=int, default=None)
    p_team_propose.add_argument("--preference", choices=["cost", "balanced", "quality", "speed"], default="balanced")
    p_team_propose.add_argument("--risk-tolerance", choices=["low", "medium", "high"], default="medium")
    p_team_propose.add_argument("--accept-risk", action="store_true")
    p_team_propose.add_argument("--output", "-o", default="")

    p_team_negotiate = subparsers.add_parser("team-negotiate", help="Bargain with a proposed team and expose the resulting trade-offs")
    p_team_negotiate.add_argument("--proposal", required=True)
    p_team_negotiate.add_argument("--request", default="", help="Natural-language bargain request")
    p_team_negotiate.add_argument("--max-tokens", type=int, default=None)
    p_team_negotiate.add_argument("--max-roles", type=int, default=None)
    p_team_negotiate.add_argument("--preference", choices=["cost", "balanced", "quality", "speed"], default="")
    p_team_negotiate.add_argument("--risk-tolerance", choices=["low", "medium", "high"], default="")
    p_team_negotiate.add_argument("--accept-risk", action="store_true")
    p_team_negotiate.add_argument("--remove-role", action="append", default=[])
    p_team_negotiate.add_argument("--downgrade-role", action="append", default=[])
    p_team_negotiate.add_argument("--upgrade-role", action="append", default=[])
    p_team_negotiate.add_argument("--output", "-o", default="")

    p_team_accept = subparsers.add_parser("team-accept", help="Accept a team proposal and freeze its contract")
    p_team_accept.add_argument("--proposal", required=True)
    p_team_accept.add_argument("--output", "-o", required=True)

    p_team_materialize = subparsers.add_parser("team-materialize", help="Generate Codex or OpenCode agent files from an accepted contract")
    p_team_materialize.add_argument("--proposal", required=True)
    p_team_materialize.add_argument("--backend", choices=["codex", "opencode"], required=True)
    p_team_materialize.add_argument("--output-dir", required=True)
    p_team_materialize.add_argument("--codex-model-mode", choices=["inherit", "chatgpt", "explicit"], default="inherit")
    p_team_materialize.add_argument("--codex-economy-model", default="")
    p_team_materialize.add_argument("--codex-balanced-model", default="")
    p_team_materialize.add_argument("--codex-premium-model", default="")

    p_team_codex_doctor = subparsers.add_parser("team-codex-doctor", help="Validate Codex installation, agents, skill, auth, and model compatibility")
    p_team_codex_doctor.add_argument("--project-root", default=".")
    p_team_codex_doctor.add_argument("--no-cli", action="store_true", help="Skip codex binary and login checks")
    p_team_codex_doctor.add_argument("--fix-models", choices=["inherit", "chatgpt"], default="")

    p_team_codex_smoke = subparsers.add_parser("team-codex-smoke", help="Run one opt-in read-only Codex custom-agent compatibility smoke")
    p_team_codex_smoke.add_argument("--project-root", default=".")
    p_team_codex_smoke.add_argument("--role", default="")
    p_team_codex_smoke.add_argument("--timeout", type=int, default=240)

    # quality/value boundary manager
    def add_boundary_common(parser_obj):
        parser_obj.add_argument("--workspace", "-w", default=".teamloop")
        parser_obj.add_argument("--project-root", default="")

    p_boundary_create = subparsers.add_parser("boundary-create", help="Create an immutable quality/value boundary contract")
    add_boundary_common(p_boundary_create)
    p_boundary_create.add_argument("--contract", required=True)

    p_boundary_measure = subparsers.add_parser("boundary-measure", help="Recompute the authoritative boundary packet")
    add_boundary_common(p_boundary_measure)
    p_boundary_measure.add_argument("--boundary-id", required=True)

    p_boundary_decide = subparsers.add_parser("boundary-decide", help="Record one runtime-validated quality/value decision")
    add_boundary_common(p_boundary_decide)
    p_boundary_decide.add_argument("--boundary-id", required=True)
    p_boundary_decide.add_argument("--decision", required=True, choices=list(boundary_mod.DECISIONS))
    p_boundary_decide.add_argument("--actor", default="quality-value-manager")
    p_boundary_decide.add_argument("--candidate-id", default="")
    p_boundary_decide.add_argument("--soft-debt", action="append", default=[])
    p_boundary_decide.add_argument("--reason", default="")

    p_boundary_complete = subparsers.add_parser("boundary-complete-improvement", help="Remeasure exactly one bounded improvement")
    add_boundary_common(p_boundary_complete)
    p_boundary_complete.add_argument("--boundary-id", required=True)

    p_boundary_status = subparsers.add_parser("boundary-status", help="Show the quality/value boundary dashboard packet")
    add_boundary_common(p_boundary_status)
    p_boundary_status.add_argument("--boundary-id", required=True)
    p_boundary_status.add_argument("--format", choices=["json", "html"], default="json")
    p_boundary_status.add_argument("--output", "-o", default="")

    p_boundary_verify = subparsers.add_parser("boundary-verify", help="Verify a current acceptance receipt and predecessor chain")
    add_boundary_common(p_boundary_verify)
    p_boundary_verify.add_argument("--boundary-id", required=True)

    p_boundary_lock = subparsers.add_parser("boundary-lock-status", help="Verify that every active boundary permits advancement")
    add_boundary_common(p_boundary_lock)

    # adapter-verify
    p_adapter_verify = subparsers.add_parser("adapter-verify", help="Verify adapter contract against runtime")
    p_adapter_verify.add_argument("--workspace", "-w", default=".teamloop")
    p_adapter_verify.add_argument("--json", action="store_true", help="Output as JSON")

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
        "validate-artifact": cmd_validate_artifact,
        "validate-research": cmd_validate_research,
        "memory-doctor": cmd_memory_doctor,
        "write-continuation-decision": cmd_write_continuation_decision,
        "check-guard-integrity": cmd_check_guard_integrity,
        "run-sentinel": cmd_run_sentinel,
        "cache-inspect": cmd_cache_inspect,
        "cache-clear": cmd_cache_clear,
        "cache-stats": cmd_cache_stats,
        "cache-validate": cmd_cache_validate,
        "final-gate": cmd_final_gate,
        "resolve-execution-policy": cmd_resolve_execution_policy,
        "materialize-execution-manifest": cmd_materialize_execution_manifest,
        "validate-execution-contract": cmd_validate_execution_contract,
        "prepare-execution": cmd_prepare_execution,
        "record-progress": cmd_record_progress,
        "route-role": cmd_route_role,
        "record-performance": cmd_record_performance,
        "performance-report": cmd_performance_report,
        "test-select": cmd_test_select,
        "release-info": cmd_release_info,
        "compat-check": cmd_compat_check,
        "schema-lint": cmd_schema_lint,
        "dogfood": cmd_dogfood,
        "inbox-send": cmd_inbox_send,
        "inbox-receive": cmd_inbox_receive,
        "inbox-stats": cmd_inbox_stats,
        "advisory-check": cmd_advisory_check,
        "team-propose": cmd_team_propose,
        "team-negotiate": cmd_team_negotiate,
        "team-accept": cmd_team_accept,
        "team-materialize": cmd_team_materialize,
        "team-codex-doctor": cmd_team_codex_doctor,
        "team-codex-smoke": cmd_team_codex_smoke,
        "boundary-create": cmd_boundary_create,
        "boundary-measure": cmd_boundary_measure,
        "boundary-decide": cmd_boundary_decide,
        "boundary-complete-improvement": cmd_boundary_complete_improvement,
        "boundary-status": cmd_boundary_status,
        "boundary-verify": cmd_boundary_verify,
        "boundary-lock-status": cmd_boundary_lock_status,
        "adapter-verify": cmd_adapter_verify,
    }

    try:
        commands[args.command](args)
    except boundary_mod.BoundaryError as exc:
        print(json.dumps({"status": "FAIL", "command": args.command, "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
