# Conventions

- Domain vocabulary is Spanish throughout (models, fields, function names, docstrings): cartera, movimiento, instrumento, plusvalia_latente/realizada, rentabilidad. Keep new identifiers consistent — don't mix English domain terms into this layer.
- Ownership pattern: resources scoped to a user go through a `get_owned_*` FastAPI dependency (e.g. `get_owned_cartera` in `app/auth/security.py`) that filters by `user_id == current_user.id` in the query itself (not fetch-then-check), and raises 404 (never 403) when missing or foreign — avoids confirming existence of another user's resource. Follow this pattern for any new owned sub-resource.
- Type hints use modern `X | None` syntax (not `Optional[X]`).
- SQLAlchemy models use `Mapped[...]` + `mapped_column(...)` annotations even though the base is legacy `declarative_base()` (mixed style — intentional, see `mem:tech_stack`).
- Ruff import sorting treats `app` as first-party (`known-first-party = ["app"]`).
