# Tech Stack

- Python 3.12, FastAPI, SQLAlchemy 2.x (legacy `declarative_base()` style, not `DeclarativeBase`/`Mapped` 2.0-native — models.py is mypy-ignored for this reason).
- Alembic for migrations (config: `alembic.ini`, versions in `alembic/versions/`).
- Auth: PyJWT + passlib[bcrypt]. **Pin `bcrypt>=4.0.1,<4.1`** — passlib 1.7.4 breaks on bcrypt>=4.1 (`AttributeError: module 'bcrypt' has no attribute '__about__'`). Do not bump bcrypt past 4.0.x until passlib is upgraded/fixed.
- DB: SQLite locally (`cartera.db`), likely Postgres in prod (`psycopg2-binary` dep, Railway deploy per `railpack.json`).
- Price/FX data: `yfinance` + OpenFIGI lookup, in `app/services/precios.py`.
- Testing: pytest + pytest-asyncio (`asyncio_mode = "auto"`), FastAPI `TestClient`.
- Lint/format/type: ruff (rules E,W,F,I; first-party = `app`), black, mypy (`python_version = 3.12`, `warn_return_any`).
- Package manager: pip with `requirements.txt` / `requirements-dev.txt` (no poetry/uv lockfile).
