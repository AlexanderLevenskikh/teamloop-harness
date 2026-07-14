# Changelog

## Unreleased

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
