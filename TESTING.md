# Testing TeamLoop Harness

This file is a practical checklist for validating the harness before using it on bigger projects.

## 1. Self-test the runtime

```bash
PY=/usr/bin/python3 bash tests/run-tests.sh
```

Expected:

```text
Results: 51/51 passed, 0 failed
```

The test suite covers workspace initialization, schema validation, JSONL escaping, state transitions, gate pass/fail behavior, scope safety, task-slicer routing, and prompt golden checks.

## 2. Validate the smallest useful loop

Use a temporary git repository and point `HARNESS` to this repository.

```bash
HARNESS=/absolute/path/to/teamloop-harness
TMP=$(mktemp -d)
mkdir -p "$TMP/project/src"
cd "$TMP/project"
git init -q

bash "$HARNESS/scripts/init-workspace.sh" --workspace .teamloop --profile generic-software-task
bash "$HARNESS/scripts/validate-state.sh" --workspace .teamloop

cat > .teamloop/state/backlog.jsonl <<'JSONL'
{"schemaVersion":1,"taskId":"task-001","title":"Create src file","status":"READY","priority":"P1","origin":"manual-smoke","scope":["src/**"],"allowedWrites":["src/**",".teamloop/**"],"forbiddenWrites":["README.md"],"requiredEvidence":["scope check passes"],"successCriteria":["src/ok.txt exists"],"forbiddenActions":["do not edit README.md"],"humanRequired":false,"blockers":[]}
JSONL

bash "$HARNESS/scripts/next-action.sh" --workspace .teamloop
bash "$HARNESS/scripts/apply-transition.sh" --workspace .teamloop --action RUN_EXECUTOR --task-id task-001
printf 'ok\n' > src/ok.txt
bash "$HARNESS/scripts/check-scope.sh" --workspace .teamloop
bash "$HARNESS/scripts/apply-transition.sh" --workspace .teamloop --action RUN_GATEKEEPER
bash "$HARNESS/scripts/run-gates.sh" --workspace .teamloop
bash "$HARNESS/scripts/validate-state.sh" --workspace .teamloop
```

Expected: scope passes, gates pass, state validates.

## 3. Check safety after task completion

After a task reaches `SAFE_CHECKPOINT`, stale task scope must not keep granting permissions.

```bash
bash "$HARNESS/scripts/apply-transition.sh" --workspace .teamloop --action CONTINUE_LOOP
printf 'should fail\n' > src/stale-scope.txt
bash "$HARNESS/scripts/check-scope.sh" --workspace .teamloop
```

Expected: scope check fails unless a new active task explicitly allows `src/**`.

## 4. Check task-slicer routing

```bash
TMP=$(mktemp -d)
cd "$TMP"
git init -q
bash "$HARNESS/scripts/init-workspace.sh" --workspace .teamloop --profile generic-software-task
bash "$HARNESS/scripts/apply-transition.sh" --workspace .teamloop --action RUN_TASK_SLICER

cat > .teamloop/state/backlog.jsonl <<'JSONL'
{"schemaVersion":1,"taskId":"task-001","title":"Ready task","status":"READY","priority":"P1","origin":"manual-smoke","scope":["src/**"],"allowedWrites":["src/**",".teamloop/**"],"forbiddenWrites":[],"requiredEvidence":["ok"],"successCriteria":["ok"],"forbiddenActions":[],"humanRequired":false,"blockers":[]}
JSONL

bash "$HARNESS/scripts/next-action.sh" --workspace .teamloop
```

Expected: `RUN_EXECUTOR` with `taskId=task-001`, not `RUN_TASK_SLICER`.

## 5. First real-project trials

Start with small tasks:

- docs-only change;
- one failing unit test;
- one small refactor with clear scope;
- one shell gate such as `npm test -- --runInBand`, `dotnet test`, or `pytest`;
- one deliberate scope violation to confirm the guard catches it.

Avoid first testing on broad refactors, dependency upgrades, or multi-directory migrations. Those are good later stress tests, but not first validation targets.

## 6. Evidence to inspect

After a run, inspect:

```text
.teamloop/state/team-state.json
.teamloop/state/backlog.jsonl
.teamloop/state/events.jsonl
.teamloop/state/run-ledger.jsonl
.teamloop/runs/<run-id>/gate-result.json
```

Useful questions:

- Did every runtime step leave `validate-state` passing?
- Did `next-action` route to the expected role?
- Did the task scope match the changed files?
- Did gates update state correctly on pass and fail?
- Did the loop continue instead of stopping too early?
- Did any prompt instruct the agent to manually edit runtime state?

## 7. Suggested maturity ladder

```text
Level 0: tests/run-tests.sh passes
Level 1: manual toy repo smoke passes
Level 2: OpenCode dry run on a docs-only task
Level 3: OpenCode run on one real code/test fix
Level 4: repeated runs without state desync
Level 5: one small migration-style task campaign
```
