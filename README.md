# TeamLoop Harness

Reusable delivery harness for supervised agent teams.

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

All scripts have `.sh` and `.ps1` variants. See [TEAMLOOP.md](TEAMLOOP.md) for full workflow.

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
  runs/                       # Per-run artifacts
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

## Memory

Structured lessons stored under `.teamloop/memory/`. Categories: lessons, antipatterns, decisions, evidence. Use `memory-doctor` to validate. See [TEAMLOOP.md](TEAMLOOP.md) for details.

## Continuation Decisions

Terminal transitions auto-write `.teamloop/state/continuation-decision.json` with one of: `DONE`, `SAFE_CHECKPOINT`, `CONTINUE`, `HUMAN_DECISION_REQUIRED`, `BLOCKED`. Use `write-continuation-decision` to set manually.

## Sentinel and Guard Integrity

- **Sentinel** (`run-sentinel`) — 9 check categories covering scope bypass, gate weakening, test suppression, protected files, state mutation, evidence manipulation, and docs drift. Runs before DONE.
- **Guard Integrity** (`check-guard-integrity`) — Protected paths, dangerous operations, enforcement levels, schema integrity.

## Final Gate

A campaign-ready final gate is the 4-command chain:

```bash
bash scripts/run-sentinel.sh --workspace .teamloop
bash scripts/check-guard-integrity.sh --workspace .teamloop
bash scripts/memory-doctor.sh --workspace .teamloop
bash scripts/validate-state.sh --workspace .teamloop
```

All four must pass before DONE.

## The Team Loop

```
discover → plan → execute → review → research → slice → gate
  → repair → sentinel → guard check → memory validate → continue
```

See [TESTING.md](TESTING.md) for validation checklist and maturity ladder.

## OpenCode Integration

`adapters/opencode/` contains source templates (agents, commands, config). When a project is initialized, these are copied to `.opencode/` as active project-local configuration. Use `/supervised-task` to start a supervised delivery run. See [TEAMLOOP.md](TEAMLOOP.md) for full details.
