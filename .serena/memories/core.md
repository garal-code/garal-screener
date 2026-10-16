# Garal Screener — Core

FastAPI backend for personal investment portfolio tracking (Spanish domain: cartera=portfolio, movimiento=transaction, instrumento=security/ticker, plusvalia=capital gain).

## Source map
- `main.py` — FastAPI app factory, CORS, router registration, `/health`. Routers mounted under `/api/v1`.
- `app/database.py` — SQLAlchemy engine/session (`get_db`, `init_db`).
- `app/models.py` — ORM models: `User`, `PasswordResetToken`, `Cartera`, `Instrumento`, `Movimiento`. Legacy `declarative_base()` style (not SQLAlchemy 2.0 `DeclarativeBase`) — see mypy override in pyproject.
- `app/schemas.py` — Pydantic request/response schemas.
- `app/auth/` — auth domain: `router.py` (register/login/logout/reset endpoints), `security.py` (JWT, password hashing, `get_current_user`, `get_owned_cartera`). See `mem:auth`.
- `app/routers/` — `carteras.py`, `instrumentos.py`, `movimientos.py`, `posiciones.py` (resumen/analisis/rentabilidad-periodo, the largest router). All CRUD on a cartera's sub-resources must scope by owner — see `mem:auth`.
- `app/services/calculos.py` — portfolio math: FIFO cost-basis (`calcular_posicion_fifo`), latent/realized gains, period returns. See `mem:domain_calculos`.
- `app/services/precios.py` — external price/FX fetching (yfinance, OpenFIGI). Network-dependent; heavily mocked in tests.
- `alembic/` — DB migrations, config at `alembic.ini`.
- `openspec/` — spec-driven-development artifacts (changes/specs), one subfolder per feature area (carteras, movimientos, posiciones, cartera-ownership, user-authentication).
- `tests/` — see `mem:testing`.

## Invariants
- Every user-owned resource (Cartera and everything hanging off it) MUST be fetched through an ownership-checked dependency, never by raw `db.query(Model).filter(Model.id == id)`. See `mem:auth`.
- Domain language in code/DB is Spanish (field names, docstrings); keep new code consistent with this.
