# TeamLoop Harness

Reusable delivery harness for supervised agent teams.

## Core Invariants

```
MANUAL_REVIEW ≠ HUMAN_REQUIRED
SAFE_CHECKPOINT ≠ DONE
RESEARCH_COMPLETE ≠ DONE
```

An agent team must not hand unfinished work back to the user just because a subagent says "developer action" or "manual review". The supervisor must route uncertainty back into research, review, task slicing, execution, or gate repair. Human handoff is allowed only when there is an explicit classified blocker with evidence and concrete questions.

## The Team Loop

```
discover → plan → execute → review → research → slice → gate → repair → continue
```

## Core Principles

### 1. Supervisor owns the work state

The supervisor is not a passive router. It must:
- read current state;
- determine the next action;
- select the correct role;
- refuse premature completion;
- route failed role outputs back into the team;
- stop for humans only with a classified blocker.

### 2. Every role output must be accepted by another role or a gate

No role may unilaterally declare final success.

### 3. MANUAL_REVIEW is not HUMAN_REQUIRED

`MANUAL_REVIEW` means agent review is needed with source truth, target evidence, and local context.

`HUMAN_REQUIRED` is only valid when there is a blocker such as:
- missing credentials;
- missing source truth;
- product behavior ambiguity;
- destructive action requiring approval;
- scope policy forbids required edit;
- legal/security/ownership decision.

### 4. SAFE_CHECKPOINT is not DONE

A safe checkpoint means the state is honest and verified, not that all work is complete.

### 5. Research must pass review

A research report is not accepted until research-lead verifies counts, evidence, contradictions, actionability, human/agent classification, and recommended bounded tasks.

## Workspace

Default workspace: `.teamloop/`

Key files:
- `.teamloop/state/team-state.json` — current team state
- `.teamloop/state/events.jsonl` — append-only event ledger
- `.teamloop/state/backlog.jsonl` — task backlog
- `.teamloop/state/current-task.json` — currently active task
- `.teamloop/policies/scope-policy.json` — scope guard rules
- `.teamloop/policies/gate-policy.json` — gate execution rules
- `.teamloop/profiles/active-profile.json` — active domain profile

## Completion Semantics

State may become `DONE` only when:
- backlog is empty or all tasks are DONE/CANCELLED;
- required gates PASS or are explicitly skipped with accepted blocker;
- no open HUMAN_DECISION_REQUIRED blockers;
- final report exists;
- state validation passes.

State may become `HUMAN_DECISION_REQUIRED` only when:
- a blocker record exists in `.teamloop/state/blockers.jsonl`;
- blocker category is from the allowed list;
- evidence exists;
- questionsForHuman are present;
- supervisor explains why the agent loop cannot continue.

## The team must not confuse uncertainty with human ownership.

If work is unclear, route to research.
If research is weak, route to research review.
If research is actionable, route to task slicing.
If a task is too large, slice it smaller.
If implementation fails, route to review or repair.
If gates fail, classify and repair.
Only stop for a human when a blocker is explicitly classified with evidence and questions.

```
MANUAL_REVIEW is not HUMAN_REQUIRED.
SAFE_CHECKPOINT is not DONE.
RESEARCH_COMPLETE is not DONE.
```

## Scripts

| Script | Description |
|--------|-------------|
| `init-workspace` | Initialize `.teamloop/` workspace |
| `write-event` | Append event to `events.jsonl` |
| `next-action` | Determine next action from state |
| `check-scope` | Validate file changes against scope policy |
| `run-gates` | Execute gate checks from policy |
| `validate-state` | Validate all state files |

## Profiles

Profiles define domain-specific behavior: discovery questions, gate commands, allowed roots, forbidden actions, role prompt overrides, and task slicing strategy.

Default profile: `generic-software-task`
