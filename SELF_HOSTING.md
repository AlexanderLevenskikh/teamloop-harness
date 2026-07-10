# Self-hosting TeamLoopHarness with OpenCode

This repository includes an active project-local OpenCode adapter:

```text
AGENTS.md
opencode.jsonc
.opencode/agents/
.opencode/commands/supervised-task.md
```

## 1. Create a branch

```bash
git switch -c dogfood/teamloop-mvp-plus
```

## 2. Verify the baseline

```bash
PY=/usr/bin/python3 bash tests/run-tests.sh
```

## 3. Initialize runtime state

```bash
bash scripts/init-workspace.sh \
  --workspace .teamloop \
  --profile generic-software-task

bash scripts/validate-state.sh --workspace .teamloop
bash scripts/next-action.sh --workspace .teamloop
```

## 4. Configure the project test gate

Add a required shell gate to `.teamloop/policies/gate-policy.json`:

```json
{
  "name": "teamloop-tests",
  "type": "shell",
  "required": true,
  "command": "PY=/usr/bin/python3 bash tests/run-tests.sh",
  "timeoutSeconds": 300
}
```

Keep the existing built-in scope gate.

## 5. Start OpenCode from the repository root

Run:

```text
/supervised-task Execute docs/plans/mvp-plus-hardening.md. Start with Iteration 0 only. Stop after a green, validated checkpoint and do not begin Iteration 1 in the same run.
```

For later sessions:

```text
/supervised-task continue
```

Always inspect the diff, runtime state, and gate evidence at every checkpoint.
