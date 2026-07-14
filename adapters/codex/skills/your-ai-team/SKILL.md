---
name: your-ai-team
description: Propose, negotiate, accept, and materialize the minimum sufficient Codex or OpenCode agent team for a software task under an explicit token budget. Use before multi-agent delegation or when the user asks to reduce agent cost.
---

# YourAITeam

1. Never spawn subagents before the user accepts a team proposal.
2. Run:
   `bash scripts/your-ai-team.sh propose --backend codex --task "<task>" --output .teamloop/team/proposal.json`
3. Show the user:
   - selected roles and grades;
   - expected token range;
   - coordination overhead;
   - responsibilities deliberately not purchased;
   - residual risks;
   - one cheaper alternative when possible.
4. Translate bargaining into `team-negotiate`. Examples:
   - `--request "Влезь в 30000 токенов"`
   - `--request "Убери исследователя, принимаю риск"`
   - `--request "Ревьюер только в конце"`
5. Accept only after an explicit yes:
   `bash scripts/your-ai-team.sh accept --proposal ... --output .teamloop/team/accepted.json`
6. Materialize the accepted contract:
   `bash scripts/your-ai-team.sh materialize --backend codex --proposal ... --output-dir .teamloop/generated/codex`
7. Use only roles listed in the accepted contract. Keep subagent depth at 1.
8. The delivery manager owns the global result, stopping decision, and the right to reject misleading local metrics.
