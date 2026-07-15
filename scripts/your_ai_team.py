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
    "quality-value-manager": {
        "title": "Quality/Value Boundary Manager",
        "baseTokens": 3200,
        "mandatory": True,
        "mutates": False,
        "sandbox": "read-only",
        "summary": "Chooses accept, improve, split, stop, or human escalation from a deterministic boundary packet.",
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
    "quality-manager": "quality-value-manager", "quality manager": "quality-value-manager", "quality value manager": "quality-value-manager", "boundary-manager": "quality-value-manager", "boundary manager": "quality-value-manager", "менеджер-границы": "quality-value-manager",
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
    if analysis.get("mutating"):
        team.append(_role(
            "quality-value-manager",
            "economy" if risk == "low" else "balanced",
            required=True,
            engagement="final-only",
            reason="Runs once per authoritative metrics fingerprint and cannot waive hard gates.",
        ))
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
            if role["roleId"] in ("delivery-manager", "quality-value-manager") or role["grade"] == "economy":
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
        if role_id in ("delivery-manager", "quality-value-manager"):
            manual_tradeoffs.append({"action": "refused", "roleId": role_id, "risk": "The delivery and quality/value managers are runtime boundary owners and cannot be removed."})
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
    common = f"""# {role['title']}

You are hired for one bounded engagement in {PRODUCT}.

Task: {proposal['task']['description']}

Your responsibility: {role['reason']}

Contract:
- Stay inside this responsibility; do not invent adjacent work.
- Expected engagement: {role['engagement']}.
- Stop after at most {role['maxSteps']} agentic steps and return a concise evidence summary.
- The Delivery Manager owns trade-offs and final acceptance.
- A local metric is evidence, not the goal. Do not hide, suppress, or relabel problems merely to improve a score.
- State residual uncertainty and unfinished work explicitly.
"""
    if role["roleId"] == "quality-value-manager":
        common += """
Boundary constraints:
- Read only the current deterministic packet through boundary-status.
- Hard invariant failures can never be accepted or reclassified as soft debt.
- Choose exactly one closed runtime decision.
- Prefer the highest-payoff reusable root fix over leaf symptoms.
- Invoke only boundary-status and boundary-decide; never edit code, metrics, evidence, policy, budget, history, or receipts.
- Your decision is not acceptance. The runtime must validate it and write the receipt.
- Never launch subagents.
- Never troubleshoot shell, WSL, cache, or repository paths and never create temporary scripts. If boundary-status cannot return a valid authoritative packet, return BOUNDARY_PACKET_UNAVAILABLE to the orchestrator.
"""
    return common


def _toml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


CODEX_CHATGPT_GRADE_MODELS = {
    "economy": "gpt-5.6-luna",
    "balanced": "gpt-5.6-terra",
    "premium": "gpt-5.6-sol",
}
CODEX_NICKNAMES = {
    "delivery-manager": ["Navigator", "Harbor", "Conductor"],
    "quality-value-manager": ["Balance", "Ledger", "Compass"],
    "explorer": ["Scout", "Maple", "Trace"],
    "researcher": ["Curie", "Sagan", "Turing"],
    "research-reviewer": ["Skeptic", "Verifier", "Delta"],
    "vibe-coder": ["Spark", "Pixel", "Tempo"],
    "implementer": ["Builder", "Forge", "Patch"],
    "senior-engineer": ["Atlas", "Keystone", "Anchor"],
    "architect": ["Blueprint", "Arch", "Frame"],
    "reviewer": ["Sentinel", "Lens", "Prism"],
    "verifier": ["Proof", "Check", "Gauge"],
    "security-reviewer": ["Shield", "Aegis", "Vault"],
    "writer": ["Schrodinger", "Quill", "Draft"],
    "visual-checker": ["Canvas", "Focus", "Iris"],
}

CODEX_GUIDANCE_BEGIN = "<!-- YOUR_AI_TEAM_CODEX_BEGIN -->"
CODEX_GUIDANCE_END = "<!-- YOUR_AI_TEAM_CODEX_END -->"



def _codex_root_guidance(proposal: Dict[str, Any]) -> str:
    accepted_roles = ", ".join(role["roleId"].replace("-", "_") for role in proposal["team"])
    fingerprint = proposal.get("acceptance", {}).get("acceptedFingerprint") or proposal.get("fingerprint") or "UNKNOWN"
    return f"""{CODEX_GUIDANCE_BEGIN}
## YourAITeam Codex execution contract

- When the user invokes `$your-ai-team` or asks to use the accepted team, the root Codex thread acts as the Delivery Manager.
- Read `your-ai-team-contract.json`; use only accepted roles: {accepted_roles}.
- Do not spawn agents before explicit acceptance. Keep subagent depth at 1 and wait for required threads.
- A failed thread is failed work, not completion. If an agent model is unsupported, run `scripts/codex-doctor` with `--fix-models inherit`, restart Codex, and retry once.
- Use deterministic `.teamloop` routing, gates, quality/value boundary receipts, sentinel, and final gate before claiming completion.
- `SAFE_CHECKPOINT` is not automatically `DONE`; ticket closure or a manager decision alone is not accepted user value.
- Accepted contract fingerprint: `{fingerprint}`.
{CODEX_GUIDANCE_END}"""


def _merge_codex_agents_guidance(path: Path, proposal: Dict[str, Any]) -> None:
    """Append or replace a small managed Codex block without deleting project guidance."""
    guidance = _codex_root_guidance(proposal)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    pattern = re.compile(
        re.escape(CODEX_GUIDANCE_BEGIN) + r".*?" + re.escape(CODEX_GUIDANCE_END),
        re.DOTALL,
    )
    if pattern.search(existing):
        merged = pattern.sub(guidance, existing)
    else:
        merged = existing.rstrip()
        if merged:
            merged += "\n\n"
        merged += guidance
    path.write_text(merged.rstrip() + "\n", encoding="utf-8")


def _merge_codex_config(path: Path, *, max_threads: int, max_depth: int = 1) -> None:
    """Merge bounded [agents] settings without overwriting unrelated project config."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            f"[agents]\nmax_threads = {max_threads}\nmax_depth = {max_depth}\n",
            encoding="utf-8",
        )
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    section_start = None
    section_end = len(lines)
    for index, line in enumerate(lines):
        match = re.match(r"^\s*\[([^]]+)\]\s*$", line)
        if not match:
            continue
        if match.group(1).strip() == "agents":
            section_start = index
            continue
        if section_start is not None and index > section_start:
            section_end = index
            break
    if section_start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["[agents]", f"max_threads = {max_threads}", f"max_depth = {max_depth}"])
    else:
        found_threads = False
        found_depth = False
        updated = []
        for index, line in enumerate(lines):
            if section_start < index < section_end and re.match(r"^\s*max_threads\s*=", line):
                updated.append(f"max_threads = {max_threads}")
                found_threads = True
            elif section_start < index < section_end and re.match(r"^\s*max_depth\s*=", line):
                updated.append(f"max_depth = {max_depth}")
                found_depth = True
            else:
                updated.append(line)
        additions = []
        if not found_threads:
            additions.append(f"max_threads = {max_threads}")
        if not found_depth:
            additions.append(f"max_depth = {max_depth}")
        if additions:
            updated[section_end:section_end] = additions
        lines = updated
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _codex_model(role: Dict[str, Any], model_mode: str, overrides: Optional[Dict[str, str]]) -> Optional[str]:
    overrides = overrides or {}
    if role["grade"] in overrides and overrides[role["grade"]]:
        return overrides[role["grade"]]
    if model_mode == "inherit":
        return None
    if model_mode == "chatgpt":
        return CODEX_CHATGPT_GRADE_MODELS[role["grade"]]
    if model_mode == "explicit":
        raise ValueError(f"explicit Codex model mode requires a model for grade {role['grade']}")
    raise ValueError("codex model mode must be inherit, chatgpt, or explicit")


def _codex_skill(proposal: Dict[str, Any], role_names: str) -> str:
    return f"""---
name: your-ai-team
description: Propose, negotiate, accept, and execute the minimum sufficient Codex agent team under an explicit budget and YourAITeam runtime gates. Use before multi-agent delegation or when resuming an accepted team contract.
---

# YourAITeam for Codex

You are the root Delivery Manager. Do not spawn a subagent before an explicit accepted contract exists.

## Proposal and negotiation

Use the platform-native wrapper for the current shell:

- PowerShell: `.\\scripts\\your-ai-team.ps1 propose --backend codex --task \"<task>\" --output .teamloop\\team\\proposal.json`
- Bash/WSL: `bash scripts/your-ai-team.sh propose --backend codex --task \"<task>\" --output .teamloop/team/proposal.json`

Show roles, grades, expected token range, coordination overhead, removed coverage, residual risks, and one cheaper alternative. Translate user bargaining through `negotiate`. Accept only after an explicit yes.

## Materialization

Materialize into the repository root, not under `.teamloop/generated`:

- PowerShell: `.\\scripts\\your-ai-team.ps1 materialize --proposal .teamloop\\team\\accepted.json --backend codex --output-dir . --codex-model-mode inherit`
- Bash/WSL: `bash scripts/your-ai-team.sh materialize --proposal .teamloop/team/accepted.json --backend codex --output-dir . --codex-model-mode inherit`

`inherit` is the compatibility default: child agents inherit a model supported by the active Codex account. Use `chatgpt` only when the account supports the generated Sol/Terra/Luna pins.

## Execution contract

1. Read `your-ai-team-contract.json` and use only accepted roles: {role_names}.
2. Keep subagent depth at 1. Never allow recursive hiring.
3. Run read-heavy independent work in parallel only when it saves time; serialize write-heavy roles.
4. Wait for every required role and collect a concise evidence summary. A failed agent thread is not completed work.
5. If a custom agent reports an unsupported model, do not investigate WSL, paths, or temporary scripts. Run `codex-doctor --fix-models inherit`, restart the Codex task, and retry once.
6. Before implementation, initialize or resume `.teamloop`, then use deterministic `next-action` and runtime routing rather than inventing lifecycle transitions.
7. After deterministic gates pass, obey the quality/value boundary lock. The quality-value-manager may read `boundary-status` and record one decision; it cannot edit code, policy, metrics, evidence, history, or receipts.
8. Before claiming completion, run current validation, sentinel when required, boundary verification, and `final-gate`. Report PASS, FAIL, SKIP, and NOT_REQUIRED honestly.
9. `SAFE_CHECKPOINT` is not automatically `DONE`; stopped or budget-exhausted work must retain visible limitations.
10. Never treat role invocation, ticket closure, generated files, or a manager decision JSON as accepted user value.
"""


def materialize_codex(
    proposal: Dict[str, Any],
    output: str,
    *,
    model_mode: str = "inherit",
    model_overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    root = Path(output)
    agents_dir = root / ".codex" / "agents"
    skill_dir = root / ".agents" / "skills" / "your-ai-team"
    agents_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.mkdir(parents=True, exist_ok=True)
    worker_roles = [r for r in proposal["team"] if r["roleId"] != "delivery-manager"]
    _merge_codex_config(
        root / ".codex" / "config.toml",
        max_threads=max(1, proposal["budget"]["maxConcurrentWorkers"]),
        max_depth=1,
    )
    _merge_codex_agents_guidance(root / "AGENTS.md", proposal)
    manifest_agents = []
    for role in proposal["team"]:
        model = _codex_model(role, model_mode, model_overrides)
        agent_name = role["roleId"].replace("-", "_")
        lines = [
            f"name = {_toml_quote(agent_name)}",
            f"description = {_toml_quote(role['reason'])}",
        ]
        if model:
            lines.append(f"model = {_toml_quote(model)}")
        lines.extend([
            f"model_reasoning_effort = {_toml_quote(role['reasoningEffort'])}",
            f"sandbox_mode = {_toml_quote(role['sandbox'])}",
            f"nickname_candidates = {_toml_quote(CODEX_NICKNAMES.get(role['roleId'], [role['title']]))}",
            "developer_instructions = \"\"\"",
            _role_prompt(role, proposal).replace('"""', "'''").rstrip(),
            "\"\"\"",
            "",
        ])
        (agents_dir / f"{role['roleId']}.toml").write_text("\n".join(lines), encoding="utf-8")
        manifest_agents.append({
            "roleId": role["roleId"],
            "agentName": agent_name,
            "grade": role["grade"],
            "model": model or "INHERIT",
            "reasoningEffort": role["reasoningEffort"],
            "sandbox": role["sandbox"],
        })
    role_names = ", ".join(r["roleId"].replace("-", "_") for r in worker_roles) or "no subagents"
    (skill_dir / "SKILL.md").write_text(_codex_skill(proposal, role_names), encoding="utf-8")
    (root / "your-ai-team-contract.json").write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    materialization = {
        "schemaVersion": 1,
        "backend": "codex",
        "contractFingerprint": proposal.get("fingerprint"),
        "acceptedFingerprint": proposal.get("acceptance", {}).get("acceptedFingerprint"),
        "modelMode": model_mode,
        "modelPolicy": "inherit active Codex model" if model_mode == "inherit" else (model_overrides or CODEX_CHATGPT_GRADE_MODELS),
        "maxThreads": max(1, proposal["budget"]["maxConcurrentWorkers"]),
        "maxDepth": 1,
        "agents": manifest_agents,
        "rootDeliveryManager": True,
        "rootGuidance": {"file": "AGENTS.md", "managedBlock": "YOUR_AI_TEAM_CODEX"},
        "runtimeAuthority": "YourAITeam deterministic gates, boundary receipts, and final-gate",
    }
    (root / "your-ai-team-codex.json").write_text(json.dumps(materialization, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    setup = """# Codex setup

1. Review the managed YourAITeam block appended to `AGENTS.md`. Existing project guidance is preserved.
2. Trust the repository in Codex so project `.codex/` configuration and `AGENTS.md` are loaded.
3. Start a new Codex task from this repository root.
4. Run `python scripts/codex_support.py --project-root .` or the platform wrapper.
5. Invoke `$your-ai-team` and use `your-ai-team-contract.json`.
6. For the cheapest live check, run `scripts/codex-smoke` (or its PowerShell wrapper).

If a model pin is unsupported for ChatGPT authentication, run:

```text
python scripts/codex_support.py --project-root . --fix-models inherit
```

Then restart the Codex task because project instructions and agent configuration are loaded at task start.
"""
    (root / "CODEX_SETUP.md").write_text(setup, encoding="utf-8")
    return {
        "backend": "codex",
        "output": str(root),
        "modelMode": model_mode,
        "files": [str(p.relative_to(root)) for p in sorted(root.rglob("*")) if p.is_file()],
    }

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
        if role["roleId"] == "quality-value-manager":
            permission["bash"] = {
                "scripts/boundary-status.sh *": "allow",
                "scripts/boundary-decide.sh *": "allow",
                "*": "deny",
            }
            permission["task"] = {"*": "deny"}
        if role["roleId"] == "delivery-manager":
            permission["task"] = allowed_tasks
        agents[role["roleId"]] = {
            "description": role["reason"], "mode": mode,
            "prompt": f"{{file:./.opencode/agents/{role['roleId']}.md}}",
            "steps": role["maxSteps"], "permission": permission,
        }
        front = ["---", f"description: {role['reason']}", f"mode: {mode}", f"steps: {role['maxSteps']}", "permission:", f"  edit: {'allow' if role['mutatesWorkspace'] else 'deny'}"]
        if role["roleId"] == "quality-value-manager":
            front.extend([
                "  bash:",
                '    "scripts/boundary-status.sh *": allow',
                '    "scripts/boundary-decide.sh *": allow',
                '    "*": deny',
                "  task:",
                '    "*": deny',
            ])
        else:
            front.append(f"  bash: {'allow' if role['sandbox'] == 'workspace-write' else 'ask'}")
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


def materialize(
    proposal: Dict[str, Any],
    backend: str,
    output: str,
    *,
    codex_model_mode: str = "inherit",
    codex_model_overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    if proposal.get("status") != "ACCEPTED":
        raise ValueError("proposal must be ACCEPTED before materialization")
    if backend == "codex":
        return materialize_codex(
            proposal,
            output,
            model_mode=codex_model_mode,
            model_overrides=codex_model_overrides,
        )
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
