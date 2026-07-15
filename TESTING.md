# Testing YourAITeam

This checklist validates both the MVP+ hardening runtime and the Fast Execution Contract.

## Full regression suites

### Bash

```bash
PY=/usr/bin/python3 bash tests/run-tests.sh
```

Expected for this revision:

```text
Results: N/N passed, 0 failed
```

where N is the current number of test functions in `run-tests.sh`. The expected count should match the actual test function count in run-tests.sh. Run `--full` to verify.

The runner also supports bounded diagnostic ranges without skipping tests in normal CI:

```bash
TEAMLOOP_TEST_FROM=151 TEAMLOOP_TEST_TO=227 \
  PY=/usr/bin/python3 bash tests/run-tests.sh
```

### PowerShell

```powershell
pwsh -NoProfile -File tests/run-tests.ps1
```

Expected for this revision:

```text
Results: 87/87 passed, 0 failed
```

PowerShell coverage mirrors the critical profile, manifest, no-progress, fake-clock, routing, sentinel, and wrapper contracts. A platform that cannot execute PowerShell must report it as **not executed**, never as PASS.

## Coverage map

| Area | Representative coverage |
|---|---|
| Core lifecycle | initialization, dispatch, transitions, identity preservation, scope and gate behavior |
| Memory | schema-valid JSONL, verified evidence, superseded references, missing subsystem behavior |
| Continuation | schema-valid decisions, terminal transitions, blocker semantics |
| Guard and sentinel | protected paths (including unstaged-path parsing), dangerous operations, nine unique sentinel checks, docs drift |
| Final gate | PASS/failure propagation, schema artifact, reviewed-content integrity, orphaned task detection |
| Fast profiles | deterministic `fast`/`standard`/`audit`, protected-scope escalation |
| Immutable contract | idempotent materialization, manual mutation, task/scope/profile/policy drift |
| No-progress | identical snapshots, material reset, performance-only noise, pure suppression-only pseudo-progress, real implementation after a TODO |
| Routing | runtime-owned decisions, watchdog recovery, audit watchdog → project gates, no watchdog self-loop, mandatory final sentinel/final gate |
| Performance | fake clock, trace schema, deterministic role-invocation comparison |
| OpenCode | runtime command order, no direct runtime-owned state mutation |
| Quality/value boundary | hard-gate non-waiver, explicit soft debt, payoff ordering, measured progress, finite budgets, advancement lock, drift/replay/tamper rejection, restart safety, dashboard semantics |

## Fresh-workspace smoke

```bash
HARNESS=/absolute/path/to/your-ai-team
TMP=$(mktemp -d)
cd "$TMP"
git init -q
git config user.email test@your-ai-team.local
git config user.name Test

bash "$HARNESS/scripts/init-workspace.sh" --workspace .teamloop --profile generic-software-task
git add . && git commit -qm init

cat >> .teamloop/state/backlog.jsonl <<'JSONL'
{"schemaVersion":1,"taskId":"task-smoke","title":"Fast smoke","status":"READY","priority":"P2","origin":"manual-smoke","scope":["src/**"],"allowedWrites":["src/**",".teamloop/**"],"forbiddenWrites":[],"requiredEvidence":["scope and gates pass"],"successCriteria":["src/ok.txt exists"],"forbiddenActions":["do not weaken gates"],"humanRequired":false,"blockers":[]}
JSONL

bash "$HARNESS/scripts/apply-transition.sh" --workspace .teamloop --action RUN_EXECUTOR --task-id task-smoke
bash "$HARNESS/scripts/prepare-execution.sh" --workspace .teamloop
bash "$HARNESS/scripts/validate-execution-contract.sh" --workspace .teamloop
bash "$HARNESS/scripts/validate-state.sh" --workspace .teamloop
```

Expected: profile `fast`, immutable contract PASS, state valid.

## No-progress smoke

Without a relevant change:

```bash
bash "$HARNESS/scripts/record-progress.sh" --workspace .teamloop
bash "$HARNESS/scripts/record-progress.sh" --workspace .teamloop
bash "$HARNESS/scripts/next-action.sh" --workspace .teamloop
```

Expected: `NO_PROGRESS_DETECTED`, then `RUN_WATCHDOG`.

After watchdog diagnosis:

```bash
bash "$HARNESS/scripts/route-role.sh" --workspace .teamloop --event watchdog-complete
bash "$HARNESS/scripts/apply-transition.sh" --workspace .teamloop --action RETRY_EXECUTOR
```

Expected: `RETRY_EXECUTOR` preserves the current task and run. A materially different scoped change must be made before the next snapshot.

## Event-driven routing smoke

```bash
bash "$HARNESS/scripts/route-role.sh" --workspace .teamloop --event implementation-complete
bash "$HARNESS/scripts/route-role.sh" --workspace .teamloop --event final-handoff
bash "$HARNESS/scripts/route-role.sh" --workspace .teamloop --event sentinel-complete
```

For `fast`, the first command normally routes to gatekeeper. The final two must route to sentinel and final gate respectively.

## Performance trace smoke

```bash
bash "$HARNESS/scripts/performance-report.sh" --workspace .teamloop
```

Inspect:

```text
.teamloop/runs/<run-id>/performance-trace.json
```

The report must contain observed phase counts and a policy-level before/after role-invocation comparison. Timing-only changes must not alter progress signatures.

## Final handoff

Before handoff:

```bash
bash "$HARNESS/scripts/run-sentinel.sh" --workspace .teamloop
bash "$HARNESS/scripts/check-guard-integrity.sh" --workspace .teamloop
bash "$HARNESS/scripts/memory-doctor.sh" --workspace .teamloop
bash "$HARNESS/scripts/final-gate.sh" --workspace .teamloop
```

For an optimized run, a missing final sentinel is a blocking final-gate failure. `final-gate-result.json` is written to `.teamloop/state/` and the run directory.

## Evidence to inspect

```text
.teamloop/state/team-state.json
.teamloop/state/continuation-decision.json
.teamloop/state/final-gate-result.json
.teamloop/runs/<run-id>/execution-policy.json
.teamloop/runs/<run-id>/execution-manifest.json
.teamloop/runs/<run-id>/execution-contract-validation.json
.teamloop/runs/<run-id>/role-routing-history.jsonl
.teamloop/runs/<run-id>/progress-history.jsonl
.teamloop/runs/<run-id>/no-progress-result.json
.teamloop/runs/<run-id>/performance-trace.json
.teamloop/runs/<run-id>/sentinel-inspection.json
```

A report is not proof by itself. Verify that the claimed content exists in the checked-out Git `HEAD` and that current hashes still match reviewed evidence.


## Quality/value boundary focused tests

```bash
python3 -m unittest tests.test_quality_value_boundary -v
TEAMLOOP_TEST_FROM=265 TEAMLOOP_TEST_TO=266 PY=python3 bash tests/run-tests.sh
```

The adversarial suite proves that manager prose or decision JSON alone cannot grant acceptance; current primary artifacts, validation evidence, trusted history, role receipt, policy/runtime fingerprints, and predecessor receipts must all verify.

A manual smoke should observe:

```text
gates PASS -> NEEDS_BOUNDARY_DECISION
advance before receipt -> blocked
ACCEPT_BOUNDARY with current packet -> SAFE_CHECKPOINT
artifact drift -> boundary-verify FAIL and advancement locked
```

### Boundary dashboard smoke

After creating and measuring a boundary:

```bash
python scripts/teamloop-core.py boundary-status --workspace .teamloop --boundary-id <id> --format html --output boundary-dashboard.html
```

Verify that the HTML contains contextual hints and does not count draft coverage as accepted progress.

## Unified script validation

Validate every supported script surface in one pass:

```bash
python3 scripts/validate_scripts.py --root .
bash scripts/validate-scripts.sh --root .
```

On Windows:

```powershell
.\scripts\validate-scripts.ps1 -Root .
```

The validator checks:

- all `scripts/*.ps1` and `tests/run-tests.ps1` with the PowerShell parser when available;
- invalid wrapper attributes such as `ValueFromRemaining=` even without PowerShell installed;
- all shell scripts and extensionless shims with `bash -n`;
- CRLF/shebang/executable contracts for Unix/WSL;
- all Python runtime modules with `py_compile`;
- delegated shim targets and known mojibake markers.

`release-package.sh` runs this validator before packaging. `install.sh` runs it again after restoring executable bits.

## Sentinel cache-preflight regression

```bash
python3 -m unittest tests.test_runtime_efficiency -v
TEAMLOOP_TEST_FROM=267 TEAMLOOP_TEST_TO=268 PY=python3 bash tests/run-tests.sh
```

The regression proves that authoritative policy/state changes invalidate sentinel cache keys and that a cached non-PASS finding is rechecked fresh once. The runtime reports `STALE_ENTRY_RECOMPUTED` instead of forcing an agent to diagnose WSL paths or clear cache manually.


## Codex adapter compatibility

Run the focused suite:

```bash
python -m unittest tests.test_your_ai_team
```

It verifies default model inheritance, optional Sol/Terra/Luna pins, non-destructive project config merge, required custom-agent TOML fields, full delivery lifecycle skill guidance, provenance manifest, and doctor repair of incompatible model pins.

Static doctor smoke:

```bash
python scripts/codex_support.py --project-root <materialized-project> --no-cli
```

A live Codex subagent smoke is intentionally opt-in because it consumes plan credits/tokens and requires an installed authenticated Codex client.

## Codex live compatibility smoke

Static checks:

```powershell
.\scripts\codex-doctor.ps1 -ProjectRoot .
python -m unittest tests.test_your_ai_team
python scripts/teamloop-core.py adapter-verify --json
```

Optional paid live smoke:

```powershell
.\scripts\codex-smoke.ps1 -ProjectRoot . -Role writer -Json
```

Do not run the live smoke in every unit-test job. It consumes Codex usage and is intended for release or environment compatibility checks.
