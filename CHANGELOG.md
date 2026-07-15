# Changelog

## Unreleased — Codex parity

- default Codex custom-agent model selection now inherits the active account model instead of pinning generic `gpt-5.6`;
- optional ChatGPT model grades map to Luna/Terra/Sol;
- materialization merges existing `.codex/config.toml` instead of overwriting it;
- generated skill covers proposal, accepted-role orchestration, runtime gates, boundary lock, restart, and final handoff;
- added `codex-doctor` for config/auth/model diagnostics and safe model-pin repair;
- added Codex provenance manifest, setup guide, focused tests, and Deep Research strategy prompt.

## Unreleased

- Completed Codex adapter parity: inherited-model default, Sol/Terra/Luna opt-in, non-destructive `.codex/config.toml` merge, managed root Delivery Manager guidance in `AGENTS.md`, formal adapter contract, model doctor, and opt-in read-only live custom-agent smoke.
- Added a Deep Research strategy prompt covering YourAITeam architecture, economics, role templates, execution profiles, and interface/product paths.

- Added unified cross-platform script validation for PowerShell, Bash, Python, and command shims.
- Sentinel cache keys now include authoritative artifacts; cached non-PASS findings receive one automatic fresh retry with explicit cache diagnostics.

### Fixed

- Corrected PowerShell wrapper parameters to use `ValueFromRemainingArguments` instead of the invalid `ValueFromRemaining` attribute.
- Added a PowerShell parser regression covering all shipped `.ps1` wrappers.


## 0.5.0-alpha.1

- Added beginner-friendly English and Russian user guides covering ordinary, team-design, supervised, profile, boundary, resume, and troubleshooting workflows.
- Added runtime-enforced quality/value boundary management.
- Added deterministic measurement packets, closed decisions, finite improvement budgets, trusted histories, acceptance receipts, and advancement locks.
- Added the read-only `quality-value-manager`, reusable boundary skills, generic adapter, schemas, policies, wrappers, documentation, and adversarial coverage.

## 0.4.1

- Renamed public product surfaces to YourAITeam while preserving the legacy workspace namespace for compatibility.
