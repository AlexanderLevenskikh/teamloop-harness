#!/usr/bin/env python3
"""YourAITeam — deterministic team composition, negotiation, and backend materialization.

This module deliberately does not call an LLM. It turns a task description and explicit
constraints into an auditable proposal. A primary agent may translate natural-language
bargaining into these constraints, but the resulting team/budget calculation is deterministic.
"""
from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA_VERSION = 1
PRODUCT = "YourAITeam"
GRADES = ("economy", "balanced", "premium")
GRADE_FACTOR = {"economy": 0.68, "balanced": 1.0, "premium": 1.42}
GRADE_EFFORT = {"economy": "low", "balanced": "medium", "premium": "high"}
GRADE_STEPS = {"economy": 5, "balanced": 9, "premium": 14}

# Prices are planning units expressed as expected model tokens, not provider billing.
ROLE_CATALOG: Dict[str, Dict[str, Any]] = {
    "delivery-manager": {
        "title": "Delivery Manager",
        "baseTokens": 4200,
        "mandatory": True,
        "mutates": False,
        "sandbox": "workspace-write",
        "summary": "Owns the goal, budget, stopping decision, trade-offs, and final acceptance.",
    },
    "explorer": {
        "title": "Code Explorer",
        "baseTokens": 4200,
        "mutates": False,
        "sandbox": "read-only",
        "summary": "Maps relevant code and returns a compact evidence summary.",
    },
    "researcher": {
        "title": "Researcher",
        "baseTokens": 7200,
        "mutates": False,
        "sandbox": "read-only",
        "summary": "Resolves material external or architectural unknowns.",
    },
    "research-reviewer": {
        "title": "Research Reviewer",
        "baseTokens": 4700,
        "mutates": False,
        "sandbox": "read-only",
        "summary": "Challenges evidence quality and unsupported conclusions.",
    },
    "vibe-coder": {
        "title": "Vibe Coder",
        "baseTokens": 7600,
        "mutates": True,
        "sandbox": "workspace-write",
        "summary": "Builds a low-risk prototype quickly with bounded polish.",
    },
    "implementer": {
        "title": "Implementer",
        "baseTokens": 11200,
        "mutates": True,
        "sandbox": "workspace-write",
        "summary": "Implements a bounded change and supplies execution evidence.",
    },
    "senior-engineer": {
        "title": "Senior Engineer",
        "baseTokens": 16800,
        "mutates": True,
        "sandbox": "workspace-write",
        "summary": "Handles ambiguous, cross-cutting, or high-risk implementation.",
    },
    "architect": {
        "title": "Architect",
        "baseTokens": 8800,
        "mutates": False,
        "sandbox": "read-only",
        "summary": "Defines boundaries and trade-offs before expensive mutation.",
    },
    "reviewer": {
        "title": "Change Reviewer",
        "baseTokens": 6400,
        "mutates": False,
        "sandbox": "read-only",
        "summary": "Reviews correctness, scope, regressions, and missing evidence.",
    },
    "verifier": {
        "title": "Verifier",
        "baseTokens": 4800,
        "mutates": False,
        "sandbox": "workspace-write",
        "summary": "Runs targeted gates and reports what they actually prove.",
    },
    "security-reviewer": {
        "title": "Security Reviewer",
        "baseTokens": 9000,
        "mutates": False,
        "sandbox": "read-only",
        "summary": "Assesses security boundaries and abuse cases.",
    },
    "writer": {
        "title": "Technical Writer",
        "baseTokens": 6200,
        "mutates": True,
        "sandbox": "workspace-write",
        "summary": "Produces concise user-facing or engineering documentation.",
    },
    "visual-checker": {
        "title": "Visual Checker",
        "baseTokens": 4300,
        "mutates": False,
        "sandbox": "read-only",
        "summary": "Checks a user-facing result for obvious visual and interaction defects.",
    },
}

ALIASES = {
    "manager": "delivery-manager", "менеджер": "delivery-manager",
    "researcher": "researcher", "исследователь": "researcher",
    "reviewer": "reviewer", "ревьюер": "reviewer", "ревью": "reviewer",
    "developer": "implementer", "разработчик": "implementer", "executor": "implementer",
    "senior": "senior-engineer", "архитектор": "architect", "architect": "architect",
    "tester": "verifier", "тестировщик": "verifier", "verifier": "verifier",
    "explorer": "explorer", "аналитик": "explorer",
    "vibecoder": "vibe-coder", "вайбкодер": "vibe-coder",
    "writer": "writer", "писатель": "writer",
    "security": "security-reviewer", "безопасник": "security-reviewer",
}

KIND_PATTERNS: Sequence[Tuple[str, Sequence[str]]] = (
    ("security", ("security", "vulnerability", "auth", "permission", "secret", "безопас", "уязвим", "авторизац", "аутентиф")),
    ("dependency", ("dependency", "dependencies", "package-lock", "yarn.lock", "npm audit", "nexus", "зависимост")),
    ("research", ("research", "investigate", "compare approaches", "изучить", "исслед", "разобраться", "проанализировать рынок")),
    ("review", ("review", "audit code", "провести аудит", "проверить pr", "код-ревью", "ревью")),
    ("documentation", ("documentation", "readme", "guide", "docs", "документац", "руководство", "статья")),
    ("prototype", ("landing", "landing page", "визитк", "лендинг", "prototype", "прототип", "simple site", "простой сайт")),
    # Intent beats tool name: "fix a flaky Playwright test" is a bugfix, not a migration.
    ("bugfix", ("bug", "fix", "flaky", "error", "broken", "ошиб", "баг", "почин", "падает", "флейк")),
    ("migration", ("migrat", "codemod", "перенест", "миграц", "мигрир", "переписать весь", "upgrade framework", "selenium to playwright", "selenium на playwright")),
    ("implementation", ("implement", "feature", "add ", "build ", "сделать", "добавить", "реализовать")),
)


def _utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(data: Any) -> str:
    return hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return value[:48] or "task"


def classify_task(description: str) -> Dict[str, Any]:
    text = description.lower()
    kind = "implementation"
    matched: List[str] = []
    for candidate, patterns in KIND_PATTERNS:
        hits = [p for p in patterns if p in text]
        if hits:
            kind = candidate
            matched = hits
            break
    high_risk_terms = ("production", "payment", "billing", "database", "schema", "security", "auth", "critical", "breaking", "прод", "платеж", "база", "критич", "безопас")
    medium_risk_terms = ("migration", "dependency", "upgrade", "refactor", "ci", "release", "миграц", "зависим", "рефактор", "релиз")
    risk = "high" if any(t in text for t in high_risk_terms) or kind == "security" else "medium" if any(t in text for t in medium_risk_terms) or kind in ("migration", "dependency") else "low"
    complexity = "high" if len(description) > 700 or any(t in text for t in ("across repositories", "cross-cutting", "architecture", "ecosystem", "несколько реп", "архитектур", "экосистем")) else "medium" if len(description) > 220 or kind in ("migration", "security", "dependency") else "low"
    uncertainty = "high" if kind == "research" or any(t in text for t in ("unknown", "not sure", "не знаю", "непонят", "гипотез")) else "medium" if any(t in text for t in ("investigate", "analyse", "analyze", "разобраться", "проверить гипотез")) else "low"
    mutating = kind not in ("research", "review")
    return {
        "kind": kind,
        "risk": risk,
        "complexity": complexity,
        "uncertainty": uncertainty,
        "mutating": mutating,
        "matchedSignals": matched,
    }


def _role(role_id: str, grade: str = "balanced", *, required: bool = True, reason: str = "", engagement: str = "full") -> Dict[str, Any]:
    meta = ROLE_CATALOG[role_id]
    factor = GRADE_FACTOR[grade] * (0.62 if engagement == "final-only" else 1.0)
    return {
        "roleId": role_id,
        "title": meta["title"],
        "grade": grade,
        "engagement": engagement,
        "required": required,
        "reason": reason or meta["summary"],
        "expectedTokens": int(round(meta["baseTokens"] * factor / 100.0) * 100),
        "reasoningEffort": GRADE_EFFORT[grade],
        "maxSteps": max(3, int(round(GRADE_STEPS[grade] * (0.65 if engagement == "final-only" else 1.0)))),
        "sandbox": meta["sandbox"],
        "mutatesWorkspace": meta["mutates"],
    }


def _initial_team(analysis: Dict[str, Any], preference: str) -> List[Dict[str, Any]]:
    kind, risk, complexity = analysis["kind"], analysis["risk"], analysis["complexity"]
    manager_grade = "premium" if risk == "high" else "balanced"
    team = [_role("delivery-manager", manager_grade, reason="Owns the global result and may reject misleading local metrics.")]
    if kind == "prototype":
        team.append(_role("vibe-coder", "economy" if preference == "cost" else "balanced", reason="A low-risk prototype does not justify a full engineering department."))
        if preference != "cost":
            team.append(_role("visual-checker", "economy", required=False, reason="Cheap final visual sanity check.", engagement="final-only"))
    elif kind == "research":
        team.append(_role("researcher", "balanced", reason="The deliverable is knowledge, so no developer is hired."))
        if analysis["uncertainty"] == "high" and preference != "cost":
            team.append(_role("research-reviewer", "balanced", required=False, engagement="final-only"))
    elif kind == "review":
        team.append(_role("reviewer", "balanced" if risk != "high" else "premium", reason="The task is evaluation, not implementation."))
    elif kind == "documentation":
        team.append(_role("writer", "balanced"))
        if analysis["uncertainty"] != "low":
            team.append(_role("researcher", "economy", required=False, engagement="final-only", reason="Fact-checks only material unknowns."))
    elif kind == "bugfix":
        team.extend([
            _role("explorer", "economy", required=False, reason="Finds the real execution path without polluting the main context."),
            _role("implementer", "balanced" if risk != "high" else "premium"),
            _role("verifier", "balanced", engagement="final-only"),
        ])
        if risk != "low" or preference == "quality":
            team.append(_role("reviewer", "balanced", required=False, engagement="final-only"))
    elif kind == "dependency":
        team.extend([
            _role("researcher", "balanced", reason="Interprets registry, vulnerability, and compatibility evidence."),
            _role("implementer", "balanced"),
            _role("verifier", "balanced", engagement="final-only"),
        ])
        if risk == "high":
            team.append(_role("reviewer", "premium", required=False, engagement="final-only"))
    elif kind == "migration":
        team.extend([
            _role("explorer", "balanced"),
            _role("architect", "balanced"),
            _role("senior-engineer", "premium" if risk == "high" else "balanced"),
            _role("reviewer", "balanced", engagement="final-only"),
            _role("verifier", "balanced", engagement="final-only"),
        ])
        if analysis["uncertainty"] == "high":
            team.insert(2, _role("researcher", "balanced", required=False))
    elif kind == "security":
        team.extend([
            _role("explorer", "balanced"),
            _role("security-reviewer", "premium"),
            _role("senior-engineer", "premium"),
            _role("verifier", "premium", engagement="final-only"),
        ])
    else:
        if complexity == "high":
            team.extend([_role("architect", "balanced"), _role("senior-engineer", "premium" if risk == "high" else "balanced"), _role("reviewer", "balanced", engagement="final-only"), _role("verifier", "balanced", engagement="final-only")])
        else:
            team.extend([_role("implementer", "economy" if preference == "cost" and risk == "low" else "balanced"), _role("verifier", "economy" if risk == "low" else "balanced", engagement="final-only")])
            if risk != "low" or preference == "quality":
                team.append(_role("reviewer", "balanced", required=False, engagement="final-only"))
    return team


def _coordination_overhead(role_count: int) -> float:
    if role_count <= 2:
        return 0.08
    if role_count == 3:
        return 0.16
    if role_count == 4:
        return 0.25
    if role_count == 5:
        return 0.34
    return 0.45 + (role_count - 6) * 0.04


def _budget(team: List[Dict[str, Any]]) -> Dict[str, Any]:
    direct = sum(r["expectedTokens"] for r in team)
    overhead_rate = _coordination_overhead(len(team))
    overhead = int(round(direct * overhead_rate / 100.0) * 100)
    expected = direct + overhead
    return {
        "directRoleTokens": direct,
        "coordinationOverheadTokens": overhead,
        "coordinationOverheadRate": round(overhead_rate, 2),
        "expectedTokens": expected,
        "rangeTokens": {"min": int(expected * 0.72), "max": int(expected * 1.45)},
        "roleCount": len(team),
        "maxConcurrentWorkers": max(1, min(3, len(team) - 1)),
        "currency": "estimated-model-tokens",
        "billingDisclaimer": "Planning estimate only; exact provider billing and cached-token behavior are backend-specific.",
    }


def _find_role(team: List[Dict[str, Any]], role_id: str) -> Optional[Dict[str, Any]]:
    return next((r for r in team if r["roleId"] == role_id), None)


def _reprice(role: Dict[str, Any], grade: str) -> None:
    fresh = _role(role["roleId"], grade, required=role["required"], reason=role["reason"], engagement=role["engagement"])
    role.update(fresh)


def _optimize(team: List[Dict[str, Any]], max_tokens: Optional[int], max_roles: Optional[int], accept_risk: bool) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    team = copy.deepcopy(team)
    tradeoffs: List[Dict[str, Any]] = []
    optional_order = ["visual-checker", "research-reviewer", "reviewer", "explorer", "researcher", "architect", "verifier"]
    # First downgrade non-manager roles.
    if max_tokens:
        for role in sorted(team, key=lambda r: r["expectedTokens"], reverse=True):
            if _budget(team)["expectedTokens"] <= max_tokens:
                break
            if role["roleId"] == "delivery-manager" or role["grade"] == "economy":
                continue
            old = role["grade"]
            new = "balanced" if old == "premium" else "economy"
            _reprice(role, new)
            tradeoffs.append({"action": "downgrade", "roleId": role["roleId"], "from": old, "to": new, "risk": "Less depth and fewer edge cases."})
    # Then remove optional roles / roles explicitly safe to merge.
    while (max_roles and len(team) > max_roles) or (max_tokens and _budget(team)["expectedTokens"] > max_tokens):
        candidate = next((_find_role(team, rid) for rid in optional_order if _find_role(team, rid) and (not _find_role(team, rid)["required"] or accept_risk)), None)
        if candidate is None:
            break
        team.remove(candidate)
        tradeoffs.append({"action": "remove", "roleId": candidate["roleId"], "risk": f"Coverage of '{candidate['title']}' is merged into the manager or left residual."})
    return team, tradeoffs


def propose(description: str, *, backend: str = "portable", max_tokens: Optional[int] = None, max_roles: Optional[int] = None, preference: str = "balanced", risk_tolerance: str = "medium", accept_risk: bool = False) -> Dict[str, Any]:
    if not description.strip():
        raise ValueError("task description is required")
    if backend not in ("portable", "codex", "opencode"):
        raise ValueError("backend must be portable, codex, or opencode")
    if preference not in ("cost", "balanced", "quality", "speed"):
        raise ValueError("preference must be cost, balanced, quality, or speed")
    analysis = classify_task(description)
    team = _initial_team(analysis, preference)
    team, tradeoffs = _optimize(team, max_tokens, max_roles, accept_risk)
    budget = _budget(team)
    status = "PROPOSED"
    unmet: List[str] = []
    if max_tokens and budget["expectedTokens"] > max_tokens:
        status = "BUDGET_UNSATISFIED"
        unmet.append(f"Expected {budget['expectedTokens']} tokens exceeds max {max_tokens} without removing required coverage.")
    if max_roles and len(team) > max_roles:
        status = "BUDGET_UNSATISFIED"
        unmet.append(f"Team has {len(team)} roles; maxRoleCount is {max_roles}.")
    proposal_core = {
        "schemaVersion": SCHEMA_VERSION,
        "product": PRODUCT,
        "createdAtUtc": _utc(),
        "status": status,
        "task": {"description": description, "fingerprint": fingerprint(description)},
        "analysis": analysis,
        "constraints": {"backend": backend, "maxTokens": max_tokens, "maxRoleCount": max_roles, "preference": preference, "riskTolerance": risk_tolerance, "acceptRisk": accept_risk},
        "team": team,
        "budget": budget,
        "tradeoffs": tradeoffs,
        "unmetConstraints": unmet,
        "residualRisks": [t["risk"] for t in tradeoffs] + (["The budget is below the cheapest team that preserves mandatory coverage."] if unmet else []),
        "acceptance": {"accepted": False, "acceptedAtUtc": None},
        "negotiationHistory": [],
    }
    proposal_core["proposalId"] = "team-" + fingerprint({"task": description, "team": team, "constraints": proposal_core["constraints"]})[:12]
    proposal_core["fingerprint"] = fingerprint({k: v for k, v in proposal_core.items() if k not in ("fingerprint", "createdAtUtc")})
    return proposal_core


def _resolve_alias(value: str) -> str:
    key = value.strip().lower().replace("_", "-")
    return ALIASES.get(key, key)


def parse_bargain_request(text: str) -> Dict[str, Any]:
    low = text.lower()
    changes: Dict[str, Any] = {"remove": [], "downgrade": [], "upgrade": []}
    m = re.search(r"(?:не больше|максимум|влез(?:ть|и)? в|max)\s*([0-9][0-9 _]*)\s*([кk])?\s*(?:токен|token)", low)
    if m:
        n = int(re.sub(r"\D", "", m.group(1)))
        changes["maxTokens"] = n * (1000 if m.group(2) else 1)
    m = re.search(r"(?:не больше|максимум|max)\s*(\d+)\s*(?:рол|role|агент)", low)
    if m:
        changes["maxRoleCount"] = int(m.group(1))
    for alias, role_id in ALIASES.items():
        if re.search(rf"(?:убери|без|remove|drop)\s+(?:роль\s+)?{re.escape(alias)}", low):
            changes["remove"].append(role_id)
        if re.search(rf"(?:удешеви|дешевле|downgrade)\s+(?:роль\s+)?{re.escape(alias)}", low):
            changes["downgrade"].append(role_id)
        if re.search(rf"(?:усиль|дороже|upgrade)\s+(?:роль\s+)?{re.escape(alias)}", low):
            changes["upgrade"].append(role_id)
    if any(x in low for x in ("дешевле", "эконом", "минимум цены", "cheaper", "cost")):
        changes["preference"] = "cost"
    if any(x in low for x in ("качество", "надежн", "quality")):
        changes["preference"] = "quality"
    if any(x in low for x in ("быстрее", "speed")):
        changes["preference"] = "speed"
    if any(x in low for x in ("принимаю риск", "accept risk", "готов рискнуть")):
        changes["acceptRisk"] = True
    if "ревью" in low and any(x in low for x in ("только финал", "только в конце", "final only")):
        changes["finalOnlyReviewer"] = True
    return changes


def negotiate(proposal: Dict[str, Any], request: str = "", **explicit: Any) -> Dict[str, Any]:
    changes = parse_bargain_request(request) if request else {"remove": [], "downgrade": [], "upgrade": []}
    for key, value in explicit.items():
        if value not in (None, [], ""):
            changes[key] = value
    constraints = copy.deepcopy(proposal.get("constraints", {}))
    for key in ("maxTokens", "maxRoleCount", "preference", "riskTolerance", "acceptRisk"):
        if key in changes:
            constraints[key] = changes[key]
    result = propose(
        proposal["task"]["description"], backend=constraints.get("backend", "portable"),
        max_tokens=constraints.get("maxTokens"), max_roles=constraints.get("maxRoleCount"),
        preference=constraints.get("preference", "balanced"), risk_tolerance=constraints.get("riskTolerance", "medium"),
        accept_risk=bool(constraints.get("acceptRisk", False)),
    )
    team = result["team"]
    manual_tradeoffs: List[Dict[str, Any]] = []
    for role_id in changes.get("remove", []):
        role = _find_role(team, role_id)
        if not role:
            continue
        if role_id == "delivery-manager":
            manual_tradeoffs.append({"action": "refused", "roleId": role_id, "risk": "The manager is the owner of the global result and cannot be removed in MVP."})
            continue
        if role["required"] and not constraints.get("acceptRisk"):
            manual_tradeoffs.append({"action": "refused", "roleId": role_id, "risk": "Required coverage can only be removed with explicit acceptRisk."})
            continue
        team.remove(role)
        manual_tradeoffs.append({"action": "remove", "roleId": role_id, "risk": f"User accepted loss of {role['title']} coverage."})
    for role_id in changes.get("downgrade", []):
        role = _find_role(team, role_id)
        if role and role["grade"] != "economy":
            old = role["grade"]
            _reprice(role, "balanced" if old == "premium" else "economy")
            manual_tradeoffs.append({"action": "downgrade", "roleId": role_id, "from": old, "to": role["grade"], "risk": "Less reasoning depth."})
    for role_id in changes.get("upgrade", []):
        role = _find_role(team, role_id)
        if role and role["grade"] != "premium":
            old = role["grade"]
            _reprice(role, "premium")
            manual_tradeoffs.append({"action": "upgrade", "roleId": role_id, "from": old, "to": "premium", "risk": "Higher expected cost."})
    if changes.get("finalOnlyReviewer"):
        role = _find_role(team, "reviewer")
        if role:
            role.update(_role("reviewer", role["grade"], required=role["required"], reason=role["reason"], engagement="final-only"))
    result["budget"] = _budget(team)
    result["tradeoffs"].extend(manual_tradeoffs)
    result["residualRisks"] = [t["risk"] for t in result["tradeoffs"]]
    result["negotiationHistory"] = copy.deepcopy(proposal.get("negotiationHistory", [])) + [{"atUtc": _utc(), "request": request, "changes": changes, "previousProposalId": proposal.get("proposalId")}]
    result["fingerprint"] = fingerprint({k: v for k, v in result.items() if k not in ("fingerprint", "createdAtUtc")})
    return result


def accept(proposal: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(proposal)
    if result.get("status") == "BUDGET_UNSATISFIED":
        raise ValueError("cannot accept a proposal with unmet budget constraints")
    result["status"] = "ACCEPTED"
    result["acceptance"] = {"accepted": True, "acceptedAtUtc": _utc(), "acceptedFingerprint": proposal.get("fingerprint")}
    result["fingerprint"] = fingerprint({k: v for k, v in result.items() if k not in ("fingerprint", "createdAtUtc")})
    return result


def _role_prompt(role: Dict[str, Any], proposal: Dict[str, Any]) -> str:
    return f"""# {role['title']}\n\nYou are hired for one bounded engagement in {PRODUCT}.\n\nTask: {proposal['task']['description']}\n\nYour responsibility: {role['reason']}\n\nContract:\n- Stay inside this responsibility; do not invent adjacent work.\n- Expected engagement: {role['engagement']}.\n- Stop after at most {role['maxSteps']} agentic steps and return a concise evidence summary.\n- The Delivery Manager owns trade-offs and final acceptance.\n- A local metric is evidence, not the goal. Do not hide, suppress, or relabel problems merely to improve a score.\n- State residual uncertainty and unfinished work explicitly.\n"""


def _toml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def materialize_codex(proposal: Dict[str, Any], output: str) -> Dict[str, Any]:
    root = Path(output)
    agents_dir = root / ".codex" / "agents"
    skill_dir = root / ".agents" / "skills" / "your-ai-team"
    agents_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.mkdir(parents=True, exist_ok=True)
    worker_roles = [r for r in proposal["team"] if r["roleId"] != "delivery-manager"]
    config = "[agents]\nmax_threads = %d\nmax_depth = 1\n\n" % max(1, proposal["budget"]["maxConcurrentWorkers"])
    (root / ".codex" / "config.toml").parent.mkdir(parents=True, exist_ok=True)
    (root / ".codex" / "config.toml").write_text(config, encoding="utf-8")
    for role in proposal["team"]:
        model = "gpt-5.6-terra" if role["grade"] == "economy" else "gpt-5.6"
        content = "\n".join([
            f"name = {_toml_quote(role['roleId'].replace('-', '_'))}",
            f"description = {_toml_quote(role['reason'])}",
            f"model = {_toml_quote(model)}",
            f"model_reasoning_effort = {_toml_quote(role['reasoningEffort'])}",
            f"sandbox_mode = {_toml_quote(role['sandbox'])}",
            "developer_instructions = \"\"\"",
            _role_prompt(role, proposal).replace('"""', "'''").rstrip(),
            "\"\"\"",
            "",
        ])
        (agents_dir / f"{role['roleId']}.toml").write_text(content, encoding="utf-8")
    role_names = ", ".join(r["roleId"].replace("-", "_") for r in worker_roles) or "no subagents"
    skill = f"""---\nname: your-ai-team\ndescription: Propose, negotiate, accept, and run the minimum sufficient AI team for a task under an explicit token budget. Use before delegating work to multiple Codex agents.\n---\n\n1. Before spawning any subagent, run `bash scripts/your-ai-team.sh propose --backend codex --task \"<task>\"` or inspect an accepted proposal supplied by the user.\n2. Show the user role composition, expected token range, coordination overhead, residual risks, and at least one cheaper trade-off.\n3. Negotiate until the user explicitly accepts. Never interpret silence as acceptance.\n4. After acceptance, use only the accepted roles: {role_names}.\n5. Keep `max_depth = 1`; do not allow recursive hiring.\n6. The delivery manager owns the result and stopping decision. Metrics are evidence, not the target.\n7. Do not spawn roles that are absent from the accepted contract.\n"""
    (skill_dir / "SKILL.md").write_text(skill, encoding="utf-8")
    (root / "your-ai-team-contract.json").write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"backend": "codex", "output": str(root), "files": [str(p.relative_to(root)) for p in sorted(root.rglob("*")) if p.is_file()]}


def materialize_opencode(proposal: Dict[str, Any], output: str) -> Dict[str, Any]:
    root = Path(output)
    agents_dir = root / ".opencode" / "agents"
    commands_dir = root / ".opencode" / "commands"
    agents_dir.mkdir(parents=True, exist_ok=True)
    commands_dir.mkdir(parents=True, exist_ok=True)
    agents: Dict[str, Any] = {}
    worker_roles = [r for r in proposal["team"] if r["roleId"] != "delivery-manager"]
    allowed_tasks = {"*": "deny"}
    for role in worker_roles:
        allowed_tasks[role["roleId"]] = "allow"
    for role in proposal["team"]:
        mode = "primary" if role["roleId"] == "delivery-manager" else "subagent"
        permission = {
            "edit": "allow" if role["mutatesWorkspace"] else "deny",
            "bash": "allow" if role["sandbox"] == "workspace-write" else "ask",
        }
        if role["roleId"] == "delivery-manager":
            permission["task"] = allowed_tasks
        agents[role["roleId"]] = {
            "description": role["reason"], "mode": mode,
            "prompt": f"{{file:./.opencode/agents/{role['roleId']}.md}}",
            "steps": role["maxSteps"], "permission": permission,
        }
        front = ["---", f"description: {role['reason']}", f"mode: {mode}", f"steps: {role['maxSteps']}", "permission:", f"  edit: {'allow' if role['mutatesWorkspace'] else 'deny'}", f"  bash: {'allow' if role['sandbox'] == 'workspace-write' else 'ask'}"]
        if role["roleId"] == "delivery-manager":
            front.extend(["  task:", '    "*": deny'] + [f"    {r['roleId']}: allow" for r in worker_roles])
        front.extend(["---", "", _role_prompt(role, proposal)])
        (agents_dir / f"{role['roleId']}.md").write_text("\n".join(front), encoding="utf-8")
    config = {
        "$schema": "https://opencode.ai/config.json",
        "default_agent": "delivery-manager",
        "agent": agents,
        "command": {
            "your-ai-team": {
                "description": "Negotiate the minimum sufficient AI team before execution",
                "agent": "delivery-manager",
                "subtask": False,
                "template": "Use the accepted YourAITeam contract in @your-ai-team-contract.json. Do not invoke any unlisted role. Requested task or negotiation: $ARGUMENTS",
            }
        },
    }
    (root / "opencode.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    command = """---\ndescription: Propose or negotiate a YourAITeam contract\nagent: delivery-manager\nsubtask: false\n---\n\nDo not start implementation until the user explicitly accepts a team proposal. Show budget, coordination overhead, removed coverage, and residual risk.\n\n$ARGUMENTS\n"""
    (commands_dir / "your-ai-team.md").write_text(command, encoding="utf-8")
    (root / "your-ai-team-contract.json").write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"backend": "opencode", "output": str(root), "files": [str(p.relative_to(root)) for p in sorted(root.rglob("*")) if p.is_file()]}


def materialize(proposal: Dict[str, Any], backend: str, output: str) -> Dict[str, Any]:
    if proposal.get("status") != "ACCEPTED":
        raise ValueError("proposal must be ACCEPTED before materialization")
    if backend == "codex":
        return materialize_codex(proposal, output)
    if backend == "opencode":
        return materialize_opencode(proposal, output)
    raise ValueError("backend must be codex or opencode")


def load(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save(path: str, data: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
