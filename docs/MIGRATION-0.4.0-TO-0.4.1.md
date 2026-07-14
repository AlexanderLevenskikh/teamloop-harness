# Migration: 0.4.0 → 0.4.1

Version 0.4.1 makes **YourAITeam** the only public product name.

## Canonical names

| Surface | Canonical value |
|---|---|
| Product | `YourAITeam` |
| Repository/package slug | `your-ai-team` |
| CLI front door | `scripts/your-ai-team` / `scripts/your-ai-team.sh` / `scripts/your-ai-team.ps1` |
| OpenCode command | `/your-ai-team` |
| Codex skill | `$your-ai-team` |
| Runtime guide | `RUNTIME.md` |
| Composer module | `scripts/your_ai_team.py` |

## Removed public aliases

The MVP-only names `your-team`, `/your-team`, `TEAMLOOP.md`, and the old repository slug are no longer part of the public surface. Update scripts and links to the canonical forms above.

## Legacy technical namespace

The following identifiers remain temporarily for workspace and source compatibility in the v0.4 runtime:

- `.teamloop/` workspace directories;
- `TEAMLOOP_*` diagnostic environment variables;
- `teamloop-core.py` and several internal `teamloop_*` Python modules;
- schema value `teamloop-validation-cache/v2` for existing cache files.

They are **not alternate product names**. Renaming them is intentionally deferred to a separately versioned state migration, because changing persisted workspace paths and cache IDs in an organizational rename would create unnecessary data-loss risk.

New user-facing documentation, release artifacts, commands, package names, generated adapter files, and prose must use `YourAITeam` or `your-ai-team`.
