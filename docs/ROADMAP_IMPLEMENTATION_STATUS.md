# Roadmap Implementation Status

This document honestly classifies the previously claimed Iterations 1–9 from the `campaign/runtime-consolidation-productization` branch against their original contracts.

**Classification dates:** July 12, 2026

**Source of truth:** Checked-out Git HEAD at `fix/runtime-consolidation-corrective`. A deliverable exists only when it is present in the checked-out Git HEAD and verified by passing tests.

## Classification Scale

| Class | Meaning |
|-------|---------|
| **COMPLETE** | All original contract requirements delivered and tested |
| **PARTIAL** | Core mechanism delivered but missing key features, tests, or integration |
| **SCAFFOLD_ONLY** | Module or command exists but does not fulfill the claimed capability |
| **NOT_STARTED** | No evidence in HEAD |

---

## Iteration 1 — Single Validation Host

**Original goal:** Create a shared `WorkspaceContext` that lazy-loads state, schemas, profiles, and backlog. Migrate command handlers to use it, eliminating duplicate file-reading logic across commands.

**Delivered behavior:**
- `scripts/teamloop_context.py` exists with `WorkspaceContext` class.
- Lazy-loading of `state`, `schemas`, `backlog`, `current_task`, `active_profile`, `run_ledger`.
- 6 command handlers migrated: `validate-state`, `sentinel`, `check-guard-integrity`, `memory-doctor`, `final-gate`, `check-scope`.
- 10 focused tests (test_77-86 area).

**Missing behavior:**
- Not all command handlers are migrated. Many commands in `teamloop-core.py` still use direct `read_json`/`read_jsonl` calls instead of `host.state_safe`, `host.schemas`, etc.
- No performance benchmark showing reduced I/O.
- `WorkspaceContext` does not implement the optional `state_store` abstraction completely — the `_state_store` attribute was added in I8 but many code paths bypass it.

**Evidence paths:** `scripts/teamloop_context.py`, tests in `run-tests.sh`

**Tests:** ~10 (WorkspaceContext tests)

**Classification:** **PARTIAL** — The host exists and is used by the validation-heavy commands, but the original promise was to make it the single access point for ALL commands, which has not been achieved.

**Recommended future task:** Audit remaining command handlers and migrate direct file reads through `WorkspaceContext`.

---

## Iteration 2 — Layered and Impact-Aware Test Execution

**Original goal:** Provide smoke, contract, runtime, integration, end-to-end, and full test layers; deterministic changed-path impact mapping; a complete mandatory checkpoint suite; safe sharding; and completeness evidence proving every required test ran exactly once.

**Delivered behavior:**
- `tests/test-layers.json` exists with per-test layer assignments.
- `tests/impact-map.json` exists with file-to-layer mappings.
- `--layer`, `--affected`, `--full`, `--list-layers` flags implemented in `run-tests.sh`.
- `test-select` command in `teamloop-core.py` handles layer resolution.
- 10 layered test execution tests.

**Missing behavior:**
- The catalog was incomplete (tests 206–227 missing) until this corrective pass.
- Impact map default fallback is `["smoke", "contract"]` which may be too narrow for some protected path changes.
- No automatic catalog consistency check existed (added in this corrective pass).

**Evidence paths:** `tests/test-layers.json`, `tests/impact-map.json`, `tests/run-tests.sh`

**Tests:** 10 (layer selection tests) + catalog consistency

**Classification:** **PARTIAL** — Layer selection and impact-aware execution are real and useful, and catalog completeness is now checked. Safe isolated parallel sharding, end-to-end coverage, and stronger impact-map guarantees are still missing.

**Recommended future task:** Harden impact map coverage; add automatic catalog regeneration.

---

## Iteration 3 — Honest Content-Addressed Validation Cache

**Original goal:** Provide deterministic reuse of cache-safe checks through semantic content-addressed keys, safe invalidation on every material input, explainable hit/miss behavior, and a guarantee that stale or partially protected PASS results are never reused.

**Delivered behavior:**
- `scripts/teamloop_cache.py` exists with `ValidationCache` class.
- `build_key()` computes SHA-256 from check name, input fingerprints, schema fingerprints, and script fingerprints.
- Cache TTL, LRU eviction, read-only mode implemented.
- Integrated into `validate-state` and `sentinel` commands.
- 14 cache tests existed (expanded to 20 in corrective pass).

**Missing behavior:**
- `_verify_entry_integrity` was toothless — only checked that `cacheKey` is valid hex and `result` exists. Result tampering (flipping PASS↔FAIL) was undetectable. (Fixed in corrective pass.)
- Malformed JSONL lines were silently dropped with no diagnostic. (Fixed in corrective pass.)
- Cache does not include profile, policy, or protected-paths fingerprints in `build_key()` — only script fingerprints. Changes to these files do not invalidate cache entries.
- `IMPLEMENTATION_VERSION` is stored but never checked on cache lookup.
- `cmd_validate_state` never calls `cache.integrity_check()`.

**Evidence paths:** `scripts/teamloop_cache.py`, `schemas/validation-cache.schema.json`

**Tests:** 14 original + 6 added in corrective pass

**Classification:** **PARTIAL** — The cache is content-addressed, versioned, TTL-bound, integrity-protected, and rejects legacy or corrupted entries. Broader dependency fingerprints and richer operator tooling are still incomplete, so the original contract is not COMPLETE.

**Recommended future task:** Broaden cache key inputs to include profile and policy fingerprints; add `integrity_check()` to `validate-state`.

---

## Iteration 4 — Public Release and Compatibility Hardening

**Original goal:** Provide runtime/workspace/protocol versioning with workspace migration and dry-run support. Include install/update integrity, partial-update detection, top-level doctor, diagnostic bundle, install/upgrade/downgrade/fresh-workspace smoke tests, and a compatibility matrix.

**Delivered behavior:**
- `scripts/install.sh` and `scripts/release-package.sh` exist for ZIP-based distribution.
- Install script restores executable permissions after ZIP extraction.

**Missing behavior:**
- No runtime/workspace/protocol versioning scheme.
- No workspace migration with dry-run or migration recovery evidence.
- No partial-update detection.
- No top-level doctor command beyond `memory-doctor`.
- No diagnostic bundle generation.
- No install/upgrade/downgrade/fresh-workspace smoke tests.
- No compatibility matrix.
- Cache integrity (resultHash) covers only the result body, not all semantic fields. (This was mistakenly scoped to I4; it is actually I3 content-addressed cache work.)

**Evidence paths:** `scripts/install.sh`, `scripts/release-package.sh`

**Tests:** 2 (install/package tests)

**Classification:** **SCAFFOLD_ONLY** — Only the basic ZIP+install flow exists. The original goal required versioning, migration, doctor, and compatibility hardening. The cache work described in earlier iterations belongs to I3, not I4.

**Recommended future task:** Implement runtime/workspace/protocol versioning; workspace migration with dry-run; top-level doctor; install smoke tests.

---

## Iteration 5 — Structured Dogfood and Old/New Runtime Guard

**Original goal:** Create a `dogfood` command that runs the full gate chain. Implement an old/new runtime comparison guard.

**Delivered behavior:**
- `scripts/teamloop_dogfood.py` exists with `dogfood` command.
- Runs validate-state, check-scope, run-gates, run-sentinel, check-guard-integrity, memory-doctor, final-gate.
- `--json` and `--old-new-compare` flags.
- 8 focused dogfood tests.

**Missing behavior:**
- The "old/new runtime guard" is NOT a real guard. `--old-new-compare` runs the same Python runtime twice and compares output. Since there's no "old" version installed or available, both invocations use the same code. This does not test backward compatibility between different runtime versions.
- Dogfood does not run actual test suites — it only runs the gate chain commands.
- No isolation between old and new invocations (same workspace, same state).

**Evidence paths:** `scripts/teamloop_dogfood.py`

**Tests:** 8 (dogfood tests)

**Classification:** **PARTIAL** — The dogfood command is real and useful for checking the gate chain. The "old/new runtime guard" is a scaffold — it produces a comparison structure but both sides use identical code.

**Recommended future task:** Implement actual dual-version comparison with pinned runtime binaries.

---

## Iteration 6 — Minimal TeamLoop Inbox Contract and Read-Only Prototype

**Original goal:** Build a read-only control-plane view over repositories and workspaces exposing: active runs/tasks; next action; blockers; HUMAN_REQUIRED status; reviewer/watchdog/sentinel findings; execution profile; no-progress state; final-gate results; reviewed evidence and diffs. The implemented JSONL agent mailbox is a separate scaffold, not the original Minimal Inbox.

**Delivered behavior:**
- `scripts/teamloop_inbox.py` provides a JSONL agent mailbox for inter-agent communication.
- Workspace integrity checks partially implemented in the sentinel inspection (state consistency, scope policy weakening detection).
- Sentinel report produced with structured findings and severity classification.
- Guard integrity checks for protected path modifications.
- Memory subsystem validation via `memory-doctor`.

**Missing behavior:**
- No read-only control-plane view over repositories/workspaces.
- No active runs/tasks query interface.
- No next-action query from external view.
- No blockers/HUMAN_REQUIRED external view.
- No reviewer/watchdog/sentinel findings aggregation.
- No execution profile, no-progress, or final-gate query.
- No reviewed evidence and diffs external view.
- The JSONL mailbox is not the original Minimal Inbox contract.

**Evidence paths:** `scripts/teamloop_inbox.py`, `scripts/teamloop-core.py` (sentinel, guard, memory-doctor commands)

**Tests:** None specific to the inbox contract.

**Classification:** **SCAFFOLD_ONLY** — The JSONL mailbox provides basic inter-agent communication but does not fulfill the original Minimal Inbox contract of a read-only control-plane view. The resilience checks (workspace integrity, sentinel staleness) are partially implemented in the sentinel command, but the original Inbox goal was different.

**Recommended future task:** Implement the read-only control-plane view; add active runs/tasks query; implement next-action, blockers, and findings aggregation.

---

## Iteration 7 — Product Director L0 Advisory Mode

**Original goal:** Recommend the next bounded task with expected value, urgency, risk, suggested execution profile, prerequisites, dependencies, alternatives, uncertainty quantification, human confirmation requirement, and comparison with runtime `next-action`. A task lint check is only a scaffold.

**Delivered behavior:**
- `scripts/teamloop_advisory.py` provides an `advisory-check` command.
- Produces structured JSON with check categories for task definition, scope, evidence, and blockers.
- `--json` flag for machine-readable output.
- The `advisory-check` performs a task lint/quality assessment.

**Missing behavior:**
- No recommendation engine for the next bounded task.
- No expected value, urgency, or risk scoring.
- No suggested execution profile recommendation.
- No prerequisite/dependency analysis.
- No alternatives generation.
- No uncertainty quantification.
- No human confirmation requirement assessment.
- No comparison with runtime `next-action`.
- The task lint check is a scaffold, not the original Product Director advisory.

**Evidence paths:** `scripts/teamloop_advisory.py`

**Tests:** None specific to L0 advisory.

**Classification:** **SCAFFOLD_ONLY** — The `advisory-check` command provides a task lint that validates structure, but does not fulfill the original goal of recommending the next bounded task with expected value, risk, profile advice, and alternatives. It is a scaffold for the advisory mode.

**Recommended future task:** Implement recommendation engine with value/urgency/risk scoring; add prerequisite/dependency analysis; generate alternatives; compare with runtime next-action.

---

## Iteration 8 — StateStore Abstraction Preparation

**Original goal:** Create a `StateStore` ABC and a `FileSystemStateStore` implementation to prepare for pluggable storage backends.

**Delivered behavior:**
- `scripts/teamloop_statestore.py` exists with `StateStore` ABC and `FileSystemStateStore`.
- `WorkspaceContext` optionally accepts a `state_store` parameter.

**Missing behavior:**
- The `StateStore` ABC has basic read/write/exists methods. There is no transaction support, no locking, no consistency model.
- `FileSystemStateStore` is a thin wrapper around `open()`/`json.load()`/`json.dump()`. It adds no value over direct filesystem access.
- `WorkspaceContext` only uses `_state_store` in a few places; most access is still direct filesystem I/O.
- No alternative implementation exists (e.g., memory store, network store).
- No tests for StateStore.

**Evidence paths:** `scripts/teamloop_statestore.py`

**Tests:** None.

**Classification:** **SCAFFOLD_ONLY** — The ABC exists but the only implementation is a minimal file wrapper that adds no behavior beyond direct filesystem access. The "abstraction preparation" is not preparation for anything useful — the ABC has no transaction or consistency guarantees.

**Recommended future task:** Define the StateStore contract with transaction boundaries and atomicity guarantees. Implement a memory store for testing.

---

## Iteration 9 — Adapter Contract Foundation

**Original goal:** Create a schema and contract for adapter implementations, with an `adapter-verify` command.

**Delivered behavior:**
- `schemas/adapter-contract.schema.json` exists.
- `adapters/opencode/adapter-contract.json` exists (instance).
- `profiles/adapter-contract.md` exists (documentation).
- `adapter-verify` command validates adapter contracts against the schema.

**Missing behavior:**
- The adapter contract schema describes adapter metadata (name, version, capabilities). It does not define a runtime API that adapters must implement.
- There is no adapter loading mechanism, no adapter registry, and no adapter discovery.
- `adapter-verify` validates the JSON file against the schema but does not test the adapter's behavior.
- No tests for adapter-verify.

**Evidence paths:** `schemas/adapter-contract.schema.json`, `adapters/opencode/adapter-contract.json`

**Tests:** None.

**Classification:** **SCAFFOLD_ONLY** — A schema and one instance file exist. The `adapter-verify` command validates JSON structure but does not verify adapter behavior. No adapter loading or runtime integration exists.

**Recommended future task:** Define the adapter runtime API. Implement adapter loading and discovery. Create behavioral verification tests.

---

## Summary

| Iteration | Claim | Classification | Tests |
|-----------|-------|---------------|-------|
| I1: Single Validation Host | WorkspaceContext shared host | **PARTIAL** | ~10 |
| I2: Layered Test Execution | Layer/impact-aware test runner | **PARTIAL** | 10+ |
| I3: Honest Validation Cache | Content-addressed cache | **PARTIAL** | 20+ |
| I4: Public Release and Compatibility Hardening | Versioning, migration, doctor, compatibility | **SCAFFOLD_ONLY** | 2 |
| I5: Dogfood Guard | Full gate chain, old/new guard | **PARTIAL** | 8 |
| I6: Minimal TeamLoop Inbox Contract and Read-Only Prototype | Read-only control-plane inbox | **SCAFFOLD_ONLY** | 0 |
| I7: Product Director L0 Advisory Mode | Bounded-task recommendation with risk/profile advice | **SCAFFOLD_ONLY** | 0 |
| I8: StateStore ABC | Pluggable storage prep | **SCAFFOLD_ONLY** | 0 |
| I9: Adapter Contract | Adapter schema and verify | **SCAFFOLD_ONLY** | 0 |

**Total:** 4 PARTIAL, 5 SCAFFOLD_ONLY, 0 COMPLETE, 0 NOT_STARTED

None of the 9 iterations achieved their original contract as COMPLETE. The core runtime improvements (I1, I5) provide real value but are incomplete. I1, I2, I3, and I5 provide real but incomplete mechanisms. I4 and I6–I9 remain scaffolds that need substantial work to deliver their original capabilities.

## Corrective Pass Additions (not part of the original campaign)

These were added during the `fix/runtime-consolidation-corrective` pass and ARE considered COMPLETE:

| Fix | Classification | Tests |
|-----|---------------|-------|
| Wrapper permissions (100755) | **COMPLETE** | verified by git ls-files |
| Test catalog completeness (206-234) | **COMPLETE** | test_229 |
| Cache result integrity hash | **COMPLETE** | test_229 |
| Cache malformed JSONL threshold | **COMPLETE** | existing + test_212 |
| Lifecycle integrity gating | **COMPLETE** | test_230-234 |
| CORRECTIVE_WORK_REQUIRED state | **COMPLETE** | test_230-234 |
