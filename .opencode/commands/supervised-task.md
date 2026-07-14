---
description: Start or continue a runtime-bound YourAITeam supervised delivery loop
agent: orchestrator
subtask: false
---

# /supervised-task

Requested task or command arguments:

$ARGUMENTS

Run or continue exactly one bounded YourAITeam action. Runtime commands are the source of truth; prompts consume routing decisions and never make their own verification optional.

## Runtime order

1. Run `bash scripts/next-action.sh --workspace .teamloop`.
2. For `RUN_EXECUTOR`, apply the transition with the returned task id, then run:
   `bash scripts/prepare-execution.sh --workspace .teamloop [--profile fast|standard|audit]`.
   For an existing run, re-run `prepare-execution`; identical inputs must be reported as reused.
3. Run `bash scripts/validate-execution-contract.sh --workspace .teamloop` before dispatch.
4. Execute exactly one bounded role action. Record external role/process timing with `record-performance.sh` when available.
5. After the role action, run `check-scope`, `validate-execution-contract`, and `validate-state`.
6. Run `record-progress`. If it reports `NO_PROGRESS_DETECTED`, do not repeat the same automatic action. Run `next-action` and obey its watchdog/research/blocker routing.
7. Use `route-role --event <event>` for conditional reviewer/watchdog/sentinel decisions. Do not invoke every role unconditionally.
8. Gatekeeper runs `run-gates`; no role writes `gate-result.json` manually.
9. Record continuation decisions only through `write-continuation-decision` or existing runtime transitions.
10. If `scripts/**` or `tests/run-tests.*` changed, run `bash scripts/validate-scripts.sh --root .` before final handoff.
11. Before final handoff, route `final-handoff`, run the required sentinel, inspect its `cacheSummary`, then run `bash scripts/final-gate.sh --workspace .teamloop` (or the matching PowerShell wrapper). A fresh sentinel PASS after `STALE_ENTRY_RECOMPUTED` is authoritative; do not launch an environment-debugging detour merely because an older cache entry failed.
12. Print `performance-report` in the checkpoint report when a trace exists.

## Execution profiles

- `fast`: one executor-like role by default; reviewer/watchdog/pre-final sentinel only on deterministic triggers. Final sentinel and final gate remain mandatory.
- `standard`: executor plus reviewer; watchdog and pre-final sentinel are triggered, not automatic.
- `audit`: executor, reviewer, watchdog, and sentinel are required; all deterministic checks remain enabled.

A requested `fast` profile is escalated to `audit` when protected runtime scope, unresolved high/critical findings, prior no-progress, or other runtime policy requires it.

## Runtime-bound roles

Available bounded roles are `discovery`, `researcher`, `research-lead`, `task-slicer`, `executor`, `change-reviewer`, `gatekeeper`, `watchdog`, and `sentinel`. Runtime policy decides which role is next; prompts never self-select optional verification. Do not hand off unfinished implementation as a vague “developer action”.

## Runtime-owned artifacts

Never directly edit JSON/JSONL files under `.teamloop/state`, execution policy/manifest files, progress history, no-progress result, performance trace, gate results, sentinel reports, or final-gate results. Use runtime writers only. Markdown role notes may be written where the active task permits them.

## No-op behavior

An unchanged run must terminate truthfully. Reused policy/manifest validation plus an unchanged progress signature is not permission to dispatch all roles again. On the configured threshold it becomes `NO_PROGRESS_DETECTED` and routes away from an identical retry.

## Final invariants

- Scope, evidence, runtime-state integrity, required project gates, final sentinel, and final gate cannot be disabled by a profile.
- `MANUAL_REVIEW ≠ HUMAN_REQUIRED`.
- `SAFE_CHECKPOINT ≠ DONE`.
- `RESEARCH_COMPLETE ≠ DONE`.
- `AGENT_SAID_DONE ≠ ACTUALLY_DONE`.
