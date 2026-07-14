---
name: root-cause-prioritization
description: Rank reusable root fixes above repeated leaf symptoms using transparent payoff factors.
---

# Root-cause prioritization

Retain raw observations, but group them by stable rootPatternId. Rank by:

`affected items x repetition/reuse x blocking severity x confidence / estimated cost`

Prefer shared setup, schemas, adapters, central helpers, CI, and infrastructure fixes. Suppressing findings, editing counters, or deleting comments is never progress.
