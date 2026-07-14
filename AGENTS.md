# YourAITeam Project Instructions

YourAITeam is a file-based runtime for bounded, reviewable AI-agent delivery loops.

## Project structure

- `scripts/teamloop-core.py` — runtime implementation and state-machine commands.
- `scripts/*.sh`, `scripts/*.ps1` — Bash and PowerShell wrappers.
- `schemas/` — contracts for runtime artifacts.
- `templates/workspace/` — initial `.teamloop` workspace template.
- `profiles/` — domain profiles.
- `adapters/opencode/` — source templates for the OpenCode adapter.
- `.opencode/` — active project-local OpenCode agents and commands.
- `tests/` — runtime regression suite.
- `.teamloop/` — active runtime state for the current campaign; create it with `init-workspace`.

## Required verification

Bash / WSL:

```bash
PY=/usr/bin/python3 bash tests/run-tests.sh
```

PowerShell:

```powershell
pwsh -NoProfile -File tests/run-tests.ps1
```

All tests must pass before a checkpoint.

## Runtime protocol

Always determine the next action through:

```bash
bash scripts/next-action.sh --workspace .teamloop
```

Use runtime commands for transitions and evidence:

```bash
bash scripts/apply-transition.sh --workspace .teamloop --action <ACTION> [--task-id <TASK_ID>]
bash scripts/write-event.sh --workspace .teamloop --type <TYPE> --actor <ACTOR> --summary <SUMMARY>
bash scripts/check-scope.sh --workspace .teamloop
bash scripts/run-gates.sh --workspace .teamloop
bash scripts/validate-state.sh --workspace .teamloop
```

Do not manually edit runtime-owned artifacts when a runtime command exists.

Runtime-owned artifacts include:

- `.teamloop/state/team-state.json`
- `.teamloop/state/current-task.json`
- `.teamloop/state/events.jsonl`
- `.teamloop/state/run-ledger.jsonl`
- `.teamloop/runs/*/gate-result.json`

Backlog, research, review, and result artifacts may only be written by the role responsible for them and must follow their schemas/contracts.

## Core invariants

- `SAFE_CHECKPOINT` is not `DONE`.
- `MANUAL_REVIEW` is not `HUMAN_DECISION_REQUIRED`.
- Research completion is not implementation completion.
- Do not weaken schemas, tests, gates, or scope rules merely to make validation pass.
- Do not delete, skip, or suppress tests to obtain a green result.
- Fix runtime producers before broadening schemas.
- Do not invent a handoff to a developer when an agent-executable next action exists.

## Self-hosting safety

This repository may use YourAITeam to improve YourAITeam itself.

- Work on a dedicated Git branch.
- Execute only one bounded iteration at a time.
- Keep every task scope and `allowedWrites` explicit.
- Run the complete test suite after every iteration.
- Treat changes to `scripts/`, `schemas/`, `templates/`, `profiles/`, and agent prompts as high-risk.
- Do not complete a multi-iteration campaign in one unreviewed pass.
- Stop at a truthful checkpoint when the current bounded iteration is green.
