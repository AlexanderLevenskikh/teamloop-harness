# YourAITeam — market and research note

Date: 2026-07-14

## Claim boundary

This is a limited landscape scan, not a proof of global novelty.

Related work already exists:

- **Agyn** models autonomous software engineering as a specialized team with coordination, research, implementation, review, isolated sandboxes, and a development methodology. It supports configurable teams, but its published framing is an autonomous engineering organization rather than a user-facing staffing negotiation before execution.
- **TheBotCompany** describes self-organizing teams in which manager agents dynamically hire, assign, and fire workers based on project needs. This is close to the dynamic-composition hypothesis and means we must not claim that adaptive hiring itself is unprecedented.
- Multi-agent and organizational-design research has studied heterogeneous team composition, costs, and changing team membership for years, although not necessarily as an interactive coding-agent product.

The current product hypothesis is narrower:

> Before execution, propose the minimum sufficient AI team for one task; expose role-level token estimates and coordination overhead; let the user bargain; explain lost coverage and residual risk; require explicit acceptance; then materialize only the accepted team for the chosen agent runtime.

The initial differentiators to test are:

1. **Human-in-the-loop staffing before spend**, rather than autonomous fan-out first.
2. **Negotiation as a real contract change**, not a cosmetic pricing selector.
3. **Role-level quality/cost grades** in one team.
4. **Coordination overhead shown separately** from direct worker estimates.
5. **Coverage and residual-risk disclosure** after every downgrade/removal.
6. **Backend-independent contract** with Codex and OpenCode materializers.
7. **A non-removable result owner** who may disagree with local metrics.
8. **No fixed “small / medium / audit” team bundles.** Safety policy and staffing remain orthogonal.

## Runtime fit

### Codex

Official Codex documentation supports subagent workflows and custom agent files with per-agent model, reasoning effort, and sandbox settings. It also provides global thread/depth controls and explicitly warns that subagents consume more tokens than comparable single-agent work. This makes team economics an operational concern rather than a metaphor.

Sources:

- https://developers.openai.com/codex/multi-agent/
- https://developers.openai.com/codex/skills/

### OpenCode

Official OpenCode documentation supports primary agents and subagents, project-local Markdown agents, maximum agentic steps, per-tool permissions, and task allowlists. This is sufficient to materialize an accepted team and prevent the manager from invoking roles that were not hired.

Source:

- https://opencode.ai/docs/agents/

## Related research

- Agyn: https://arxiv.org/abs/2602.01465
- TheBotCompany: https://arxiv.org/abs/2603.25928
- Dynamic team composition and coordination: https://arxiv.org/abs/2401.05832

## What still needs deep research

- systematic product and open-source landscape review;
- whether any coding-agent UI already supports pre-run staffing negotiation;
- pricing semantics across subscriptions, API tokens, cached tokens, local models, and corporate gateways;
- empirical evidence on when extra roles improve acceptance rate enough to pay for themselves;
- calibration methods for token/time ranges;
- behavioral economics of user bargaining and risk disclosure;
- governance implications when a user removes safety-critical coverage;
- whether “delivery manager” should be a role, a deterministic policy layer, or a hybrid.
