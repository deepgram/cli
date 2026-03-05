Run tests for a specific package or all packages.

If $ARGUMENTS is provided, run tests for that package:
`uv run pytest packages/$ARGUMENTS/tests/ -v`

If no arguments, run all tests:
`uv run pytest -v`

Report pass/fail counts and any failures.
