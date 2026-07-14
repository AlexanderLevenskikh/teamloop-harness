#!/usr/bin/env python3
"""
YourAITeam — Schema Evolution Audit Module

Audits all schemas in schemas/ for:
  - additionalProperties:false occurrences (top-level and nested)
  - JSON validity
  - JSON Schema draft-07 conformance
  - Cross-schema consistency (required fields, type mismatches)

Provides structured reports for CLI consumption.
"""
import json
import os
import re
import sys


# ---------------------------------------------------------------------------
# Schema Evolution Rules
# ---------------------------------------------------------------------------
#
# These rules govern how schemas may evolve between schema versions.
#
# RULE 1: additionalProperties:false is a contract-breaking constraint.
#   — Removing it is ADDITIVE (compatible): validators stop rejecting
#     previously-unknown fields. Existing artifacts still validate.
#   — Adding it is BREAKING: artifacts with previously-allowed extra
#     fields now fail validation.
#   — Recommendation: future-facing schemas (profiles, policies, memory)
#     should omit additionalProperties:false. Internal/runtime schemas
#     (team-state, task, event) may keep it for strictness.
#
# RULE 2: Adding a new optional property is ADDITIVE and compatible,
#   provided additionalProperties is not false (or the new property is
#   added alongside removing additionalProperties:false).
#
# RULE 3: Adding a new required property is BREAKING unless the property
#   has a sensible default. All existing artifacts must be updated.
#
# RULE 4: Changing an enum (adding values) is ADDITIVE. Removing enum
#   values is BREAKING — existing artifacts may fail validation.
#
# RULE 5: Changing a type (string→integer, etc.) is BREAKING.
#
# RULE 6: Removing a property from "properties" is ADDITIVE only if
#   additionalProperties:false is NOT set (orphan data is silently ignored).
#   With additionalProperties:false, removing a property means artifacts
#   still carrying it now fail.
#
# RULE 7: Removing a required field from "required" is ADDITIVE.
#   Adding to "required" is BREAKING (see RULE 3).
#
# RULE 8: Schema files must remain valid JSON and valid JSON Schema
#   draft-07. No structural drift is permitted.
#
# RULE 9: Cross-schema consistency: if Schema A references a field whose
#   type is defined in Schema B, both must agree. E.g., if both schemas
#   define "status" as an enum, the enum values should not silently diverge
#   without an explicit migration plan.
#
# ---------------------------------------------------------------------------

# Minimum set of top-level keys that constitute a valid JSON Schema draft-07
_MINIMAL_SCHEMA_KEYS = {"$schema", "type"}


def load_all_schemas(schemas_dir):
    """Load all .schema.json files from the given directory.

    Returns:
        dict[str, dict]: mapping of filename → parsed JSON object.
        Raises ValueError if any file is not valid JSON.
    """
    schemas = {}
    if not os.path.isdir(schemas_dir):
        raise ValueError(f"Schemas directory not found: {schemas_dir}")

    for entry in sorted(os.listdir(schemas_dir)):
        if not entry.endswith(".schema.json"):
            continue
        path = os.path.join(schemas_dir, entry)
        try:
            with open(path, "r", encoding="utf-8") as f:
                schemas[entry] = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON in schema file {entry}: {exc}"
            ) from exc
    return schemas


def _find_additional_properties_occurrences(obj, path="$"):
    """Recursively find all additionalProperties keys in a schema object.

    Returns:
        list[dict]: each item has keys 'path', 'value', 'line' (None for now).
    """
    occurrences = []
    if isinstance(obj, dict):
        if "additionalProperties" in obj:
            occurrences.append({
                "path": path,
                "value": obj["additionalProperties"],
            })
        for key, value in obj.items():
            occurrences.extend(
                _find_additional_properties_occurrences(value, f"{path}.{key}")
            )
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            occurrences.extend(
                _find_additional_properties_occurrences(item, f"{path}[{i}]")
            )
    return occurrences


def audit_additional_properties(schemas_dir):
    """Audit all schemas for additionalProperties:false occurrences.

    Returns:
        dict with:
          total_schemas: int
          schemas_with_strict: int — schemas that have additionalProperties:false
          occurrences: list of {schema, path, is_top_level}
          schemas: list of {filename, top_level_strict, nested_count}
    """
    schemas = load_all_schemas(schemas_dir)
    occurrences = []
    per_schema = []

    for filename, schema_obj in schemas.items():
        occs = _find_additional_properties_occurrences(schema_obj)
        top_level_strict = False
        nested_count = 0
        for occ in occs:
            if occ["value"] is False:
                is_top = occ["path"] == "$"
                if is_top:
                    top_level_strict = True
                else:
                    nested_count += 1
                occurrences.append({
                    "schema": filename,
                    "path": occ["path"],
                    "is_top_level": is_top,
                })
        per_schema.append({
            "filename": filename,
            "top_level_strict": top_level_strict,
            "nested_strict_count": nested_count,
            "total_strict_count": sum(
                1 for o in occs if o["value"] is False
            ),
        })

    schemas_with_strict = sum(
        1 for s in per_schema
        if s["top_level_strict"] or s["nested_strict_count"] > 0
    )

    return {
        "total_schemas": len(schemas),
        "schemas_with_strict": schemas_with_strict,
        "total_additional_properties_false": len(occurrences),
        "schemas": per_schema,
        "occurrences": occurrences,
    }


def lint_schema(schema_path):
    """Validate a single schema file.

    Checks:
      1. Valid JSON
      2. Has $schema key pointing to draft-07
      3. Has 'type' key
      4. Has 'properties' key (for object schemas)
      5. 'required' array entries exist in 'properties'

    Returns:
        dict with:
          valid: bool
          filename: str
          issues: list of {check, severity, detail}
    """
    issues = []
    filename = os.path.basename(schema_path)

    # Check 1: valid JSON
    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            schema_obj = json.load(f)
    except json.JSONDecodeError as exc:
        return {
            "valid": False,
            "filename": filename,
            "issues": [{
                "check": "valid-json",
                "severity": "ERROR",
                "detail": f"Invalid JSON: {exc}",
            }],
        }
    except OSError as exc:
        return {
            "valid": False,
            "filename": filename,
            "issues": [{
                "check": "file-access",
                "severity": "ERROR",
                "detail": f"Cannot read file: {exc}",
            }],
        }

    if not isinstance(schema_obj, dict):
        issues.append({
            "check": "schema-root-type",
            "severity": "ERROR",
            "detail": "Schema root must be a JSON object",
        })
        return {
            "valid": False,
            "filename": filename,
            "issues": issues,
        }

    # Check 2: $schema key
    schema_url = schema_obj.get("$schema", "")
    if "draft-07" not in schema_url:
        issues.append({
            "check": "draft-07-meta",
            "severity": "ERROR",
            "detail": f"$schema must reference draft-07, got: {schema_url!r}",
        })

    # Check 3: 'type' key
    if "type" not in schema_obj:
        issues.append({
            "check": "has-type",
            "severity": "ERROR",
            "detail": "Schema must have a 'type' key",
        })

    # Check 4: 'properties' for object schemas
    if schema_obj.get("type") == "object" and "properties" not in schema_obj:
        issues.append({
            "check": "has-properties",
            "severity": "WARNING",
            "detail": "Object schema has no 'properties' definition",
        })

    # Check 5: required fields exist in properties
    required_fields = schema_obj.get("required", [])
    defined_props = set(schema_obj.get("properties", {}).keys())
    missing_required = [
        field for field in required_fields
        if field not in defined_props
    ]
    if missing_required:
        issues.append({
            "check": "required-fields-defined",
            "severity": "ERROR",
            "detail": (
                f"Required fields not defined in properties: "
                f"{', '.join(missing_required)}"
            ),
        })

    return {
        "valid": not any(i["severity"] == "ERROR" for i in issues),
        "filename": filename,
        "issues": issues,
    }


def lint_all_schemas(schemas_dir):
    """Lint all schema files in the directory.

    Returns:
        dict with:
          total: int
          valid: int
          invalid: int
          results: list of lint_schema results
    """
    results = []
    if not os.path.isdir(schemas_dir):
        return {
            "total": 0,
            "valid": 0,
            "invalid": 0,
            "results": [],
            "error": f"Schemas directory not found: {schemas_dir}",
        }

    for entry in sorted(os.listdir(schemas_dir)):
        if not entry.endswith(".schema.json"):
            continue
        path = os.path.join(schemas_dir, entry)
        results.append(lint_schema(path))

    valid_count = sum(1 for r in results if r["valid"])
    return {
        "total": len(results),
        "valid": valid_count,
        "invalid": len(results) - valid_count,
        "results": results,
    }


def check_cross_schema_consistency(schemas_dir):
    """Check for cross-schema consistency issues.

    Looks for:
      - Same field name defined with different types across schemas
      - Same enum field name with different allowed values
      - Required field referenced in one schema but not defined in another

    Returns:
        dict with:
          issues: list of {check, severity, detail, schemas}
    """
    schemas = load_all_schemas(schemas_dir)
    issues = []

    # Build a map of field → (type, schema) for top-level properties
    field_types = {}  # field_name → list of (schema, type_def)
    enum_fields = {}  # field_name → list of (schema, enum_values)

    for filename, schema_obj in schemas.items():
        props = schema_obj.get("properties", {})
        for prop_name, prop_def in props.items():
            if isinstance(prop_def, dict):
                prop_type = prop_def.get("type")
                if prop_type:
                    field_types.setdefault(prop_name, []).append(
                        (filename, prop_type)
                    )

                # Check enum fields
                if "enum" in prop_def:
                    enum_fields.setdefault(prop_name, []).append(
                        (filename, sorted(prop_def["enum"]))
                    )

    # Check type consistency for shared field names
    for field_name, type_list in field_types.items():
        if len(type_list) < 2:
            continue
        types_seen = set(t for _, t in type_list)
        if len(types_seen) > 1:
            schemas_involved = [f"{s}:{t}" for s, t in type_list]
            issues.append({
                "check": "cross-schema-type-mismatch",
                "severity": "WARNING",
                "detail": (
                    f"Field '{field_name}' has different types: "
                    f"{', '.join(schemas_involved)}"
                ),
                "schemas": [s for s, _ in type_list],
            })

    # Check enum consistency for shared enum field names
    for field_name, enum_list in enum_fields.items():
        if len(enum_list) < 2:
            continue
        # Only flag if one enum is NOT a superset of another
        first_values = enum_list[0][1]
        for schema_name, values in enum_list[1:]:
            if values != first_values:
                issues.append({
                    "check": "cross-schema-enum-drift",
                    "severity": "WARNING",
                    "detail": (
                        f"Enum field '{field_name}' has different values "
                        f"across schemas"
                    ),
                    "schemas": [s for s, _ in enum_list],
                })

    return {
        "total_checks": len(field_types) + len(enum_fields),
        "issues": issues,
    }


def run_full_audit(schemas_dir):
    """Run the complete schema audit: lint + additionalProperties + consistency.

    Returns a structured report dict.
    """
    # Lint runs first — it handles per-file JSON errors gracefully.
    lint_report = lint_all_schemas(schemas_dir)

    # additionalProperties audit and cross-schema consistency require
    # all schemas to load. If any schema has invalid JSON, these phases
    # are reported as skipped — the lint errors already surface the issue.
    try:
        ap_audit = audit_additional_properties(schemas_dir)
    except ValueError as exc:
        ap_audit = {
            "total_schemas": 0,
            "schemas_with_strict": 0,
            "total_additional_properties_false": 0,
            "schemas": [],
            "occurrences": [],
            "error": str(exc),
        }

    try:
        consistency = check_cross_schema_consistency(schemas_dir)
    except ValueError as exc:
        consistency = {
            "total_checks": 0,
            "issues": [{
                "check": "cross-schema-consistency",
                "severity": "ERROR",
                "detail": f"Could not load schemas: {exc}",
            }],
        }

    # Combine all issues for overall pass/fail
    # Only lint ERRORS cause FAIL. Cross-schema consistency issues are
    # WARNING-level by design (different schemas can legitimately use the
    # same field name with different types or enums).
    all_errors = []
    for r in lint_report.get("results", []):
        for issue in r.get("issues", []):
            if issue["severity"] == "ERROR":
                all_errors.append({
                    "schema": r["filename"],
                    **issue,
                })
    # Consistency issues are WARNING-level — they do not cause FAIL.
    # They appear in the report for awareness only.

    has_errors = len(all_errors) > 0

    return {
        "status": "FAIL" if has_errors else "PASS",
        "lint": lint_report,
        "additional_properties": ap_audit,
        "cross_schema_consistency": consistency,
        "evolution_recommendations": _generate_recommendations(
            ap_audit, consistency
        ),
    }


def _generate_recommendations(ap_audit, consistency):
    """Generate evolution recommendations based on audit findings."""
    recommendations = []

    total = ap_audit["total_schemas"]
    strict = ap_audit["schemas_with_strict"]
    if strict == total and total > 0:
        recommendations.append({
            "severity": "INFO",
            "recommendation": (
                f"All {total} schemas use additionalProperties:false. "
                f"Consider removing from future-facing schemas "
                f"(profiles, policies, memory) to allow safe extension."
            ),
        })

    # Count strictly nested
    nested_total = sum(
        s["nested_strict_count"] for s in ap_audit.get("schemas", [])
    )
    if nested_total > 0:
        recommendations.append({
            "severity": "INFO",
            "recommendation": (
                f"{nested_total} nested additionalProperties:false constraints "
                f"found across schemas. Nested strictness is generally safe "
                f"for internal structures."
            ),
        })

    # Consistency issues
    for issue in consistency.get("issues", []):
        recommendations.append({
            "severity": issue["severity"],
            "recommendation": issue["detail"],
        })

    if not recommendations:
        recommendations.append({
            "severity": "INFO",
            "recommendation": "No evolution concerns detected.",
        })

    return recommendations


def format_text_report(report):
    """Format a full audit report as human-readable text."""
    lines = []
    lines.append("Schema Evolution Audit")
    lines.append("=" * 22)
    lines.append("")

    # Lint summary
    lint = report.get("lint", {})
    lines.append(f"Lint: {lint.get('valid', 0)}/{lint.get('total', 0)} schemas valid")
    if lint.get("invalid", 0) > 0:
        for r in lint.get("results", []):
            if not r["valid"]:
                for issue in r.get("issues", []):
                    lines.append(
                        f"  [{issue['severity']}] {r['filename']}: "
                        f"{issue['detail']}"
                    )
    lines.append("")

    # additionalProperties summary
    ap = report.get("additional_properties", {})
    lines.append(
        f"Schemas with additionalProperties:false: "
        f"{ap.get('schemas_with_strict', 0)}/{ap.get('total_schemas', 0)}"
    )
    lines.append(
        f"  Total occurrences (including nested): "
        f"{ap.get('total_additional_properties_false', 0)}"
    )
    lines.append("")

    # Per-schema breakdown
    lines.append("Per-schema breakdown:")
    for s in ap.get("schemas", []):
        flag = ""
        if s["top_level_strict"]:
            flag = " [STRICT]"
        nested = ""
        if s["nested_strict_count"] > 0:
            nested = f" +{s['nested_strict_count']} nested"
        lines.append(f"  {s['filename']}{flag}{nested}")
    lines.append("")

    # Cross-schema consistency
    cs = report.get("cross_schema_consistency", {})
    if cs.get("issues"):
        lines.append("Cross-schema consistency issues:")
        for issue in cs["issues"]:
            lines.append(
                f"  [{issue['severity']}] {issue['detail']}"
            )
        lines.append("")

    # Recommendations
    lines.append("Evolution Recommendations:")
    for rec in report.get("evolution_recommendations", []):
        lines.append(f"  [{rec['severity']}] {rec['recommendation']}")
    lines.append("")

    # Overall status
    lines.append(f"Overall: {report['status']}")

    return "\n".join(lines)


def format_json_report(report):
    """Format report as JSON."""
    return json.dumps(report, ensure_ascii=False, indent=2)
