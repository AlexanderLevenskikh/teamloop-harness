---
name: adaptive-boundary-sizing
description: Decide when a bounded unit should be improved once, split, stopped honestly, or escalated.
---

# Adaptive boundary sizing

- Improve once when one high-payoff root candidate fits the remaining finite budget.
- Split when the unit mixes independent roots or cannot be remeasured coherently after one change.
- Stop only after budget exhaustion or the configured no-progress threshold.
- Ask a human for policy, product, or risk choices the runtime cannot make safely.
- Fast mode reduces cycles, never hard quality thresholds.
