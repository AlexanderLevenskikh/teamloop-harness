# TeamLoop Harness

Reusable delivery harness for supervised agent teams.

## Core Invariants

```
MANUAL_REVIEW ≠ HUMAN_REQUIRED
SAFE_CHECKPOINT ≠ DONE
RESEARCH_COMPLETE ≠ DONE
```

An agent team must not hand unfinished work back to the user just because a subagent says "developer action" or "manual review". The supervisor must route uncertainty back into research, review, task slicing, execution, or gate repair. Human handoff is allowed only when there is an explicit classified blocker with evidence and concrete questions.

## The Team Loop

Complete lifecycle — from discovery through final gate to either continuation or handoff:

```
discover → plan → execute → review → gate → sentinel → guard → memory → validate
   |          |         |          |         |          |          |         |        |
   ▼          ▼         ▼          ▼         ▼          ▼          ▼         ▼        ▼
 DISCOVERY  PLANNING  EXECUTING  REVIEWING  NEEDS_GATE  SENTINEL  GUARD     MEMORY   VALIDATED
                                              CHECK      CHECK     CHECK
```

Each phase has a dedicated role and a set of checks:

| Phase | Role | Key action | Gate |
|-------|------|-----------|------|
| DISCOVERY | discoverer | gather context | — |
| PLANNING | planner | produce backlog | — |
| EXECUTING | executor | implement within scope | `check-scope` |
| REVIEWING | change-reviewer | verify against criteria | — |
| NEEDS_GATE | gatekeeper | run gate-policy | `run-gates` |
| SENTINEL CHECK | sentinel | 9 safety inspections | `run-sentinel` |
| GUARD CHECK | guard | protected-path integrity | `check-guard-integrity` |
| MEMORY CHECK | memory-doctor | lessons/evidence integrity | `memory-doctor` |
| VALIDATED | — | full state validation | `validate-state` |

After gates pass, the loop returns to `next-action` which routes to either:
- `RUN_EXECUTOR` — next READY task exists;
- `CONTINUE` — backlog consumed, safe checkpoint;
- `HUMAN_DECISION_REQUIRED` — classified blocker with evidence and questions.

## Core Principles

### 1. Supervisor owns the work state

The supervisor is not a passive router. It must:
- read current state;
- determine the next action;
- select the correct role;
- refuse premature completion;
- route failed role outputs back into the team;
- stop for humans only with a classified blocker.

### 2. Every role output must be accepted by another role or a gate

No role may unilaterally declare final success.

### 3. MANUAL_REVIEW is not HUMAN_REQUIRED

`MANUAL_REVIEW` means agent review is needed with source truth, target evidence, and local context.

`HUMAN_REQUIRED` is only valid when there is a blocker such as:
- missing credentials;
- missing source truth;
- product behavior ambiguity;
- destructive action requiring approval;
- scope policy forbids required edit;
- legal/security/ownership decision.

### 4. SAFE_CHECKPOINT is not DONE

A safe checkpoint means the state is honest and verified, not that all work is complete.

### 5. Research must pass review

A research report is not accepted until research-lead verifies counts, evidence, contradictions, actionability, human/agent classification, and recommended bounded tasks.

## Memory Subsystem

Persistent cross-task memory lives in `.teamloop/memory/`. It survives between tasks, campaigns, and sessions.

### Structure

```
.teamloop/memory/
  lessons.jsonl           — curated lessons learned (ACTIVE, SUPERSEDED, DEPRECATED)
  antipatterns.jsonl      — anti-patterns to avoid (ACTIVE, REJECTED)
  decisions.jsonl         — product and technical decisions (ACTIVE, SUPERSEDED)
  evidence-map.jsonl      — evidence records linked to lessons/antipatterns
  project-profile.json    — project-specific memory configuration
```

### Lessons

Each lesson has a `lessonId`, `title`, `description`, and `status`. Active lessons require at least one `evidenceId` pointing to a record in `evidence-map.jsonl`.

Valid statuses:
- **ACTIVE** — currently applicable; requires verified evidence.
- **SUPERSEDED** — replaced by a newer lesson; `supersededBy` must reference an existing lesson.
- **DEPRECATED** — retired; no evidence required.

### Evidence Map

Evidence records have an `evidenceId`, `type` (e.g., `TEST_RESULT`, `RUNTIME_ERROR`, `REVIEW_COMMENT`), and `reference` pointing to a file or URL. Evidence is considered **VERIFIED** by default; explicitly marking it `UNVERIFIED` causes validation to fail when referenced by an ACTIVE lesson.

### Antipatterns

Anti-patterns capture recurring mistakes. Active antipatterns require evidence. Rejected antipatterns do not.

### Memory Doctor

The `memory-doctor` command diagnoses issues across all memory subsystems:

```bash
python scripts/teamloop-core.py memory-doctor --workspace .teamloop
```

Output is JSON with a `status` field (`PASS`, `FAIL`, `WARNING`) and an array of `checks`. Each check reports its name, status, and description. Warnings (e.g., empty subsystems) do not cause failure; errors (e.g., ACTIVE lesson without evidence) do.

### Memory Validation

`validate-state` checks all memory artifacts:
- JSONL files parse as valid JSON on each line.
- ACTIVE lessons have `evidenceIds` that exist in `evidence-map.jsonl`.
- Evidence referenced by ACTIVE lessons is not marked `UNVERIFIED`.
- SUPERSEDED lessons have `supersededBy` referencing an existing lesson.
- `project-profile.json` validates against the memory-profile schema.
- Missing memory directory is tolerated (no crash).

## Sentinel / Integrity Inspection

The sentinel is a read-only safety inspector that runs 9 independent checks across the workspace. It never modifies state — it produces a report in `.teamloop/runs/run-{id}/sentinel-inspection.json`.

### Invocation

```bash
bash scripts/run-sentinel.sh --workspace .teamloop
# or
python scripts/teamloop-core.py run-sentinel --workspace .teamloop
```

### 9 Check Categories

| # | Category | What it detects |
|---|----------|----------------|
| 1 | STATE_CONSISTENCY | Corrupted or manually-edited team-state.json |
| 2 | SCOPE_POLICY_WEAKENING | Empty or weakened scope-policy.json |
| 3 | GATE_WEAKENING | Missing or empty gate-policy.json |
| 4 | TEST_SUPPRESSION | Missing tests/ directory |
| 5 | PROTECTED_FILE_CHANGE | Staged modifications to protected paths |
| 6 | HIDDEN_UNRESOLVED_WORK | Orphaned READY tasks in backlog |
| 7 | MANUAL_STATE_MUTATION | State changed without corresponding events |
| 8 | EVIDENCE_MANIPULATION | Events.jsonl truncated or gaps detected |
| 9 | DOCS_CONTRACT_DRIFT | Schema files with invalid JSON |

### Severity Levels

- **CRITICAL** — overall status becomes `FAIL`; `validate-state` rejects the workspace.
- **WARNING** — overall status becomes `WARNING`; workspace remains valid but flagged.
- **INFO** — informational finding; does not affect overall status.

### Sentinel Report

The report includes `schemaVersion`, `runId`, `inspectedAtUtc`, `findings[]`, `overallStatus`, and `summary` (with `totalFindings`, `criticalCount`, `warningCount`, `infoCount`).

`validate-state` reads the most recent sentinel report and fails if it contains CRITICAL findings.

## Guard Integrity

Guard integrity protects critical project files from unauthorized modification. It uses a `protected-paths.json` policy and compares staged git changes against the protected path patterns.

### Policy

```json
{
  "schemaVersion": 1,
  "protectedPaths": ["scripts/**", "schemas/**", "tests/**"],
  "enforcementLevel": "error",
  "evidenceRequired": {
    "fullTestSuite": true,
    "independentReview": true
  }
}
```

Place the policy at `.teamloop/policies/protected-paths.json`.

### Enforcement Levels

- **error** — violations cause `check-guard-integrity` to exit 1 with `status: FAIL`.
- **warn** — violations are reported but command exits 0 (status still `FAIL` internally).

### Invocation

```bash
bash scripts/check-guard-integrity.sh --workspace .teamloop
# or
python scripts/teamloop-core.py check-guard-integrity --workspace .teamloop
```

### Checks Performed

1. **protected-path-violations** — staged changes to protected paths.
2. **test-file-deleted** — staged deletion of files in `tests/`.
3. **schema-integrity** — all schema files in `schemas/` parse as valid JSON.
4. **policy-schema-match** — protected-paths.json validates against its schema.

Without a policy file, the command returns `PASS` with a note that no policy exists.

## Final Gate

The final gate is an actual runtime aggregator, not a documentation-only command list.

```bash
bash scripts/run-sentinel.sh --workspace .teamloop
bash scripts/check-guard-integrity.sh --workspace .teamloop
bash scripts/memory-doctor.sh --workspace .teamloop
bash scripts/final-gate.sh --workspace .teamloop
```

It writes `.teamloop/state/final-gate-result.json` and a per-run copy. Checks include state, memory, continuation, scope, project gates, active task/run consistency, blockers, stale artifacts, sentinel, guard integrity, reviewed-content integrity, immutable execution-contract integrity, and unresolved no-progress. Optimized runs cannot pass without a final sentinel report.

## Fast Execution Runtime

Before one bounded role action, the supervisor runs `next-action` and `prepare-execution`. The runtime resolves `fast`, `standard`, or `audit`, then freezes:

```text
.teamloop/runs/<run-id>/execution-policy.json
.teamloop/runs/<run-id>/execution-manifest.json
.teamloop/runs/<run-id>/execution-contract-validation.json
```

The contract is content-addressed and excludes timestamps/performance from semantic fingerprints. Identical materialization is idempotent; changed task, scope, profile, gate/policy inputs, or manual mutation fail and require a fresh run/task revision.

Role decisions are runtime-owned and appended to `role-routing-history.jsonl`. Progress is recorded in `progress-history.jsonl`; `no-progress-result.json` blocks blind repeats. Two identical semantic snapshots normally produce `NO_PROGRESS_DETECTED → RUN_WATCHDOG`. After watchdog diagnosis, `RETRY_EXECUTOR` preserves the task/run identity but requires a materially different strategy. Suppression-only removal of TODO/warning/finding signals without changed executable evidence does not count as progress.

For `audit`, reviewer completion routes to watchdog and watchdog completion routes to project gates; the mandatory final sentinel still runs immediately before `final-gate`. Stale gate, sentinel, or reviewed-content artifacts from another run cannot satisfy the current immutable execution contract.

Performance tracing is best-effort in `performance-trace.json`; failures in tracing never corrupt semantic state. See [docs/FAST_EXECUTION.md](docs/FAST_EXECUTION.md) for profile tables, trigger rules, recovery, and examples.

## Workspace

Default workspace: `.teamloop/`

Key files:
- `.teamloop/state/team-state.json` — current team state
- `.teamloop/state/events.jsonl` — append-only event ledger
- `.teamloop/state/backlog.jsonl` — task backlog
- `.teamloop/state/current-task.json` — currently active task
- `.teamloop/state/continuation-decision.json` — last continuation decision
- `.teamloop/state/run-ledger.jsonl` — run history
- `.teamloop/policies/scope-policy.json` — scope guard rules
- `.teamloop/policies/gate-policy.json` — gate execution rules
- `.teamloop/policies/protected-paths.json` — guard integrity protected paths
- `.teamloop/profiles/active-profile.json` — active domain profile
- `.teamloop/memory/lessons.jsonl` — memory lessons
- `.teamloop/memory/evidence-map.jsonl` — evidence records
- `.teamloop/memory/antipatterns.jsonl` — anti-patterns
- `.teamloop/memory/decisions.jsonl` — decisions
- `.teamloop/memory/project-profile.json` — memory configuration
- `.teamloop/runs/run-{id}/gate-result.json` — gate execution result
- `.teamloop/runs/run-{id}/sentinel-inspection.json` — sentinel report
- `.teamloop/runs/run-{id}/execution-policy.json` — resolved role-routing policy
- `.teamloop/runs/run-{id}/execution-manifest.json` — immutable bounded execution contract
- `.teamloop/runs/run-{id}/role-routing-history.jsonl` — runtime-owned role decisions
- `.teamloop/runs/run-{id}/progress-history.jsonl` — semantic progress snapshots
- `.teamloop/runs/run-{id}/no-progress-result.json` — no-progress decision
- `.teamloop/runs/run-{id}/performance-trace.json` — performance instrumentation
- `.teamloop/research/` — research reports

## Completion Semantics

State may become `DONE` only when:
- backlog is empty or all tasks are DONE/CANCELLED;
- required gates PASS or are explicitly skipped with accepted blocker;
- no open HUMAN_DECISION_REQUIRED blockers;
- final report exists;
- full gate chain passes (sentinel, guard, memory, validate-state);
- state validation passes.

State may become `HUMAN_DECISION_REQUIRED` only when:
- a blocker record exists in `.teamloop/state/blockers.jsonl`;
- blocker category is from the allowed list;
- evidence exists;
- questionsForHuman are present;
- supervisor explains why the agent loop cannot continue.

## The team must not confuse uncertainty with human ownership.

If work is unclear, route to research.
If research is weak, route to research review.
If research is actionable, route to task slicing.
If a task is too large, slice it smaller.
If implementation fails, route to review or repair.
If gates fail, classify and repair.
Only stop for a human when a blocker is explicitly classified with evidence and questions.

```
MANUAL_REVIEW is not HUMAN_REQUIRED.
SAFE_CHECKPOINT is not DONE.
RESEARCH_COMPLETE is not DONE.
```

## Scripts

| Script | Description |
|--------|-------------|
| `init-workspace` | Initialize `.teamloop/` workspace |
| `write-event` | Append event to `events.jsonl` |
| `next-action` | Determine next action from state |
| `check-scope` | Validate file changes against scope policy |
| `run-gates` | Execute gate checks from policy |
| `validate-state` | Validate all state files |
| `run-sentinel` | Run sentinel inspection (9 safety checks) |
| `check-guard-integrity` | Check for unauthorized changes to protected files |
| `memory-doctor` | Diagnose memory subsystem issues |
| `write-continuation-decision` | Write a continuation decision to state |
| `prepare-execution` | Resolve policy, materialize and validate immutable run contract |
| `resolve-execution-policy` | Persist deterministic execution profile |
| `materialize-execution-manifest` | Freeze bounded task/scope/gate/evidence inputs |
| `validate-execution-contract` | Detect mutation and stale/drifted inputs |
| `record-progress` | Detect semantic progress or no-progress |
| `route-role` | Persist event-triggered role decision |
| `record-performance` | Append best-effort trace phase |
| `performance-report` | Summarize trace and deterministic routing comparison |
| `final-gate` | Aggregate all blocking handoff checks |

## Profiles

Profiles define domain-specific behavior: discovery questions, gate commands, allowed roots, forbidden actions, role prompt overrides, and task slicing strategy.

Default profile: `generic-software-task`
