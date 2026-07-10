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

### Initialize workspace

```powershell
.\scripts\init-workspace.ps1 -Workspace ".teamloop" -Profile "generic-software-task"
```

Or with bash:

```bash
bash scripts/init-workspace.sh --workspace ".teamloop" --profile "generic-software-task"
```

### Validate workspace

```powershell
.\scripts\validate-state.ps1 -Workspace ".teamloop"
```

### Determine next action

```powershell
.\scripts\next-action.ps1 -Workspace ".teamloop"
```

### Check scope

```powershell
.\scripts\check-scope.ps1 -Workspace ".teamloop"
```

### Run gates

```powershell
.\scripts\run-gates.ps1 -Workspace ".teamloop"
```

## Workspace Structure

```
.teamloop/
  state/
    team-state.json        # Current team state
    backlog.jsonl           # Task backlog
    events.jsonl            # Append-only event ledger
    run-ledger.jsonl        # Run history
    decisions.jsonl         # Decision log
    blockers.jsonl          # Human-required blockers
    current-task.json       # Active task
  runs/
    run-001/                # Per-run artifacts
  research/
    research-001.md         # Research reports
  policies/
    scope-policy.json       # Scope guard rules
    gate-policy.json        # Gate execution rules
    role-policy.json        # Role configuration
  profiles/
    active-profile.json     # Active domain profile
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
| `check-scope` | Validate file changes against scope |
| `run-gates` | Execute gate policy checks |
| `validate-state` | Validate all state files |

## Profiles

Profiles define domain-specific behavior: discovery questions, gate commands, scope rules, task slicing strategy, and role overrides.

Available: `generic-software-task`

## OpenCode Adapter

For OpenCode integration, see `adapters/opencode/`. Use `/supervised-task` to start a supervised delivery run.

## The Team Loop

```
discover → plan → execute → review → research → slice → gate → repair → continue
```
