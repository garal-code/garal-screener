# Task Completion Checklist

Before considering a coding task done, run `make check` (equivalent to CI):
1. `ruff check .`
2. `black --check --diff .`
3. `mypy . --ignore-missing-imports --no-error-summary`
4. `pytest`

If any step fails, prefer `make resolve` for auto-fixable lint/format issues, then re-run `make check`.
Coverage gate: `fail_under = 70` in `[tool.coverage.report]` — run `pytest --cov` if a change may have dropped coverage.
