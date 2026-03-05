# CLAUDE.md — deepctl Project Instructions

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
5. **Version markers** — every version line in pyproject.toml must have `# x-release-please-version`
6. **New packages require updates to**: root `pyproject.toml`, `release-please-config.json`, `.release-please-manifest.json`, `.github/workflows/test.yml` (test paths)

## Workspace Packages

All packages live under `packages/`. The UV workspace (`members = ["packages/*"]`) auto-discovers them. Each command registers via Python entry points — no hardcoded imports.

## Code Style

- Linter/formatter: `ruff` (line-length 88, target py310)
- Type checker: `mypy` (strict)
- Use `from __future__ import annotations` for forward references
- Move type-only imports into `TYPE_CHECKING` blocks
- Positional args: `{"name": "source"}` — Options: `{"names": ["--flag"], "is_option": True}`
