# YourAITeam

A budget-aware AI-team composer and bounded delivery runtime for Codex and OpenCode.

Canonical public names: **YourAITeam** for the product and `your-ai-team` for repositories, packages, commands, skills, and generated adapter artifacts. See [the 0.4.1 migration note](docs/MIGRATION-0.4.0-TO-0.4.1.md) for the intentionally preserved legacy workspace namespace.

## Core Invariants

```
MANUAL_REVIEW ≠ HUMAN_REQUIRED
SAFE_CHECKPOINT ≠ DONE
RESEARCH_COMPLETE ≠ DONE
```

An agent team must not hand unfinished work back to the user just because a subagent says "developer action" or "manual review". The supervisor routes uncertainty back into research, review, task slicing, execution, or gate repair. Human handoff is only allowed with an explicit classified blocker.

## Quick Start

```powershell
.\scripts\init-workspace.ps1 -Workspace ".teamloop" -Profile "generic-software-task"
.\scripts\next-action.ps1 -Workspace ".teamloop"
```

All scripts have `.sh` and `.ps1` variants. See [RUNTIME.md](RUNTIME.md) for full workflow.

## YourAITeam — task-specific team composition

YourAITeam 0.4.1 provides a front door that proposes the **minimum sufficient AI team** for a task before subagents start spending tokens. The user can negotiate the token ceiling, role grades, engagement, and removed coverage; the accepted contract can then be materialized for Codex or OpenCode.

```bash
bash scripts/your-ai-team.sh propose --backend codex \
  --task "Почини flaky Playwright тест" \
  --output .teamloop/team/proposal.json

bash scripts/your-ai-team.sh negotiate \
  --proposal .teamloop/team/proposal.json \
  --request "Влезь в 25000 токенов, ревьюер только в конце" \
  --output .teamloop/team/proposal-2.json
```

See [YOUR_AI_TEAM.md](YOUR_AI_TEAM.md), the [worked examples](examples/your-ai-team/), and the chapter [«Когда метрикам понадобился менеджер»](docs/book/when-metrics-needed-a-manager.ru.md). Team composition is independent from the existing `fast / standard / audit` safety profiles.

## Workspace Structure

```
.teamloop/
  state/
    team-state.json           # Current team state
    backlog.jsonl             # Task backlog
    current-task.json         # Active task
    events.jsonl              # Append-only event ledger
    run-ledger.jsonl          # Run history
    continuation-decision.json # Terminal state decision
    blockers.jsonl            # Human-required blockers
  memory/                     # Structured memory (lessons, decisions, antipatterns)
  runs/                       # Per-run artifacts, immutable contracts, progress, traces
  research/                   # Research reports
  policies/                   # scope-policy, gate-policy, role-policy
  profiles/                   # active-profile.json
```

## Roles

| Role | Responsibility |
|------|---------------|
| supervisor | Owns work state, routes between roles |
| researcher | Investigates unknowns, produces evidence |
| research-lead | Reviews research quality |
| task-slicer | Creates bounded executable tasks |
| executor | Implements tasks within scope |
| change-reviewer | Reviews diff and task alignment |
| gatekeeper | Runs formal gates |
| watchdog | Diagnoses deterministic no-progress and contradictory runtime behavior |
| sentinel | Performs policy-required read-only integrity inspection |

## Scripts

| Script | Description |
|--------|-------------|
| `init-workspace` | Create `.teamloop/` workspace |
| `write-event` | Append to event ledger |
| `next-action` | Dispatch matrix — determine next action |
| `apply-transition` | Advance state machine |
| `check-scope` | Validate file changes against scope |
| `run-gates` | Execute gate policy checks |
| `validate-state` | Validate all state files |
| `memory-doctor` | Validate memory JSONL files |
| `check-guard-integrity` | Check protected paths and schema integrity |
| `write-continuation-decision` | Write continuation decision records |
| `run-sentinel` | Read-only sentinel integrity inspection |
| `prepare-execution` | Resolve profile, freeze immutable manifest, validate the bounded contract |
| `resolve-execution-policy` | Persist deterministic `fast` / `standard` / `audit` routing policy |
| `materialize-execution-manifest` | Freeze task revision, scope, gates, evidence, and policy fingerprints |
| `validate-execution-contract` | Reject manual mutation and task/scope/profile/policy drift |
| `record-progress` | Record semantic progress and detect blind retry loops |
| `route-role` | Persist deterministic event-driven role routing decisions |
| `record-performance` | Record best-effort runtime performance phases |
| `performance-report` | Print trace totals and deterministic routing comparison |
| `final-gate` | Aggregate state, memory, continuation, scope, gates, sentinel, guard, review, contract, and no-progress checks |

## Fast Execution Contract

`prepare-execution` resolves one of three machine-readable profiles:

- `fast`: one executor-like role by default; review/watchdog are trigger-driven;
- `standard`: executor + reviewer; watchdog is trigger-driven;
- `audit`: executor + reviewer + watchdog + sentinel.

Profiles never disable scope, evidence, required project gates, final sentinel, final gate, or runtime-state integrity. Each run freezes an immutable execution policy/manifest, records semantic progress, blocks identical retries, and emits a best-effort performance trace. See [docs/FAST_EXECUTION.md](docs/FAST_EXECUTION.md).

## Memory

Structured lessons stored under `.teamloop/memory/`. Categories: lessons, antipatterns, decisions, evidence. Use `memory-doctor` to validate. See [RUNTIME.md](RUNTIME.md) for details.

## Continuation Decisions

Terminal transitions auto-write `.teamloop/state/continuation-decision.json` with one of: `DONE`, `SAFE_CHECKPOINT`, `CONTINUE`, `HUMAN_DECISION_REQUIRED`, `BLOCKED`. Use `write-continuation-decision` to set manually.

## Sentinel and Guard Integrity

- **Sentinel** (`run-sentinel`) — 9 check categories covering scope bypass, gate weakening, test suppression, protected files, state mutation, evidence manipulation, and docs drift. Runs before DONE.
- **Guard Integrity** (`check-guard-integrity`) — Protected paths, dangerous operations, enforcement levels, schema integrity.

## Final Gate

Run required pre-handoff inspection, then the real aggregator:

```bash
bash scripts/run-sentinel.sh --workspace .teamloop
bash scripts/check-guard-integrity.sh --workspace .teamloop
bash scripts/memory-doctor.sh --workspace .teamloop
bash scripts/final-gate.sh --workspace .teamloop
```

`final-gate` writes `.teamloop/state/final-gate-result.json` and fails on blocking state, scope, gate, sentinel, reviewed-content, immutable-contract, or unresolved no-progress defects. A missing final sentinel is blocking for optimized runs.

## The Team Loop

```
discover → plan → execute → review → research → slice → gate
  → repair → sentinel → guard check → memory validate → continue
```

See [TESTING.md](TESTING.md) for validation checklist and maturity ladder.

## OpenCode Integration

`adapters/opencode/` contains source templates (agents, commands, config). When a project is initialized, these are copied to `.opencode/` as active project-local configuration. Use `/supervised-task` to start a supervised delivery run. See [RUNTIME.md](RUNTIME.md) for full details.
