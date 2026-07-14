# Quality/Value Boundary Management

YourAITeam 0.5 introduces a deterministic quality/value boundary between successful gates and workflow advancement.

## Why it exists

Autonomous work fails in two opposite ways:

- **unbounded diligence**: research, review, and remediation repeat without a rational stop;
- **greedy completion**: tickets and gates turn green while required deliverables, behavior, or evidence remain defective.

The runtime enforces this invariant:

```text
Hard checks define what is forbidden.
Boundary management chooses the highest-value option among what remains allowed.
```

The manager cannot override hard checks, edit implementation or evidence, change policy or budget, or grant advancement by writing a decision JSON.

## Lifecycle position

For a task with a boundary contract:

```text
execute -> review -> deterministic gates PASS
        -> NEEDS_BOUNDARY_DECISION
        -> quality-value-manager decision
        -> runtime-validated receipt and advancement
```

A gate PASS is necessary but not sufficient. The task and run remain active until the runtime verifies a current acceptance receipt.

Tasks without a boundary contract keep the pre-0.5 lifecycle for compatibility.

## Boundary packet

`boundary-measure` recomputes a compact authoritative packet from primary artifacts. It records:

- expected and observed deliverables;
- hard invariant failures and visible soft debt;
- validation evidence bound to current inputs;
- normalized root patterns and cascade counts;
- before/after metrics;
- estimated cost, confidence, reach, reuse, and payoff;
- remaining improvement budget and no-progress streak;
- artifact, policy, config, tool, and evidence fingerprints.

Editable prose and agent-authored counters are diagnostic only. Acceptance authority comes from recomputation.

## Decisions

The closed decision set is:

```text
ACCEPT_BOUNDARY
ACCEPT_WITH_RECORDED_SOFT_DEBT
IMPROVE_CURRENT_BOUNDARY
SPLIT_CURRENT_BOUNDARY
STOP_BUDGET_EXHAUSTED
REQUEST_HUMAN_DECISION
```

The runtime rejects impossible decisions. Acceptance is forbidden with hard failures. Soft-debt acceptance requires an explicit debt list. Improvement requires an authoritative candidate. Budget stop requires exhausted budget or the configured no-progress threshold.

## Profiles

Default improvement budgets:

| Profile | Maximum cycles |
|---|---:|
| fast | 2 |
| standard | 4 |
| audit | 6 |

Profiles change ceremony and finite budget, not hard quality thresholds.

## Payoff arbitration

Candidate priority is policy-driven and transparent:

```text
expected payoff =
  affected items
  x repetition or reuse
  x blocking severity
  x confidence of a safe fix
  / estimated cost
```

A reusable root fix should outrank low-reach symptom cleanup when its measured payoff is higher.

## Trusted history and receipts

Improvement and decision histories are hash-chained and validated fail-closed. Acceptance receipts bind:

- current primary artifacts;
- authoritative metrics;
- boundary contract and policy;
- runtime/tool compatibility;
- validation evidence;
- manager decision and role receipt;
- the complete predecessor acceptance chain.

Artifact drift, copied evidence, replayed role receipts, edited history, or predecessor drift invalidates advancement.

## Commands

```bash
bash scripts/boundary-create.sh --workspace .teamloop --contract boundary.json
bash scripts/boundary-measure.sh --workspace .teamloop --boundary-id boundary-001
bash scripts/boundary-status.sh --workspace .teamloop --boundary-id boundary-001
bash scripts/boundary-decide.sh --workspace .teamloop --boundary-id boundary-001 \
  --decision ACCEPT_BOUNDARY --reason "Current bounded result meets the contract"
bash scripts/boundary-verify.sh --workspace .teamloop --boundary-id boundary-001
bash scripts/boundary-lock-status.sh --workspace .teamloop --boundary-id boundary-001
```

After `IMPROVE_CURRENT_BOUNDARY`, execute exactly one selected bounded action and record the measured result:

```bash
bash scripts/boundary-complete-improvement.sh --workspace .teamloop \
  --boundary-id boundary-001 --candidate-id root-shared-schema
```

## Domain adapters

The core remains domain-neutral. An adapter supplies:

- primary artifact measurement;
- hard-invariant mapping;
- root-pattern extraction;
- cost/value weights;
- acceptance requirements;
- optional dashboard fields.

See `adapters/generic-software-task/` for the reference adapter.

## Trusted writer contract

Acceptance receipts and role receipts are emitted only by `teamloop-core`; the manager cannot write them directly. The policy fixes `trustedWriterCommand=teamloop-core`, `managerMayWriteReceipts=false`, `requireManagerRoleReceipt=true`, and `historyMode=append-only-hash-chain`. Runtime verification also recomputes current primary-artifact and evidence fingerprints, so a self-hash alone is never acceptance authority.

## Read-only boundary dashboard

```bash
python scripts/teamloop-core.py boundary-status --workspace .teamloop --boundary-id <id> --format html --output boundary-dashboard.html
```

The dependency-free HTML keeps accepted progress separate from draft coverage and includes contextual `?` hints. It is a presentation surface only; primary artifacts, authoritative packets, and receipt-chain verification remain the acceptance authority.
