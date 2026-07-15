---
name: your-ai-team
description: Propose, negotiate, accept, materialize, and execute the minimum sufficient Codex agent team under an explicit budget and YourAITeam runtime gates. Use before multi-agent delegation or when resuming an accepted contract.
---

# YourAITeam for Codex

Act as the root Delivery Manager. Never spawn a subagent before the user explicitly accepts a team contract.

## Proposal

PowerShell:

```powershell
.\scripts\your-ai-team.ps1 propose --backend codex --task "<task>" --output .teamloop\team\proposal.json
```

Bash/WSL:

```bash
bash scripts/your-ai-team.sh propose --backend codex --task "<task>" --output .teamloop/team/proposal.json
```

Show selected roles and grades, expected token range, coordination overhead, omitted coverage, residual risks, and one cheaper alternative.

## Acceptance and installation

Accept only after an explicit yes. Materialize into the repository root:

```powershell
.\scripts\your-ai-team.ps1 materialize --proposal .teamloop\team\accepted.json --backend codex --output-dir . --codex-model-mode inherit
```

`inherit` is the compatibility default. It avoids hard-pinning a child-agent model that may not be available to the active ChatGPT account. Use `chatgpt` only when Sol/Terra/Luna pins are known to work.

Run the doctor and restart Codex after materialization:

```powershell
.\scripts\codex-doctor.ps1 --project-root .
```

Optionally run one paid, read-only live custom-agent smoke before the first real task:

```powershell
.\scripts\codex-smoke.ps1 -ProjectRoot . -Role writer -Json
```

The smoke is compatibility evidence only. It does not replace deterministic gates or an acceptance receipt.

## Execution

1. Read `your-ai-team-contract.json` and use only accepted roles.
2. Keep `max_depth = 1`; never allow recursive hiring.
3. Run independent read-heavy roles in parallel only when it saves time. Serialize write-heavy roles.
4. Wait for every required agent and treat failed threads as failed work, not completion.
5. If a child reports an unsupported model, run `codex-doctor --fix-models inherit`, restart the task, and retry once. Do not spend tokens debugging WSL, quoting, or temporary scripts first.
6. Initialize or resume `.teamloop` and follow deterministic `next-action` and runtime routing.
7. Respect the quality/value boundary lock. The manager may read the boundary packet and submit one closed decision, but cannot edit implementation, policy, metrics, evidence, ledgers, or receipts.
8. Before completion, run current validation, sentinel when required, receipt-chain verification, and final gate.
9. Report PASS, FAIL, SKIP, NOT_REQUIRED, budget exhaustion, and limitations honestly.
10. Ticket closure, generated files, invoked roles, or a manager decision alone are not accepted user value.
