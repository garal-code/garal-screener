# Suggested Commands

Run everything through the venv interpreter — Makefile hardcodes `.venv/bin/python`.

- `make check` — full CI-equivalent gate: ruff check, black --check --diff, mypy, pytest. Run before considering any task done (see `mem:task_completion`).
- `make typecheck` — mypy only.
- `make resolve` — auto-fix: black format + ruff --fix.
- `.venv/bin/python -m pytest` — run tests directly (add `-k <name>` / `-m unit|integration|slow` to filter; markers registered in pyproject).
- `.venv/bin/python -m pytest --cov` — coverage; fails under 70% (`fail_under = 70` in pyproject).
- `alembic revision --autogenerate -m "..."` / `alembic upgrade head` — migrations (uses `alembic.ini` + `alembic/env.py`).
- `uvicorn main:app --reload` — run the API locally (reads `.env` via `load_dotenv()`).

## Darwin-specific
- Use `eza`, `rg`, `fd`, `bat`, `sd` instead of `ls`/`grep`/`find`/`cat`/`sed` (per user's global tooling preference, not project-specific).
