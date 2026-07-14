# YourAITeam User Guide

A practical guide to using YourAITeam without reading the full runtime specification first.

> New here? Start with **OpenCode + `standard` profile**. Use ordinary Build mode for tiny edits, `/your-ai-team` when you want to negotiate the team and budget, and `/supervised-task` when you want the runtime to control delivery.

## 1. The three things people usually call a “mode”

YourAITeam has three independent layers. They are related, but they are not the same setting.

### A. Work mode

| Mode | What it does | Best for |
|---|---|---|
| **Ordinary Build mode** | A normal coding-agent conversation. No `.teamloop` lifecycle is required. | Tiny edits, experiments, repairing YourAITeam itself. |
| **Team design mode** (`/your-ai-team`) | Proposes roles, grades, and a token budget. It does not start implementation until you accept. | Expensive, unclear, or negotiable tasks. |
| **Supervised delivery mode** (`/supervised-task`) | Runs the task through the durable `.teamloop` lifecycle, gates, review, sentinel, and final checks. | Multi-step, risky, or long-running work. |

### B. Execution profile

The profile controls ceremony and the finite improvement budget. It never disables hard quality checks.

| Profile | Typical behavior | Boundary improvement budget |
|---|---|---:|
| `fast` | Minimum team and trigger-driven review. | 2 cycles |
| `standard` | Executor plus reviewer; recommended default. | 4 cycles |
| `audit` | Strongest review, watchdog, and sentinel requirements. | 6 cycles |

A requested `fast` run may be escalated when the runtime detects protected scope, serious findings, or repeated no-progress.

### C. Boundary decision

After deterministic gates pass, an opt-in quality/value boundary may still prevent advancement. The boundary manager chooses one runtime-validated outcome:

- `ACCEPT_BOUNDARY` — the bounded result is acceptable;
- `ACCEPT_WITH_RECORDED_SOFT_DEBT` — acceptable with an explicit non-blocking debt list;
- `IMPROVE_CURRENT_BOUNDARY` — perform exactly one highest-value bounded improvement;
- `SPLIT_CURRENT_BOUNDARY` — the work is too broad and should be divided;
- `STOP_BUDGET_EXHAUSTED` — stop honestly after the permitted budget or no-progress limit;
- `REQUEST_HUMAN_DECISION` — a real user decision is required.

The manager cannot override a hard gate or issue its own acceptance receipt.

---

## 2. Requirements

Recommended:

- Python 3.10 or newer;
- Git;
- OpenCode for the interactive workflow;
- PowerShell 7 (`pwsh`) on Windows;
- Bash on Linux/macOS, Git Bash, or WSL.

Check the tools:

```powershell
python --version
git --version
pwsh --version
opencode --version
```

On Linux/WSL:

```bash
python3 --version
git --version
opencode --version
```

After extracting the ZIP on Linux/WSL, restore executable permissions:

```bash
bash scripts/install.sh
```

PowerShell `.ps1` wrappers do not require Unix executable bits.

---

## 3. Put YourAITeam in the project root

For the alpha release, place the YourAITeam files in the root of the repository where you will start OpenCode.

The same directory must directly contain:

```text
AGENTS.md
opencode.jsonc
.opencode/
scripts/
schemas/
templates/
tests/
```

Copy the complete YourAITeam package into the project root instead of copying only selected wrappers. `templates/` is required to initialize a fresh `.teamloop` workspace, and `tests/` is required to verify the installation and future updates. If either directory is missing, initialization or validation may be incomplete even when the individual scripts are present.

Do not start OpenCode one directory above or below this root.

Verify:

```powershell
Get-Location
Get-ChildItem -Force
Get-ChildItem -Force .opencode\agents, .opencode\commands
opencode agent list
```

Restart OpenCode after changing agents or commands.

---

## 4. Initialize the durable workspace

The runtime state lives in `.teamloop/`.

### Windows

```powershell
.\scripts\init-workspace.ps1 -Workspace ".teamloop" -Profile "generic-software-task"
.\scripts\validate-state.ps1 -Workspace ".teamloop"
```

### Bash / WSL

```bash
bash scripts/init-workspace.sh --workspace .teamloop --profile generic-software-task
bash scripts/validate-state.sh --workspace .teamloop
```

Do not manually edit runtime-owned JSON/JSONL files under `.teamloop/state`. Use runtime commands and transitions.

`generic-software-task` is a **domain/workspace profile**. It is different from the execution profiles `fast`, `standard`, and `audit`.

---

## 5. Recommended OpenCode user journey

### Path 1 — a tiny ordinary task

Use the **Build** agent.

Examples:

- rename a variable;
- inspect a file;
- make a small local edit;
- repair YourAITeam itself without self-hosting.

The current alpha configuration starts with `orchestrator` as the default primary agent. Switch to **Build** with `Tab` before sending the prompt when you do not want the runtime lifecycle.

A slash command is not a permanent toggle. If ordinary prompts still continue an old supervised run, the active primary agent is probably still `orchestrator`, or the same `.teamloop` state is being resumed.

### Path 2 — design and negotiate the team

In OpenCode:

```text
/your-ai-team Fix the flaky Playwright test without spending more than 25k tokens
```

The team manager should propose:

- the minimum sufficient roles;
- role grades (`economy`, `balanced`, `premium`);
- estimated token range;
- risks caused by removing or downgrading roles.

You can answer naturally:

```text
Keep the reviewer only for the final review and fit within 20k tokens.
```

Then explicitly accept the proposal.

Important: team design does **not** automatically mean that work has begun. In the alpha release, materialized team files are artifacts; an already running OpenCode session does not hot-reload a newly generated team.

### Path 3 — supervised delivery

Use:

```text
/supervised-task Implement the requested change using the standard profile.
```

Or:

```text
/supervised-task Continue the current run.
```

The orchestrator should obey the runtime:

```text
next-action
→ one bounded role action
→ scope and contract checks
→ progress measurement
→ review/gates when required
→ sentinel/final gate before handoff
```

It must not dispatch every role automatically or repeat the same no-progress action forever.

---

## 6. Choosing a profile

Use `standard` unless you have a reason not to.

### `fast`

Good for:

- small, well-understood changes;
- low-risk repository scope;
- tasks with strong deterministic tests.

It reduces ceremony, not quality. Final hard checks remain enabled.

### `standard`

Good for:

- normal bug fixes and features;
- changes that benefit from a reviewer;
- most day-to-day work.

### `audit`

Good for:

- runtime, permissions, security, CI, releases;
- broad refactors;
- work where evidence tampering or stale validation would be expensive;
- changes to YourAITeam’s own protected runtime.

Profiles affect both role routing and the maximum boundary improvement cycles, but they do not permit acceptance with hard failures.

---

## 7. What happens at the quality/value boundary

Boundary management is **opt-in per task/run** in the current alpha. A task without a boundary contract uses the compatible pre-0.5 gate-to-checkpoint path.

With a boundary contract:

```text
gates PASS
→ NEEDS_BOUNDARY_DECISION
→ measure current artifacts
→ manager selects one allowed action
→ runtime verifies receipt chain
→ advancement is unlocked or remains blocked
```

Most users should let the integration create and operate the boundary. The low-level commands are useful for diagnostics:

```powershell
.\scripts\boundary-measure.ps1 -Workspace .teamloop --boundary-id boundary-001
.\scripts\boundary-status.ps1 -Workspace .teamloop --boundary-id boundary-001
.\scripts\boundary-verify.ps1 -Workspace .teamloop --boundary-id boundary-001
.\scripts\boundary-lock-status.ps1 -Workspace .teamloop
```

Bash equivalents use the `.sh` files and `--boundary-id`.

### HTML dashboard

```powershell
python scripts/teamloop-core.py boundary-status `
  --workspace .teamloop `
  --boundary-id boundary-001 `
  --format html `
  --output boundary-dashboard.html
```

The dashboard separates:

- broad draft coverage;
- receipt-verified accepted progress;
- hard blockers;
- root issues and payoff;
- remaining improvement budget;
- human-decision requirements.

The dashboard is read-only and is not acceptance authority.

---

## 8. Deterministic CLI team workflow

You can use the team composer without OpenCode.

### PowerShell

```powershell
.\scripts\your-ai-team.ps1 propose `
  --backend opencode `
  --task "Fix the flaky Playwright test" `
  --max-tokens 35000 `
  --output .teamloop\team\proposal.json

.\scripts\your-ai-team.ps1 negotiate `
  --proposal .teamloop\team\proposal.json `
  --request "Fit within 25000 tokens; reviewer only at the end" `
  --output .teamloop\team\proposal-2.json

.\scripts\your-ai-team.ps1 accept `
  --proposal .teamloop\team\proposal-2.json `
  --output .teamloop\team\accepted.json

.\scripts\your-ai-team.ps1 materialize `
  --proposal .teamloop\team\accepted.json `
  --backend opencode `
  --output-dir .teamloop\generated\opencode
```

### Bash

```bash
bash scripts/your-ai-team.sh propose \
  --backend opencode \
  --task "Fix the flaky Playwright test" \
  --max-tokens 35000 \
  --output .teamloop/team/proposal.json
```

Then use `negotiate`, `accept`, and `materialize` in the same way.

For Codex, use `--backend codex`.

---

## 9. Statuses you will see

| Status | Meaning |
|---|---|
| `DONE` | The complete requested outcome passed the required chain. |
| `SAFE_CHECKPOINT` | State is safe to resume, but the overall project is not necessarily done. |
| `NEEDS_BOUNDARY_DECISION` | Gates passed, but quality/value acceptance is still locked. |
| `HUMAN_DECISION_REQUIRED` | A classified decision only the user can make is required. |
| `BLOCKED` | Work cannot safely continue under the current contract. |
| `STOPPED_BUDGET_EXHAUSTED` | The finite improvement budget was consumed honestly. |
| `PARTIAL_WITH_DEBT` / `DRAFT_WITH_LIMITATIONS` | Useful work exists, but it is not full success. |

Remember:

```text
SAFE_CHECKPOINT != DONE
TICKET_CLOSED != USER_VALUE_ACCEPTED
```

---

## 10. Resume after restart or context compaction

The durable truth is `.teamloop`, not the chat summary.

After restarting OpenCode:

1. start it from the same project root;
2. keep the same `.teamloop` directory;
3. select `orchestrator`;
4. run:

```text
/supervised-task Continue the current run from durable state.
```

For diagnostics:

```powershell
.\scripts\validate-state.ps1 -Workspace .teamloop
.\scripts\next-action.ps1 -Workspace .teamloop
```

Do not repair the state by manually changing status fields, receipts, counters, or evidence.

---

## 11. Common problems

### “I sent an ordinary prompt, but it keeps talking about the old run”

The active primary agent is probably `orchestrator`, which always follows `.teamloop`.

- switch to **Build** with `Tab`;
- create a new OpenCode session for ordinary work;
- do not use session continuation flags when you want a clean conversation;
- check which repository root and `.teamloop` are active.

### “The final gate passed, but several checks were skipped”

Read the summary counts. `PASS`, `SKIP`, `NOT_REQUIRED`, and `UNAVAILABLE` are different outcomes. Overall PASS does not mean every check ran.

### “Gate passed, but the task is still locked”

A boundary contract exists. Inspect:

```powershell
.\scripts\boundary-lock-status.ps1 -Workspace .teamloop
```

Then view the boundary packet and decision.

### “The agent says it completed an improvement, but the runtime says NO_PROGRESS”

The runtime compares authoritative before/after measurements. Editing comments, counters, reports, or status labels does not count as deliverable progress.

### “A Windows run starts investigating WSL paths”

Use the native wrapper family for the environment that owns the checkout:

- native Windows/OpenCode session: `scripts/*.ps1`;
- Linux or a repository stored inside WSL: `scripts/*.sh`.

Do not invoke WSL Bash against a Windows checkout merely to run YourAITeam checks. Mixed path spaces such as `C:\...` and `/mnt/c/...` are valid in their own environments but create misleading diagnostics when combined. Inspect sentinel `cacheSummary` before changing shells.

### “PowerShell reports encoding/parser errors”

Use PowerShell 7 (`pwsh`) and the ASCII-safe wrappers from the current release. Avoid editing `.ps1` scripts with legacy encodings.

---

### “Sentinel failed, but the underlying problem was already fixed”

The sentinel now performs a deterministic cache preflight. Its JSON contains `cacheSummary`:

- `CACHE_BYPASSED` — the cache was corrupt/invalid, so every check ran fresh;
- `STALE_ENTRY_RECOMPUTED` — a cached WARNING/CRITICAL changed during the automatic fresh retry;
- `CACHE_EMPTY` — no reusable entries existed;
- `CACHE_READY` — normal cache reuse.

Do not start by debugging WSL paths, shell quoting, or manually deleting the cache. First inspect:

```powershell
$result = .\scripts\run-sentinel.ps1 -Workspace .teamloop | ConvertFrom-Json
$result.cacheSummary
```

A fresh PASS is authoritative. Use `cache-clear` only as an explicit recovery operation, not as the default troubleshooting ritual.

### Validate all shipped scripts

Run the unified validator after copying/updating YourAITeam and whenever `scripts/` or the test launchers change:

```powershell
.\scripts\validate-scripts.ps1 -Root .
```

```bash
bash scripts/validate-scripts.sh --root .
```

It checks every PowerShell, Bash, Python, and extensionless command wrapper. Missing PowerShell/Bash runtimes are reported as `UNAVAILABLE`; syntax that can be checked locally is still validated.

---

## 12. Recommended first run

For a normal repository task:

1. initialize `.teamloop`;
2. start OpenCode from the repository root;
3. optionally run `/your-ai-team <task>` and negotiate the proposal;
4. run `/supervised-task <task> using the standard profile`;
5. let the runtime route one bounded action at a time;
6. inspect boundary status if advancement is locked;
7. accept `DONE` only after the final gate and current receipt chain pass.

That path gives the best balance between useful autonomy and honest stopping behavior.

## Further reading

- [YourAITeam MVP](YOUR_AI_TEAM.md)
- [OpenCode setup](OPENCODE_SETUP.md)
- [Runtime reference](RUNTIME.md)
- [Fast / standard / audit](docs/FAST_EXECUTION.md)
- [Quality/value boundary](docs/QUALITY_VALUE_BOUNDARY.md)
- [Testing](TESTING.md)
