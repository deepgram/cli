Create a new command package for deepctl.

The command name is: $ARGUMENTS

Follow the complete process documented in AGENTS.md under "Creating a New Command Package":

1. Create the package directory structure under `packages/deepctl-cmd-{name}/`
2. Write `pyproject.toml` with proper entry points and version markers
3. Write `models.py` with Pydantic result models (subclass BaseResult)
4. Write `command.py` with the command class (subclass BaseCommand)
5. Write `__init__.py` exporting the command and result
6. Write unit tests in `tests/unit/test_{name}.py`
7. Update root `pyproject.toml` (dependencies + uv sources)
8. Update `release-please-config.json` (packages + components)
9. Update `.release-please-manifest.json`
10. Update `.github/workflows/test.yml` (add test path)
11. Run `uv sync` to install the new package
12. Run `make check` to verify quality
13. Run the tests
14. Run `make readmes` to regenerate READMEs
15. Run `dg skills update` to update AI skill files

Ask for details about what the command should do before implementing.
