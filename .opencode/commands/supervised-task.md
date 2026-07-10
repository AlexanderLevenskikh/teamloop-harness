---
description: Start or continue a runtime-bound TeamLoop supervised delivery loop
agent: orchestrator
subtask: false
---

# /supervised-task

Requested task or command arguments:

$ARGUMENTS

Run or continue a TeamLoop supervised delivery task.

## Usage

```
/supervised-task              Start or continue the supervised loop
/supervised-task status       Print current state summary
/supervised-task continue     Continue from current phase
/supervised-task research     Trigger research phase
/supervised-task fix-gate     Fix a failed gate
```

## Runtime-Bound Protocol

The agent MUST use runtime scripts as the single source of truth. Do NOT read state files manually to infer what to do.

1. **Determine next step**: Run `bash scripts/next-action.sh --workspace .teamloop`. Treat its JSON output as authoritative.
2. **Enter the returned action**: If the returned action is supported by `apply-transition`, run `bash scripts/apply-transition.sh --workspace .teamloop --action <ACTION> [--task-id <ID>]` before delegating the role. `RUN_EXECUTOR` requires the returned `taskId`.
3. **Delegate exactly one role**: Invoke only the role named by `nextAction`. Let that role complete its bounded responsibility and advance the runtime through its documented command.
4. **Run gates**: Gatekeeper must use `bash scripts/run-gates.sh --workspace .teamloop`. Do not create or edit `gate-result.json` manually.
5. **Validate before checkpoint**: Before any `SAFE_CHECKPOINT` or final handoff, run `bash scripts/validate-state.sh --workspace .teamloop`. If it fails, fix the root cause first.
6. **Write events**: Use `bash scripts/write-event.sh --workspace .teamloop --type ... --actor ... --summary ...`. Do not append to `events.jsonl` manually.

Only edit state files directly when no script exists for the needed operation, and record the reason in an event.

## Available Role Agents

- **discovery**: Initial problem analysis and requirement gathering.
- **researcher**: Technical investigation and solution research.
- **research-lead**: Reviews research artifacts and findings for quality.
- **task-slicer**: Breaks research into bounded executable tasks.
- **executor**: Implements tasks within scope constraints.
- **change-reviewer**: Reviews code changes for scope violations.
- **gatekeeper**: Runs automated gate checks on completed work.

## How It Works

1. Run `bash scripts/next-action.sh --workspace .teamloop` to determine the next step.
2. Apply the returned transition when required; include `--task-id` for `RUN_EXECUTOR`.
3. Route exactly one bounded action to the matching role agent.
4. Let the role use its documented runtime transition on completion.
5. Run `bash scripts/validate-state.sh --workspace .teamloop` before checkpoint or handoff.

## Modes

### `/supervised-task`

Primary entry point. Calls `bash scripts/next-action.sh` and routes to the next role automatically. Does NOT ask the user if there is a clear next action. Only stops for `HUMAN_DECISION_REQUIRED` or `DONE`.

### `/supervised-task status`

Prints current state:
```
Status: <status>
Phase: <currentPhase>
Task: <currentTaskId>
Run: <currentRunId>
Human Required: <humanRequired>
Goal: <goal>
```

### `/supervised-task continue`

Resumes from `SAFE_CHECKPOINT` or any in-progress state. Calls `bash scripts/next-action.sh` to determine where to continue.

### `/supervised-task research`

Forces transition to `NEEDS_RESEARCH` phase using `bash scripts/apply-transition.sh --action RUN_RESEARCHER`. Use when the executor cannot proceed due to unknowns.

### `/supervised-task fix-gate`

Reroutes from `GATE_FAILED` to the executor with gate failure context using `bash scripts/apply-transition.sh --action GATE_FAILED`.

## Critical Constraints

- Must not write `DONE` with failed gates.
- Must not write `DONE` with open tasks.
- Must not transition to `HUMAN_DECISION_REQUIRED` without a blocker record.
- Must not say "no further work" without a `BLOCKED_NO_AGENT_EXECUTABLE_TASKS` verdict from task-slicer or gatekeeper.
- Must not accept "developer action" or "manual review" as final handoff.

## Invariants

```
MANUAL_REVIEW is not HUMAN_REQUIRED.
SAFE_CHECKPOINT is not DONE.
RESEARCH_COMPLETE is not DONE.
```
