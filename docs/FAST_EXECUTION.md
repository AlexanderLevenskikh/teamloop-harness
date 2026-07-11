# Fast Execution Contract

TeamLoopHarness can reduce orchestration overhead without weakening deterministic safety. The optimization is runtime-owned: prompts consume policy decisions, but they cannot decide that their own review, sentinel, scope, evidence, or final gate is optional.

## Lifecycle

```text
next-action
  → prepare-execution
  → one bounded role action
  → validate-state + scope/integrity
  → record-progress
  → route-role only when a deterministic trigger fires
  → required project gates
  → continuation decision
  → required final sentinel
  → final-gate
```

`SAFE_CHECKPOINT ≠ DONE`. A fast profile changes role routing, not completion semantics.

## Execution profiles

| Profile | Default required roles | Conditional roles | Typical selection |
|---|---|---|---|
| `fast` | executor | reviewer, watchdog, pre-final sentinel | P2/low-risk task with no protected scope or unresolved findings |
| `standard` | executor, reviewer | watchdog, pre-final sentinel | P1/medium-risk task |
| `audit` | executor, reviewer, watchdog, sentinel | none | P0/high-risk, protected runtime scope, unresolved high/critical finding, or prior no-progress |

The final sentinel and final gate remain mandatory for every optimized run. An explicit `fast` request is escalated to `audit` when protected paths, unresolved high/critical findings, or prior no-progress make fast routing unsafe.

## Runtime commands

Every command has `.sh` and `.ps1` wrappers.

| Command | Purpose |
|---|---|
| `prepare-execution` | Resolve policy, materialize the immutable manifest, and validate the contract idempotently |
| `resolve-execution-policy` | Resolve and persist `fast` / `standard` / `audit` policy |
| `materialize-execution-manifest` | Freeze task revision, scope, profile, gates, evidence, and source fingerprints |
| `validate-execution-contract` | Detect manual mutation, stale run/task identity, scope/profile drift, and source-policy drift |
| `route-role` | Produce and persist a deterministic runtime-owned role decision |
| `record-progress` | Append a semantic progress snapshot and update no-progress status |
| `record-performance` | Record an explicit best-effort performance phase |
| `performance-report` | Print phase totals and deterministic before/after routing evidence |

Typical preflight:

```bash
bash scripts/next-action.sh --workspace .teamloop
bash scripts/prepare-execution.sh --workspace .teamloop
bash scripts/validate-state.sh --workspace .teamloop
```

PowerShell:

```powershell
.\scripts\next-action.ps1 -Workspace .teamloop
.\scripts\prepare-execution.ps1 -Workspace .teamloop
.\scripts\validate-state.ps1 -Workspace .teamloop
```

## Immutable run contract

An optimized run owns:

```text
.teamloop/runs/<run-id>/execution-policy.json
.teamloop/runs/<run-id>/execution-manifest.json
.teamloop/runs/<run-id>/execution-contract-validation.json
```

Semantic fingerprints exclude timestamps and performance data. Repeating materialization with identical inputs reuses the same contract. Changing task revision, scope, profile, gate policy, role policy, protected-path policy, or active profile for the same run fails and requires a fresh run/task revision.

Do not edit these files manually. `validate-state`, `validate-execution-contract`, and `final-gate` verify their schema and integrity.

## Event-driven role routing

Role decisions are appended to:

```text
.teamloop/runs/<run-id>/role-routing-history.jsonl
```

Each decision is schema-validated and content-addressed. Examples:

| Event/condition | Deterministic result |
|---|---|
| fast implementation complete, no trigger | gatekeeper |
| standard/audit implementation complete | change reviewer |
| audit review complete | watchdog |
| audit watchdog complete | gatekeeper/project gates |
| `NO_PROGRESS_DETECTED` | watchdog |
| watchdog completes a no-progress diagnosis | materially different `RETRY_EXECUTOR` in the same run |
| final handoff | sentinel |
| sentinel complete | final gate |

A mutated routing record makes `validate-state` fail.

## No-progress control

Artifacts:

```text
.teamloop/runs/<run-id>/progress-history.jsonl
.teamloop/runs/<run-id>/no-progress-result.json
```

The signature includes bounded repository content, task revision/status, gate failures, blockers, findings, review evidence, validation failures, unresolved executable work, and continuation inputs. It excludes timestamps, performance durations, and formatting-only runtime noise.

The default threshold is two identical semantic snapshots and is bounded by policy. At the threshold:

```text
NO_PROGRESS_DETECTED → RUN_WATCHDOG
```

After watchdog diagnosis, the runtime marks `STRATEGY_CHANGE_REQUIRED` and permits only a materially different retry in the same run. An unchanged retry triggers no-progress again.

Deleting `TODO`/`FIXME`/warning markers or open findings without any non-marker scoped content change or changed gate, review, validation, task-state, or blocker evidence is classified as `SUPPRESSION_ONLY_NOT_PROGRESS`; it does not reset the streak. Replacing a marker with a materially different implementation changes the normalized scope fingerprint and counts as an attempted fix.

Malformed history is a hard validation error. It is never silently treated as progress.

## Performance tracing

Artifact:

```text
.teamloop/runs/<run-id>/performance-trace.json
```

Tracked phases include state load, next-action resolution, contract creation/validation, role dispatch, state writes, scope validation, gates, continuation decisions, sentinel, progress detection, and final gate. Tracing is best-effort and cannot corrupt semantic state.

Tests use `TEAMLOOP_FAKE_CLOCK_MS` rather than sleeps:

```bash
TEAMLOOP_FAKE_CLOCK_MS='[100,125]' \
  bash scripts/prepare-execution.sh --workspace .teamloop
```

`performance-report` also emits a deterministic policy-level comparison. For a low-risk task with no optional trigger:

```text
before: executor + reviewer + watchdog + sentinel = 4 role invocations
 after: executor + final sentinel                  = 2 role invocations
saved: 2 unconditional role invocations (50%)
```

This is invocation-count evidence, not a wall-clock speed claim. Actual durations remain environment-dependent.

## Recovery

If policy or manifest validation fails:

1. Do not overwrite or repair the runtime-owned JSON manually.
2. Inspect `execution-contract-validation.json`.
3. Restore the frozen inputs when the drift is accidental.
4. When the task/profile/scope legitimately changed, create a fresh run or task revision.
5. Re-run `prepare-execution`, `validate-state`, scope checks, and project gates.

If no-progress fires, do not repeat the same executor command. Run watchdog, record a materially different strategy, and use `RETRY_EXECUTOR` in the same run.

## What fast execution never skips

- runtime-state validation;
- scope and protected-path enforcement;
- evidence integrity;
- required project gates;
- continuation decision;
- policy-required review;
- mandatory final sentinel;
- final gate;
- same-run project-gate and reviewed-content evidence when scoped content changed;
- the distinction between `SAFE_CHECKPOINT` and `DONE`.
