# Testing (`tests/`)

- In-memory SQLite shared across connections via URI: `sqlite:///file::memory:?cache=shared&uri=true` — required so multiple connections in one test see the same tables (plain `:memory:` would give each connection its own DB).
- `setup_db` fixture (autouse) creates/drops all tables per test — full isolation, no shared state across tests.
- `client` fixture patches `main.init_db` so the FastAPI lifespan doesn't touch the production DB engine, and overrides `get_db` to use the test session.
- `auth_client` / `second_auth_client` — pre-authenticated TestClients (see `mem:auth`) for testing owner-scoped endpoints; use `second_auth_client` whenever a test needs a "foreign user" to verify 404-not-403 isolation.
- Test files split by concern: `test_api.py` (routers/integration), `test_auth_security.py`, `test_calculos.py`, `test_precios.py` (mock network calls — never hit real yfinance/OpenFIGI in tests).
- Markers: `unit`, `integration`, `slow` — registered in pyproject, `--strict-markers` enforced (typo'd marker = hard failure).
