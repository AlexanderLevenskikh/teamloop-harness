# Deep Research Prompt — YourAITeam and Task-Specific AI Team Economics

## Role

Act as an interdisciplinary research team combining expertise in:

- agentic software engineering and coding agents;
- multi-agent systems and organizational design;
- engineering management and project delivery;
- LLM inference economics and FinOps;
- human-computer interaction and negotiation UX;
- developer tools and open-source ecosystems;
- product strategy and market analysis;
- safety, governance, and evaluation.

## Research object

Evaluate the following product hypothesis skeptically:

> A user describes a task. Before any expensive execution, the system proposes the minimum sufficient AI team, assigns role-level quality/cost grades, estimates direct token use and coordination overhead, exposes risks, allows the user to negotiate the team and budget, requires explicit acceptance, and then materializes only the accepted roles in runtimes such as Codex and OpenCode.

The system is not a fixed multi-agent team and not a three-tier package. A task may need:

- only a manager and a cheap vibe-coder;
- a researcher without a developer;
- an implementer and final verifier;
- or a larger high-risk team.

A non-removable delivery manager owns the global result, budget, stopping decision, and acceptance. Local metrics are evidence, not the objective.

## Questions

### 1. Novelty and adjacent work

Find products, repositories, papers, patents, demos, and internal-platform descriptions related to:

- dynamic agent-team formation;
- managers that hire/fire subagents;
- cost-aware routing and model selection;
- task-specific role composition;
- agent marketplaces or bidding;
- human negotiation over agent delegation;
- token budgets and pre-execution estimates;
- risk/coverage contracts after removing roles;
- coding-agent team generators for Codex, OpenCode, Claude Code, Cursor, or similar tools.

Distinguish clearly between:

- fixed teams;
- dynamically selected workflows;
- autonomous self-organization;
- model routing;
- user-facing staffing negotiation;
- exact or approximate equivalents to the full hypothesis.

Do not claim novelty merely because terminology differs.

### 2. User and business value

Identify target users and jobs-to-be-done:

- individual developers with subscription limits;
- small teams with API budgets;
- enterprise engineering organizations;
- non-developers building prototypes;
- platform teams managing many coding agents.

Test whether the negotiation metaphor improves decisions or adds friction. Explore alternative metaphors such as staffing plan, execution quote, risk-adjusted plan, or resource contract.

### 3. Economics

Develop a rigorous model for:

- direct role cost;
- coordination overhead;
- context duplication;
- model/reasoning-effort choice;
- parallelism and latency;
- retries and failure probability;
- verification cost;
- expected rework avoided;
- business value and risk exposure.

Explain which values can be estimated before execution and which require historical calibration. Avoid pretending that provider token counts map cleanly to subscription limits.

### 4. Minimum viable product

Assess the current MVP shape:

1. deterministic task classification;
2. role catalogue;
3. economy/balanced/premium grades;
4. estimated token range;
5. explicit coordination overhead;
6. natural-language bargaining;
7. residual-risk disclosure;
8. explicit acceptance;
9. Codex materialization;
10. OpenCode materialization.

Recommend the smallest experiment capable of falsifying the core hypothesis.

### 5. Evaluation design

Design a comparative test across at least these task classes:

- simple landing page;
- documentation rewrite;
- bounded bug fix;
- dependency upgrade;
- architecture research;
- high-risk migration;
- security-sensitive change.

Compare:

- single strong agent;
- fixed small team;
- fixed large team;
- YourAITeam initial proposal;
- YourAITeam negotiated proposal.

Measure:

- actual tokens or best available usage proxy;
- elapsed time;
- user interventions;
- task completion;
- test/gate outcome;
- review findings;
- residual defects;
- accepted-result rate;
- cost per accepted result;
- calibration error of the estimate;
- coordination overhead.

### 6. Safety and governance

Analyze what should happen when a user bargains away:

- reviewer;
- verifier;
- security review;
- manager;
- rollback or sandbox guarantees.

Distinguish removable expertise from non-negotiable runtime invariants. Consider whether some tasks should refuse a low budget rather than silently downgrade.

### 7. Architecture

Recommend a backend-neutral contract and adapter architecture for Codex and OpenCode, with a path to other runtimes. Consider:

- immutable accepted-team contract;
- role/model/effort/step/sandbox mapping;
- actual usage collection;
- session reload behavior;
- task routing;
- recursion limits;
- role spawning authorization;
- provenance and reproducibility.

## Required output

Produce:

1. executive conclusion;
2. landscape matrix with links and dates;
3. closest analogues and exact differences;
4. falsifiable hypotheses;
5. recommended MVP changes;
6. experiment protocol;
7. economic model;
8. risk register;
9. product-positioning options;
10. go / revise / stop recommendation.

Use primary sources whenever possible. Separate confirmed facts, reasonable inferences, and speculation. Include negative evidence and reasons the idea may fail.
