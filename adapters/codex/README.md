# Codex adapter — YourAITeam MVP

Codex currently supports subagent workflows and repo-scoped custom skills/agents. The MVP emits:

- `.codex/config.toml` with bounded `max_threads` and `max_depth = 1`;
- `.codex/agents/<role>.toml` with role-specific model, reasoning effort, sandbox, and instructions;
- `.agents/skills/your-ai-team/SKILL.md` as the proposal/negotiation front door;
- an immutable accepted team contract.

The generated team should be opened in a fresh Codex task/session after materialization.
