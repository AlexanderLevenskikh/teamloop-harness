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

## Iteration 2 — Layered and Impact-Aware Testing

**Original goal:** Classify tests into layers (smoke, contract, runtime, integration, full). Add `--layer`, `--affected`, `--full` flags to the test runner. Create an impact map that maps changed files to affected test layers.

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

**Classification:** **PARTIAL** — The mechanism works but was incomplete. The catalog gap meant filtered runs silently skipped 22 tests.

**Recommended future task:** Harden impact map coverage; add automatic catalog regeneration.

---

## Iteration 3 — Content-Addressed Validation Cache

**Original goal:** Create a validation cache that stores deterministic validation results keyed by SHA-256 fingerprints. Include script fingerprints to detect code changes. Make cache miss on any material input change.

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

**Classification:** **PARTIAL** — The cache infrastructure is real and functional, but the original safety contract required cryptographic binding between keys and stored results, which was absent.

**Recommended future task:** Broaden cache key inputs to include profile and policy fingerprints; add `integrity_check()` to `validate-state`.

---

## Iteration 4 — Public Release and Compatibility Hardening

**Original goal:** Add semantic versioning (`--version` flag), a backward-compatibility gate, and a schema evolution audit command.

**Delivered behavior:**
- `scripts/version.py` provides version `0.3.0`.
- `--version` flag added to `teamloop-core.py`.
- `release-info` command produces version metadata.
- `scripts/teamloop_compat.py` with `compat-check` command.
- `scripts/teamloop_schema.py` with `schema-lint` command.

**Missing behavior:**
- No actual backward-compatibility testing against prior versions (the compat-check validates current artifacts against current schemas, not historical ones).
- Schema lint does not detect breaking schema changes (e.g., required field additions).
- No release artifacts (tarball, zip) are produced by any command.
- No changelog or release notes infrastructure.

**Evidence paths:** `scripts/version.py`, `scripts/teamloop_compat.py`, `scripts/teamloop_schema.py`

**Tests:** None specific to compat or schema-lint.

**Classification:** **SCAFFOLD_ONLY** — Commands exist and produce output, but the compatibility gate does not actually test against prior versions, and schema-lint does not detect breaking changes. The versioning infrastructure is minimal.

**Recommended future task:** Implement actual version-pinning tests; make schema-lint detect required-field additions and type changes.

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

## Iteration 6 — Minimal TeamLoop Inbox Contract

**Original goal:** Create an event-driven agent notification system with inbox-send, inbox-receive, and inbox-stats commands.

**Delivered behavior:**
- `scripts/teamloop_inbox.py` exists with inbox commands.
- `schemas/inbox.schema.json` exists.
- `inbox-send`, `inbox-receive`, `inbox-stats` commands wire up in `teamloop-core.py`.

**Missing behavior:**
- The inbox is a JSONL file that stores messages. There is no delivery mechanism, no acknowledgment, no retry, no priority, and no filtering. It is a message log, not an inbox.
- No tests verify the inbox commands.
- No integration with the runtime lifecycle (messages are not triggered by state transitions).
- The schema exists but the messages are plain dictionaries without the delivery semantics described in the original goal.

**Evidence paths:** `scripts/teamloop_inbox.py`, `schemas/inbox.schema.json`

**Tests:** None.

**Classification:** **SCAFFOLD_ONLY** — A JSONL message mailbox exists with send/receive/stats commands, but it lacks all the features of an actual inbox contract: delivery guarantees, acknowledgment, filtering, and lifecycle integration.

**Recommended future task:** Implement inbox with message acknowledgment, delivery filtering, and lifecycle event triggers.

---

## Iteration 7 — Product Director L0 Advisory Mode

**Original goal:** Create a WARNING-only advisory check that audits tasks for quality issues (scope, evidence, criteria) without blocking.

**Delivered behavior:**
- `scripts/teamloop_advisory.py` exists with `advisory-check` command.
- Runs checks on current-task.json for missing scope, empty success criteria, etc.
- WARNING-only severity — never blocks.

**Missing behavior:**
- The advisory checks are basic task linting (checks if fields are present and non-empty). They do not evaluate the quality of scope descriptions, the reasonableness of success criteria, or the alignment between evidence and claims.
- No integration into the runtime lifecycle — advisory checks must be manually invoked.
- No tests for advisory-check.
- The "Product Director" framing implies product-quality judgment, which this does not deliver.

**Evidence paths:** `scripts/teamloop_advisory.py`

**Tests:** None.

**Classification:** **SCAFFOLD_ONLY** — A task linter exists with WARNING-only severity. Calling it "Product Director L0" overstates its capability. It checks for missing fields, not product quality.

**Recommended future task:** Implement quality heuristics for scope descriptions, criteria specificity, and evidence alignment. Integrate into lifecycle.

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
| I2: Layered Testing | Layer/impact-aware test runner | **PARTIAL** | 10+ |
| I3: Validation Cache | Content-addressed cache | **PARTIAL** | 20 |
| I4: Release Hardening | Versioning, compat, schema-lint | **SCAFFOLD_ONLY** | 0 |
| I5: Dogfood Guard | Full gate chain, old/new guard | **PARTIAL** | 8 |
| I6: Inbox Contract | Event-driven notifications | **SCAFFOLD_ONLY** | 0 |
| I7: Product Director L0 | Advisory quality checks | **SCAFFOLD_ONLY** | 0 |
| I8: StateStore ABC | Pluggable storage prep | **SCAFFOLD_ONLY** | 0 |
| I9: Adapter Contract | Adapter schema and verify | **SCAFFOLD_ONLY** | 0 |

**Total:** 3 PARTIAL, 5 SCAFFOLD_ONLY, 0 COMPLETE, 0 NOT_STARTED

None of the 9 iterations achieved their original contract as COMPLETE. The core runtime improvements (I1, I2, I3, I5) provide real value but are incomplete. The product-layer iterations (I6–I9) are scaffolds that need substantial work to deliver their claimed capabilities.

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
