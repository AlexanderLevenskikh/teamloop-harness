# ADR-0001: Runtime-enforced Quality/Value Boundary Manager

- Status: Accepted for 0.5.0-alpha.1
- Date: 2026-07-14

## Context

Gate-only workflows can either loop indefinitely or advance on locally green evidence while material value remains missing. A conversational manager is insufficient because it can edit its own inputs or be replayed after drift.

## Decision

Introduce an optional, domain-neutral boundary contract evaluated after deterministic gates and before task/run completion.

Responsibilities are separated:

1. deterministic measurement computes facts and hard failures;
2. the read-only `quality-value-manager` selects one decision from a closed enum;
3. the runtime validates the decision, budgets, role receipt, improvement history, and acceptance chain before advancement.

The feature is opt-in per bounded task/run in the alpha release. Existing tasks without a boundary contract preserve legacy behavior.

## Consequences

Positive:

- hard failures cannot be waived by managerial judgment;
- improvement is measured, finite, and restart-safe;
- acceptance becomes invalid after artifact/evidence/policy drift;
- root fixes can be prioritized transparently.

Costs:

- additional schemas, durable state, and lifecycle phase;
- adapters must define authoritative artifacts and invariant mapping;
- acceptance requires a current manager role receipt and verification.

## Rejected alternatives

- always-on managerial agent: expensive and non-deterministic;
- manager-authored acceptance JSON: insufficient trust boundary;
- weakening fast profile gates: violates quality invariants;
- using editable dashboards or ticket status as authority: vulnerable to evidence manipulation.

## Trusted writer contract

Acceptance receipts and role receipts are emitted only by `teamloop-core`; the manager cannot write them directly. The policy fixes `trustedWriterCommand=teamloop-core`, `managerMayWriteReceipts=false`, `requireManagerRoleReceipt=true`, and `historyMode=append-only-hash-chain`. Runtime verification also recomputes current primary-artifact and evidence fingerprints, so a self-hash alone is never acceptance authority.
