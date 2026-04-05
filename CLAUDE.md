# CLAUDE.md — deepctl Project Instructions

> **This app is part of the Deepgram DX stack.** When working in this repo, you must follow cross-stack documentation rules. A PostToolUse hook will remind you when you edit cross-stack files, but you are also responsible for catching changes the hook doesn't cover.

## DX Stack Rules

1. **Incremental changes, comprehensive reviews.** Make changes incrementally. But before finishing any task, do a comprehensive review to spot architectural misses — port conflicts, auth flow breakage, env var mismatches, or contract changes that affect other services.

2. **Update dx-stack docs when you change cross-stack behavior.** If your change affects ports, auth flows, env vars, redirect URIs, API contracts, or deployment config, update the reference docs at `/Users/lukeoliff/Projects/deepgram/dx-stack/` before finishing.

3. **Know the architecture.** Read `/Users/lukeoliff/Projects/deepgram/dx-stack/CLAUDE.md` for the full stack context — port map, auth flows, service-to-service communication, and environment matrix.

### What requires dx-stack updates

| Change | Update |
|--------|--------|
| Port changes | `dx-stack/CLAUDE.md` port map + `docs/runbook.md` |
| Auth flow / session changes | `dx-stack/docs/auth.md` |
| OIDC client changes | `dx-stack/docs/auth.md` client table |
| Env var changes | `dx-stack/docs/environments.md` |
| New cross-service endpoints | `dx-stack/CLAUDE.md` cross-service section |
| Deployment / Fly config changes | `dx-stack/CLAUDE.md` deployment section |
| Database schema changes | `dx-stack/docs/auth.md` schema section |
| Redirect URI changes | `dx-stack/docs/auth.md` + seed.ts |

## Project

**deepctl** (Deepgram CLI) — plugin-based CLI for Deepgram speech recognition. Aliases: `deepctl`, `deepgram`, `dg`.

## Architecture Reference

See [AGENTS.md](./AGENTS.md) for comprehensive documentation covering:
- Command creation, group commands, external plugins
- Core APIs (Config, Auth, Output, Client)
- Argument specification format
- Testing patterns and CI
- Release process and version management
- All existing commands

## Quick Reference

```bash
# Development
make dev                    # format + lint-fix + test
make check                  # format-check + lint-check + typecheck (no tests)
make readmes                # Regenerate READMEs (REQUIRED after package changes)

# Testing
uv run pytest                                       # All tests
uv run pytest packages/deepctl-cmd-<name>/tests/ -v # Single package

# Running
uv run dg --help            # CLI help
uv run dg <command> --help  # Command help
```

## Rules

1. **Never manually edit** README sections between `<!-- BEGIN:* -->` and `<!-- END:* -->` markers — run `make readmes`
2. **Always run `make check`** after code changes to catch ruff/mypy issues
3. **Always run `make readmes`** after adding or modifying packages
4. **Run `dg skills update`** after adding new commands to regenerate AI skill files
5. **Version markers** — every `version =` line in pyproject.toml must have `# x-release-please-version` (NOT on dependency lines)
6. **New packages require updates to**: root `pyproject.toml`, `.github/release-please-config.json`, `.github/.release-please-manifest.json`, `.github/workflows/test.yml` (test paths)

## Workspace Packages

All packages live under `packages/`. The UV workspace (`members = ["packages/*"]`) auto-discovers them. Each command registers via Python entry points — no hardcoded imports.

## Code Style

- Linter/formatter: `ruff` (line-length 88, target py310)
- Type checker: `mypy` (strict)
- Use `from __future__ import annotations` for forward references
- Move type-only imports into `TYPE_CHECKING` blocks
- Positional args: `{"name": "source"}` — Options: `{"names": ["--flag"], "is_option": True}`
