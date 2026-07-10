# TeamLoopHarness MVP+ hardening campaign

Implement the following iterations strictly in order. Complete only one bounded iteration per supervised checkpoint. Do not begin the next iteration until the current iteration has green tests, valid runtime state, and reviewable evidence.

## Global rules

- Runtime commands are the source of truth.
- Every successful runtime command must leave `validate-state` passing.
- Do not weaken schemas, tests, gates, or scope to hide a defect.
- Preserve all existing routing, stale-state, gate PASS/FAIL, malformed-artifact, and scope-guard regressions.
- Do not add Selenium/Playwright-specific behavior to the generic core.

## Iteration 0 — Baseline verification

- Read `README.md`, `TESTING.md`, schemas, policies, scripts, profiles, and OpenCode prompts.
- Run `PY=/usr/bin/python3 bash tests/run-tests.sh`.
- Initialize a fresh temporary workspace and run `validate-state`.
- Record the baseline test count and stop if baseline is red.

Acceptance: full suite green; fresh workspace valid; baseline recorded.

## Iteration 1 — Structured memory and memory doctor

Add a generic `.teamloop/memory/` subsystem:

```text
project-profile.json
lessons.jsonl
antipatterns.jsonl
decisions.jsonl
evidence-map.jsonl
memory-summary.md
```

Add schemas and `memory-doctor` wrappers. Active lessons/antipatterns/decisions require evidence. Unverified hypotheses are not active guidance. Deprecated/rejected/superseded records remain history but cannot be treated as current guidance. `validate-state` must include the same validation logic without duplicated drifting rules.

Acceptance: fresh/empty memory passes; malformed JSONL fails cleanly; active guidance without evidence fails; deprecated history is retained but inactive; full suite green.

## Iteration 2 — Strict continuation decision

Add `.teamloop/state/continuation-decision.json`, schema, writer, validator, and state consistency checks.

Supported decisions:

```text
DONE
SAFE_CHECKPOINT
CONTINUE
HUMAN_DECISION_REQUIRED
BLOCKED
```

Invariants:

- `SAFE_CHECKPOINT != DONE`.
- `HUMAN_DECISION_REQUIRED` requires a valid open blocker and concrete questions.
- `DONE` requires no active run/task, no open READY/IN_PROGRESS work, passing required gates, valid state, and evidence.
- Stale task/run references fail validation or are explicitly ignored by a documented rule.

Acceptance: writer and validator exist; impossible combinations fail; `validate-state` checks continuation consistency; full suite green.

## Iteration 3 — Final gate aggregator

Add `final-gate` and `.teamloop/state/final-gate-result.json`.

`run-gates` executes project checks. `final-gate` evaluates the integrity of the whole bounded iteration by aggregating:

- state validation;
- memory doctor;
- continuation validation;
- scope result;
- latest required gate result;
- active task/run consistency;
- blocker consistency;
- malformed and stale artifacts.

`PASS` means the final-gate evaluation supports its emitted decision; it does not automatically mean the whole project is `DONE`.

Acceptance: DONE/SAFE_CHECKPOINT/repair/human escalation are evidence-backed; repeated runs are semantically idempotent; OpenCode handoff requires final gate; full suite green.

## Iteration 4 — Protected harness integrity and dangerous operations

Add a protected-path policy and `check-guard-integrity`.

Protect at least runtime core, validators, gate/final-gate scripts, schemas, policies, and OpenCode command prompts. Protected changes are allowed but require full tests, independent change review, and later sentinel evidence.

Document/detect where practical:

- destructive repository operations;
- test/assertion/gate suppression;
- manual runtime-state manipulation;
- guard weakening and policy self-exclusion.

Do not rely on brittle immutable checksums as the only mechanism.

Acceptance: normal changes do not trigger protected review; protected changes do; missing evidence blocks final gate; temporary git repos are used in tests; full suite green.

## Iteration 5 — Structured sentinel inspection

Add `.teamloop/runs/<run-id>/sentinel-inspection.json`, schema, safe writer/validator, and deterministic findings where possible.

Categories should cover state consistency, scope bypass, gate weakening, test suppression, evidence manipulation, protected-file changes, hidden unresolved work, manual state mutation, and documentation/contract drift.

Critical unresolved findings block `DONE`. Warnings may permit a truthful checkpoint with justification. Sentinel does not edit implementation code or resolve its own findings.

Acceptance: findings are structured and evidence-backed; protected changes require sentinel; final gate consumes sentinel results; full suite green.

## Iteration 6 — OpenCode integration and end-to-end hardening

Bind `/supervised-task` and all roles to the new runtime commands:

1. `next-action`;
2. one bounded role execution;
3. runtime transition/event writers;
4. state validation;
5. project gates;
6. continuation decision;
7. sentinel when required;
8. final gate before handoff.

No role may instruct manual mutation of runtime-owned artifacts.

Add executable/documented end-to-end scenarios for:

- successful bounded task;
- scope violation;
- required gate failure;
- valid human blocker;
- protected harness change;
- memory integrity.

Update `README.md`, `TESTING.md`, and architecture docs.

Acceptance: all workflows are runtime-bound; every runtime-produced state is valid and recoverable through `next-action`; full suite and all smoke scenarios pass.

## Explicit non-goals

Do not implement during this campaign:

- wave mode;
- adapters for other agents;
- codemod/AST transformation engines;
- Selenium/Playwright domain logic;
- distributed orchestration;
- database state;
- web UI or plugin marketplace.

## Required checkpoint report after each iteration

Report:

1. iteration completed;
2. files changed;
3. commands/schemas/policies added;
4. tests added and full result;
5. manual smoke scenarios run;
6. known limitations and deferred work;
7. exact evidence supporting the checkpoint.

Never claim the entire campaign is complete after finishing only one iteration.
