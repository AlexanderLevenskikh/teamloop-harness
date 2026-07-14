# YourAITeam MVP

YourAITeam is a task-specific AI-team composer and bounded delivery runtime.

It does **not** sell fixed `simple / standard / audit` teams. It asks a different question:

> What is the minimum sufficient team for this task, risk, quality target, and token budget?

## User journey

```text
/your-ai-team <task>
→ deterministic task analysis
→ proposed roles, grades, and token range
→ user bargains
→ explicit risk/capability trade-offs
→ user accepts
→ Codex or OpenCode team is materialized
→ only accepted roles may execute
```

## Commands

```bash
bash scripts/your-ai-team.sh propose \
  --backend codex \
  --task "Почини flaky Playwright тест" \
  --max-tokens 35000 \
  --output .teamloop/team/proposal.json

bash scripts/your-ai-team.sh negotiate \
  --proposal .teamloop/team/proposal.json \
  --request "Влезь в 25000 токенов, ревьюер только в конце" \
  --output .teamloop/team/proposal-2.json

bash scripts/your-ai-team.sh accept \
  --proposal .teamloop/team/proposal-2.json \
  --output .teamloop/team/accepted.json

bash scripts/your-ai-team.sh materialize \
  --proposal .teamloop/team/accepted.json \
  --backend codex \
  --output-dir .teamloop/generated/codex
```

For OpenCode, use `--backend opencode`. In the repository OpenCode config, call `/your-ai-team`.
In Codex, invoke `$your-ai-team` or select the skill through `/skills`.

## What “price” means

The MVP uses estimated model tokens, steps, role count, and coordination overhead. It does not pretend to know exact monetary billing, cache discounts, or organization-specific model prices.

## Role grades

- `economy`: low reasoning effort, fewer steps, narrow responsibility;
- `balanced`: default quality/cost trade-off;
- `premium`: deeper reasoning and more steps for high-risk work.

Different roles can have different grades in the same team.

## Manager invariant

`delivery-manager` cannot be removed. It owns:

- global value rather than local scores;
- budget and stopping decisions;
- acceptance of the wave/stage;
- disclosure of residual risks;
- the right to reject a green metric when the product result is not acceptable.

## Composition is not gate intensity

Legacy `fast / standard / audit` remains a safety and evidence policy. Team composition is orthogonal.
A simple landing page may use a manager and an economy vibe-coder while still requiring a concrete final check. A research task may need no developer at all.

## Current MVP boundary

Implemented:

- deterministic proposal;
- natural-language bargaining for common constraints;
- explicit acceptance;
- Codex materializer;
- OpenCode materializer;
- task matrix and regression tests.

Not yet implemented:

- live token metering from each backend;
- model-price API integration;
- hot-reload of teams inside an already running session;
- learning role estimates from historical runs;
- full replacement of the legacy fixed lifecycle.

## Worked examples

See [`examples/your-ai-team/`](examples/your-ai-team/):

- a two-role landing-page team;
- a research team with no developer;
- a Playwright bugfix proposal;
- a negotiated and accepted cheaper proposal;
- generated Codex agent files;
- generated OpenCode agent files.

## Research status

A limited landscape scan found close adjacent work, including self-organizing manager agents that dynamically hire and fire workers. The exact user-facing workflow in this MVP should therefore be treated as a **product hypothesis**, not an established novelty claim. See [`docs/YOUR_AI_TEAM_MARKET_NOTE.md`](docs/YOUR_AI_TEAM_MARKET_NOTE.md) and the ready-to-run deep-research prompt under [`docs/research/`](docs/research/).
