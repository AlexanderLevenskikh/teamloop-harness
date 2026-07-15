# Codex adapter — YourAITeam

The Codex adapter provides project-scoped custom agents, a reusable skill, deterministic team contracts, model compatibility handling, and lifecycle guidance comparable to the OpenCode adapter.

## Generated files

- `.codex/config.toml` — non-destructively merged bounded `max_threads` and `max_depth = 1`;
- `.codex/agents/<role>.toml` — accepted custom agents with narrow instructions and sandbox defaults;
- `.agents/skills/your-ai-team/SKILL.md` — root Delivery Manager workflow;
- `your-ai-team-contract.json` — immutable accepted team contract;
- `your-ai-team-codex.json` — materialization provenance and model policy;
- `CODEX_SETUP.md` — setup and smoke-test instructions.

## Model policy

`--codex-model-mode inherit` is the default and safest option for ChatGPT authentication. Agent files omit the `model` key and inherit a supported model from the parent Codex task.

`--codex-model-mode chatgpt` pins:

- economy → `gpt-5.6-luna`;
- balanced → `gpt-5.6-terra`;
- premium → `gpt-5.6-sol`.

`explicit` requires model overrides for every grade used by the accepted team.

## Installation

Materialize into the repository root, not a hidden generated subdirectory:

```powershell
.\scripts\your-ai-team.ps1 materialize --proposal .teamloop\team\accepted.json --backend codex --output-dir . --codex-model-mode inherit
.\scripts\codex-doctor.ps1 --project-root .
```

Start a new trusted Codex task after materialization because project configuration and instructions are loaded at task start.

## Trust boundary

Codex sandbox settings help guide roles, but live parent permissions can override child defaults. Acceptance authority therefore remains in YourAITeam deterministic measurements, protected ledgers, boundary receipts, and final gate.

## Root Delivery Manager guidance

Materialization non-destructively appends one managed block to the repository `AGENTS.md`. Existing project guidance is preserved. This makes the root Codex thread follow the accepted-team contract automatically after a new task starts.

## Live smoke

After `codex-doctor` passes, optionally run one paid read-only compatibility check:

```powershell
.\scripts\codex-smoke.ps1 -ProjectRoot . -Role writer -Json
```

The smoke invokes `codex exec --ephemeral --sandbox read-only`, asks for exactly one accepted custom agent, checks the structured result, detects unsupported-model errors, and verifies that Git status did not change.
