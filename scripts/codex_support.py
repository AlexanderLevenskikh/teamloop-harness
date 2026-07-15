#!/usr/bin/env python3
"""Codex integration diagnostics and safe model-pin management for YourAITeam."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

RECOMMENDED_CHATGPT_MODELS = {
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex-spark",
}
CHATGPT_GRADE_MODELS = {
    "economy": "gpt-5.6-luna",
    "balanced": "gpt-5.6-terra",
    "premium": "gpt-5.6-sol",
}
GENERIC_MODEL_RISK = {"gpt-5.6"}
CODEX_GUIDANCE_BEGIN = "<!-- YOUR_AI_TEAM_CODEX_BEGIN -->"
CODEX_GUIDANCE_END = "<!-- YOUR_AI_TEAM_CODEX_END -->"
UNSUPPORTED_MODEL_PATTERNS = (
    "model is not supported",
    "model is unavailable",
    "not supported when using codex with a chatgpt account",
)


def _run(command: List[str], cwd: Path, timeout: int = 15) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return {
            "available": True,
            "exitCode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }
    except FileNotFoundError:
        return {"available": False, "exitCode": None, "stdout": "", "stderr": "command not found"}
    except subprocess.TimeoutExpired as exc:
        return {
            "available": True,
            "exitCode": None,
            "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            "stderr": "timed out",
        }


def _read_toml(path: Path) -> Dict[str, Any]:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def _manifest_path(project_root: Path) -> Path:
    return project_root / "your-ai-team-codex.json"


def _load_manifest(project_root: Path) -> Dict[str, Any]:
    path = _manifest_path(project_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def inspect_codex_project(project_root: str | Path, *, run_cli: bool = True) -> Dict[str, Any]:
    root = Path(project_root).resolve()
    checks: List[Dict[str, Any]] = []
    warnings: List[str] = []
    errors: List[str] = []

    def check(check_id: str, status: str, message: str, **details: Any) -> None:
        record = {"id": check_id, "status": status, "message": message}
        if details:
            record["details"] = details
        checks.append(record)
        if status == "FAIL":
            errors.append(message)
        elif status == "WARN":
            warnings.append(message)

    config_path = root / ".codex" / "config.toml"
    if config_path.exists():
        try:
            config = _read_toml(config_path)
            agents = config.get("agents", {})
            max_depth = agents.get("max_depth")
            max_threads = agents.get("max_threads")
            if max_depth != 1:
                check("config-depth", "FAIL", "Codex max_depth must be 1 for bounded YourAITeam delegation", actual=max_depth)
            else:
                check("config-depth", "PASS", "Codex max_depth is bounded to 1")
            if not isinstance(max_threads, int) or max_threads < 1:
                check("config-threads", "FAIL", "Codex max_threads is missing or invalid", actual=max_threads)
            else:
                check("config-threads", "PASS", "Codex max_threads is configured", actual=max_threads)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            check("config-parse", "FAIL", f"Cannot parse .codex/config.toml: {exc}")
    else:
        check("config-present", "FAIL", ".codex/config.toml is missing")

    agent_dir = root / ".codex" / "agents"
    agent_files = sorted(agent_dir.glob("*.toml")) if agent_dir.is_dir() else []
    if not agent_files:
        check("agents-present", "FAIL", "No project-scoped Codex custom agents were found")
    else:
        check("agents-present", "PASS", f"Found {len(agent_files)} Codex custom agents")

    models: Dict[str, Optional[str]] = {}
    for path in agent_files:
        try:
            data = _read_toml(path)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            check(f"agent:{path.name}", "FAIL", f"Cannot parse {path.relative_to(root)}: {exc}")
            continue
        missing = [key for key in ("name", "description", "developer_instructions") if not data.get(key)]
        if missing:
            check(f"agent:{path.name}", "FAIL", f"Agent {path.name} misses required fields: {', '.join(missing)}")
            continue
        model = data.get("model")
        models[str(data["name"])] = str(model) if model else None
        if model in GENERIC_MODEL_RISK:
            check(
                f"agent-model:{path.name}",
                "WARN",
                f"Agent {data['name']} pins {model}, which has failed on some ChatGPT-account Codex installations; use inherit mode or a Sol/Terra/Luna id",
                model=model,
            )
        elif model and model not in RECOMMENDED_CHATGPT_MODELS:
            check(
                f"agent-model:{path.name}",
                "WARN",
                f"Agent {data['name']} pins a model not in the current ChatGPT compatibility allowlist",
                model=model,
            )
        else:
            check(
                f"agent-model:{path.name}",
                "PASS",
                f"Agent {data['name']} uses {'inherited model selection' if not model else model}",
            )

    skill_path = root / ".agents" / "skills" / "your-ai-team" / "SKILL.md"
    if skill_path.exists():
        skill_text = skill_path.read_text(encoding="utf-8", errors="replace")
        marker_groups = (
            ("accepted contract",),
            ("max_depth", "depth at 1", "depth = 1"),
            ("final-gate",),
            ("boundary",),
            ("only accepted roles", "use only accepted roles", "only roles listed"),
        )
        lowered = skill_text.lower()
        missing_markers = [" | ".join(group) for group in marker_groups if not any(marker in lowered for marker in group)]
        if missing_markers:
            check("skill-contract", "FAIL", "Codex skill is missing lifecycle guidance", missing=missing_markers)
        else:
            check("skill-contract", "PASS", "Codex skill contains accepted-team and lifecycle guidance")
    else:
        check("skill-present", "FAIL", ".agents/skills/your-ai-team/SKILL.md is missing")

    contract_path = root / "your-ai-team-contract.json"
    if contract_path.exists():
        try:
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            if contract.get("status") != "ACCEPTED":
                check("accepted-contract", "FAIL", "Materialized team contract is not ACCEPTED")
            else:
                check("accepted-contract", "PASS", "Accepted team contract is present")
        except (OSError, json.JSONDecodeError) as exc:
            check("accepted-contract", "FAIL", f"Cannot read accepted team contract: {exc}")
    else:
        check("accepted-contract", "FAIL", "your-ai-team-contract.json is missing")

    agents_guidance = root / "AGENTS.md"
    if agents_guidance.exists():
        guidance_text = agents_guidance.read_text(encoding="utf-8", errors="replace")
        if CODEX_GUIDANCE_BEGIN in guidance_text and CODEX_GUIDANCE_END in guidance_text:
            check("root-delivery-guidance", "PASS", "AGENTS.md contains the managed root Delivery Manager contract")
        else:
            check("root-delivery-guidance", "WARN", "AGENTS.md does not contain the managed YourAITeam Codex block")
    else:
        check("root-delivery-guidance", "WARN", "AGENTS.md is missing; invoke the skill explicitly in every Codex task")

    manifest = _load_manifest(root)
    if manifest:
        check("materialization-manifest", "PASS", "Codex materialization manifest is present", modelMode=manifest.get("modelMode"))
    else:
        check("materialization-manifest", "WARN", "your-ai-team-codex.json is missing; model repair and provenance are limited")

    cli_result: Dict[str, Any] = {"available": False}
    login_result: Dict[str, Any] = {"available": False}
    if run_cli:
        codex = shutil.which("codex")
        if not codex:
            check("codex-cli", "WARN", "Codex CLI is not available on PATH; static integration checks still ran")
        else:
            cli_result = _run([codex, "--version"], root)
            if cli_result["exitCode"] == 0:
                check("codex-cli", "PASS", "Codex CLI is available", version=cli_result["stdout"])
            else:
                check("codex-cli", "FAIL", "Codex CLI version check failed", stderr=cli_result["stderr"])
            login_result = _run([codex, "login", "status"], root)
            if login_result["exitCode"] == 0:
                check("codex-login", "PASS", "Codex authentication is active", status=login_result["stdout"])
            else:
                check("codex-login", "WARN", "Codex login status is not healthy", stderr=login_result["stderr"])

    status = "FAIL" if errors else "WARN" if warnings else "PASS"
    return {
        "schemaVersion": 1,
        "status": status,
        "projectRoot": str(root),
        "checks": checks,
        "models": models,
        "cli": cli_result,
        "login": login_result,
        "warnings": warnings,
        "errors": errors,
        "recommendedNextAction": (
            "FIX_CODEX_INTEGRATION"
            if errors
            else "RESTART_CODEX_AND_RUN_READ_ONLY_SMOKE"
            if warnings
            else "RUN_READ_ONLY_CUSTOM_AGENT_SMOKE"
        ),
    }


def _replace_model_line(text: str, model: Optional[str]) -> str:
    lines = text.splitlines()
    output: List[str] = []
    replaced = False
    insert_at = 0
    for index, line in enumerate(lines):
        if re.match(r"^\s*model\s*=", line):
            if model:
                output.append(f'model = {json.dumps(model)}')
            replaced = True
            continue
        output.append(line)
        if re.match(r"^\s*description\s*=", line):
            insert_at = len(output)
    if model and not replaced:
        output.insert(insert_at, f'model = {json.dumps(model)}')
    return "\n".join(output).rstrip() + "\n"


def apply_model_mode(project_root: str | Path, mode: str) -> Dict[str, Any]:
    root = Path(project_root).resolve()
    if mode not in ("inherit", "chatgpt"):
        raise ValueError("mode must be inherit or chatgpt")
    manifest = _load_manifest(root)
    role_grades = {item.get("agentName"): item.get("grade", "balanced") for item in manifest.get("agents", [])}
    changed: List[str] = []
    for path in sorted((root / ".codex" / "agents").glob("*.toml")):
        data = _read_toml(path)
        name = str(data.get("name", path.stem))
        grade = role_grades.get(name, "balanced")
        model = None if mode == "inherit" else CHATGPT_GRADE_MODELS.get(grade, CHATGPT_GRADE_MODELS["balanced"])
        old = path.read_text(encoding="utf-8")
        new = _replace_model_line(old, model)
        if old != new:
            path.write_text(new, encoding="utf-8")
            changed.append(str(path.relative_to(root)))
    if manifest:
        manifest["modelMode"] = mode
        manifest["modelPolicy"] = "inherit parent Codex model" if mode == "inherit" else CHATGPT_GRADE_MODELS
        _manifest_path(root).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "PASS", "mode": mode, "changedFiles": changed}


def _git_status(root: Path) -> Optional[str]:
    if not (root / ".git").exists() or not shutil.which("git"):
        return None
    result = _run(["git", "status", "--porcelain=v1", "--untracked-files=all"], root, timeout=20)
    return result.get("stdout") if result.get("exitCode") == 0 else None


def _select_smoke_role(project_root: Path, requested: str = "") -> str:
    agent_dir = project_root / ".codex" / "agents"
    available: Dict[str, Path] = {}
    for path in sorted(agent_dir.glob("*.toml")):
        try:
            data = _read_toml(path)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        name = str(data.get("name", path.stem))
        if name not in {"delivery_manager", "quality_value_manager"}:
            available[name] = path
    if requested:
        normalized = requested.replace("-", "_")
        if normalized not in available:
            raise ValueError(f"requested Codex smoke role is not materialized: {requested}")
        return normalized
    for preferred in ("writer", "explorer", "researcher", "reviewer", "verifier", "implementer"):
        if preferred in available:
            return preferred
    if available:
        return sorted(available)[0]
    raise ValueError("no accepted worker custom agent is available for Codex smoke")


def run_live_smoke(
    project_root: str | Path,
    *,
    role: str = "",
    timeout: int = 240,
) -> Dict[str, Any]:
    """Run one opt-in, read-only custom-agent smoke through codex exec.

    This is compatibility evidence, not acceptance authority. The deterministic
    YourAITeam runtime still owns gates, boundary receipts, and final success.
    """
    root = Path(project_root).resolve()
    static = inspect_codex_project(root, run_cli=True)
    if static["status"] == "FAIL":
        return {
            "schemaVersion": 1,
            "status": "FAIL",
            "code": "STATIC_INTEGRATION_FAILED",
            "static": static,
            "recommendedNextAction": "FIX_CODEX_INTEGRATION",
        }
    codex = shutil.which("codex")
    if not codex:
        return {
            "schemaVersion": 1,
            "status": "UNAVAILABLE",
            "code": "CODEX_CLI_UNAVAILABLE",
            "recommendedNextAction": "INSTALL_OR_ADD_CODEX_TO_PATH",
        }
    selected_role = _select_smoke_role(root, role)
    before = _git_status(root)
    output_schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL"]},
            "agent": {"type": "string"},
            "thread_id": {"type": "string"},
            "files_changed": {"type": "boolean"},
            "summary": {"type": "string"},
            "error": {"type": "string"},
        },
        "required": ["status", "agent", "thread_id", "files_changed", "summary", "error"],
        "additionalProperties": False,
    }
    prompt = f"""Use $your-ai-team and the accepted contract in this repository.
This is an opt-in compatibility smoke, not implementation work.
Spawn exactly the accepted custom agent `{selected_role}`. Do not spawn any other agent.
Ask it to read README.md (or AGENTS.md when README.md is absent) and return one concrete observation.
Do not edit files and do not run the quality_value_manager. Wait for the custom thread.
Return the requested JSON. Set status PASS only if the custom thread completed successfully.
Set agent to `{selected_role}`, include the real thread id when available, files_changed=false, and error="".
"""
    with tempfile.TemporaryDirectory(prefix="your-ai-team-codex-smoke-") as td:
        schema_path = Path(td) / "schema.json"
        output_path = Path(td) / "result.json"
        schema_path.write_text(json.dumps(output_schema, ensure_ascii=False), encoding="utf-8")
        command = [
            codex, "exec", "--ephemeral", "--sandbox", "read-only", "--json",
            "--output-schema", str(schema_path), "-o", str(output_path), prompt,
        ]
        try:
            proc = subprocess.run(
                command, cwd=str(root), text=True, capture_output=True,
                timeout=timeout, check=False,
            )
            raw = "\n".join(part for part in (proc.stdout, proc.stderr) if part)
        except subprocess.TimeoutExpired as exc:
            return {
                "schemaVersion": 1, "status": "FAIL", "code": "SMOKE_TIMEOUT",
                "role": selected_role, "timeoutSeconds": timeout,
                "recommendedNextAction": "RETRY_ONCE_OR_USE_INTERACTIVE_SMOKE",
            }
        lowered = raw.lower()
        if any(pattern in lowered for pattern in UNSUPPORTED_MODEL_PATTERNS):
            return {
                "schemaVersion": 1,
                "status": "FAIL",
                "code": "UNSUPPORTED_AGENT_MODEL",
                "role": selected_role,
                "exitCode": proc.returncode,
                "diagnostic": raw[-4000:],
                "recommendedNextAction": "RUN_CODEX_DOCTOR_FIX_MODELS_INHERIT_AND_RESTART",
            }
        result: Dict[str, Any] = {}
        if output_path.exists():
            try:
                result = json.loads(output_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                result = {}
        after = _git_status(root)
        repository_changed = before is not None and after is not None and before != after
        passed = (
            proc.returncode == 0
            and result.get("status") == "PASS"
            and str(result.get("agent", "")).replace("-", "_") == selected_role
            and result.get("files_changed") is False
            and not repository_changed
        )
        return {
            "schemaVersion": 1,
            "status": "PASS" if passed else "FAIL",
            "code": "CUSTOM_AGENT_SMOKE_PASSED" if passed else "CUSTOM_AGENT_SMOKE_FAILED",
            "role": selected_role,
            "exitCode": proc.returncode,
            "result": result,
            "repositoryChanged": repository_changed,
            "eventStreamTail": proc.stdout[-4000:],
            "stderrTail": proc.stderr[-2000:],
            "recommendedNextAction": "RUN_CHEAP_BOUNDED_TASK" if passed else "INSPECT_SMOKE_REPORT",
        }


def _print_human(data: Dict[str, Any]) -> None:
    print(f"Codex integration: {data['status']}")
    for item in data["checks"]:
        print(f"[{item['status']}] {item['id']}: {item['message']}")
    print(f"Next action: {data['recommendedNextAction']}")


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and repair YourAITeam Codex integration")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-cli", action="store_true", help="Skip codex binary and login checks")
    parser.add_argument("--fix-models", choices=["inherit", "chatgpt"], default="")
    parser.add_argument("--live-smoke", action="store_true", help="Run one opt-in read-only Codex custom-agent smoke")
    parser.add_argument("--smoke-role", default="", help="Accepted custom agent to use for the live smoke")
    parser.add_argument("--timeout", type=int, default=240, help="Live smoke timeout in seconds")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.fix_models:
        result = apply_model_mode(args.project_root, args.fix_models)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"Updated Codex agent model mode to {args.fix_models}; changed {len(result['changedFiles'])} files")
        return 0
    if args.live_smoke:
        result = run_live_smoke(args.project_root, role=args.smoke_role, timeout=args.timeout)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"Codex live smoke: {result['status']} ({result.get('code', '')})")
            if result.get("diagnostic"):
                print(result["diagnostic"])
            print(f"Next action: {result.get('recommendedNextAction', '')}")
        return 0 if result["status"] == "PASS" else 2 if result["status"] == "UNAVAILABLE" else 1
    data = inspect_codex_project(args.project_root, run_cli=not args.no_cli)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_human(data)
    return 1 if data["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
