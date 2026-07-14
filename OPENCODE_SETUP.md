# OpenCode project setup

OpenCode discovers project configuration only from the project root it was started in.

The directory where you run `opencode` must contain all of these directly:

```text
AGENTS.md
opencode.jsonc
.opencode/
  agents/
    orchestrator.md
  commands/
    supervised-task.md
```

Do not leave them one directory deeper, for example `current/your-ai-team/.opencode`, while starting OpenCode from `current`.

## Verify

From the same directory:

```bash
pwd
ls -la
ls -la .opencode/agents .opencode/commands
opencode agent list
```

On PowerShell:

```powershell
Get-Location
Get-ChildItem -Force
Get-ChildItem -Force .opencode\agents, .opencode\commands
opencode agent list
```

You should see `orchestrator` as a primary agent alongside Build and Plan. The custom command should appear as `/supervised-task`.

OpenCode reads agents and commands when a session starts. Fully close and restart the TUI after adding or changing these files:

```bash
opencode .
```

If custom entries are still missing:

1. Update OpenCode: `opencode upgrade`.
2. Start it explicitly in this directory: `opencode /absolute/path/to/project`.
3. Run with diagnostics: `opencode --log-level DEBUG --print-logs`.
4. Inspect the newest log under `~/.local/share/opencode/log/` (Windows: `%USERPROFILE%\.local\share\opencode\log`).
