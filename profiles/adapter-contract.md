# Adapter Contract

The adapter contract defines the interface between a YourAITeam adapter (e.g. OpenCode) and the Harness runtime. It specifies what commands an adapter must be able to invoke, what agents it provides, and what capabilities it supports.

## Schema

The adapter contract is defined by `schemas/adapter-contract.schema.json` (JSON Schema draft-07, `schemaVersion: 1`).

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `schemaVersion` | integer | Must be `1`. |
| `adapterId` | string | Unique identifier (e.g. `"opencode"`). |
| `version` | string | Semantic version (`"1.0.0"`). |
| `requiredCommands` | string[] | Runtime commands the adapter must invoke. |

### Optional Fields

| Field | Type | Description |
|-------|------|-------------|
| `description` | string | Human-readable adapter description. |
| `optionalCommands` | string[] | Commands the adapter may invoke if present. |
| `providedAgents` | object[] | Agent definitions with `name` and `file`. |
| `providedCommands` | object[] | Slash-commands with `name` and `file`. |
| `supportedProfiles` | string[] | Domain profiles the adapter supports. |
| `supportedTransitions` | string[] | State transitions the adapter can trigger. |
| `capabilities` | object | Feature flags: `roleDispatch`, `transitionEngine`, `gateRunner`, `researchSupport`, `memorySupport`. |
| `outputFormat` | object | Expected output format and error handling strategy. |

## Minimum Required Commands

An adapter must support at least the following runtime commands:

- `next-action` — determine the next action from workspace state
- `apply-transition` — apply a state machine transition
- `write-event` — append to the event ledger

Additional commonly required commands:

- `check-scope` — validate file changes against scope policy
- `run-gates` — execute gate checks
- `validate-state` — validate all workspace state files

## Output Format Requirements

- Runtime commands produce **structured JSON** output when `--json` is passed.
- Without `--json`, commands produce **human-readable text**.
- Exit code `0` indicates success; non-zero indicates failure.

## Error Handling Expectations

- Errors are written to **stderr** with a descriptive message.
- The `outputFormat.errorHandling` field in the adapter contract declares the adapter's expectation:
  - `"exit-code"` — only the exit code matters
  - `"stderr"` — read stderr for details
  - `"structured"` — parse JSON error fields

## Verification

Run `adapter-verify` to validate an adapter against the contract:

```bash
python scripts/teamloop-core.py adapter-verify --workspace .teamloop
python scripts/teamloop-core.py adapter-verify --workspace .teamloop --json
```

The command checks:

1. **Adapter contract exists** — `adapters/<adapterId>/adapter-contract.json` or inline config.
2. **Required commands exist** — all commands in `requiredCommands` are available in the runtime.
3. **Agents are defined** — all `providedAgents` reference existing files.
4. **Transitions supported** — all `supportedTransitions` are valid runtime transitions.
5. **Schema validation** — the adapter contract validates against `adapter-contract.schema.json`.

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All checks passed; adapter is compliant. |
| 1 | One or more checks failed; output lists violations. |

## Example: OpenCode Adapter

```json
{
  "schemaVersion": 1,
  "adapterId": "opencode",
  "version": "1.0.0",
  "description": "OpenCode adapter for YourAITeam",
  "requiredCommands": [
    "next-action", "apply-transition", "write-event",
    "check-scope", "run-gates", "validate-state",
    "validate-task", "validate-artifact",
    "memory-doctor", "run-sentinel",
    "check-guard-integrity", "final-gate"
  ],
  "providedAgents": [
    {"name": "supervisor", "file": "adapters/opencode/agents/supervisor.md"},
    {"name": "researcher", "file": "adapters/opencode/agents/researcher.md"},
    {"name": "research-lead", "file": "adapters/opencode/agents/research-lead.md"},
    {"name": "task-slicer", "file": "adapters/opencode/agents/task-slicer.md"},
    {"name": "executor", "file": "adapters/opencode/agents/executor.md"},
    {"name": "change-reviewer", "file": "adapters/opencode/agents/change-reviewer.md"},
    {"name": "gatekeeper", "file": "adapters/opencode/agents/gatekeeper.md"}
  ],
  "providedCommands": [
    {"name": "supervised-task", "file": "adapters/opencode/commands/supervised-task.md"}
  ],
  "supportedProfiles": ["generic-software-task"],
  "supportedTransitions": [
    "RUN_DISCOVERY", "RUN_EXECUTOR", "RUN_CHANGE_REVIEWER",
    "RUN_GATEKEEPER", "RUN_RESEARCHER", "RUN_RESEARCH_LEAD",
    "RUN_TASK_SLICER", "RUN_SENTINEL", "RUN_GUARD",
    "CONTINUE", "CANCEL_TASK"
  ],
  "capabilities": {
    "roleDispatch": true,
    "transitionEngine": true,
    "gateRunner": true,
    "researchSupport": true,
    "memorySupport": false
  }
}
```
