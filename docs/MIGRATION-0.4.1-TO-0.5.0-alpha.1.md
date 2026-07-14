# Migration: 0.4.1 -> 0.5.0-alpha.1

Version 0.5.0-alpha.1 adds optional quality/value boundary management.

## Compatibility

Existing `.teamloop` workspaces and tasks without boundary contracts retain the 0.4.1 gate-to-checkpoint path. Fresh workspaces receive `policies/boundary-policy.json` and schema-valid profile defaults.

## New lifecycle states

- `NEEDS_BOUNDARY_DECISION`
- `BOUNDARY_STOPPED`

Consumers that validate `team-state.json`, event types, run results, or gate results must update to the bundled schemas.

## Adopting the feature

1. upgrade scripts, schemas, profiles, policies, roles, and adapters together;
2. validate the workspace;
3. create a boundary contract for a bounded task before gate completion;
4. run deterministic gates;
5. route `RUN_QUALITY_VALUE_MANAGER`;
6. record a runtime-validated decision;
7. verify the acceptance lock before advancement.

Do not copy only the manager role. The runtime, policy, schemas, and trusted receipt chain are required for safety.

## Rollback

Workspaces with active boundary state should be completed, explicitly stopped, or archived before rolling back to 0.4.1. The older runtime does not understand the new lifecycle phases and receipts.
