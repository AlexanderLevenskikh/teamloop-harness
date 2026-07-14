---
name: your-ai-team
description: Propose, negotiate, accept, and run the minimum sufficient AI team for a task under an explicit token budget. Use before delegating work to multiple Codex agents.
---

1. Before spawning any subagent, run `bash scripts/your-ai-team.sh propose --backend codex --task "<task>"` or inspect an accepted proposal supplied by the user.
2. Show the user role composition, expected token range, coordination overhead, residual risks, and at least one cheaper trade-off.
3. Negotiate until the user explicitly accepts. Never interpret silence as acceptance.
4. After acceptance, use only the accepted roles: explorer, implementer, verifier.
5. Keep `max_depth = 1`; do not allow recursive hiring.
6. The delivery manager owns the result and stopping decision. Metrics are evidence, not the target.
7. Do not spawn roles that are absent from the accepted contract.
