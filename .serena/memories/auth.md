# Auth Module (`app/auth/`)

- `security.py`: JWT via PyJWT (`JWT_SECRET_KEY`, `JWT_ALGORITHM`), cookie-based session (`ACCESS_TOKEN_COOKIE_NAME`, `ACCESS_TOKEN_EXPIRE_MINUTES`), passlib bcrypt hashing.
- `DUMMY_PASSWORD_HASH` + timing-safe verify path in login: prevents user-enumeration via response-time side channel when the email doesn't exist.
- `IS_LOCAL_ENV` gates dev-only behavior (e.g. logging reset tokens) — never let dev conveniences leak into prod paths; check this flag pattern when adding similar debug affordances.
- `get_current_user` — dependency resolving the JWT cookie into a `User`.
- `get_owned_cartera(cartera_id, current_user, db)` — the canonical ownership-check dependency: filters `Cartera` by `id` AND `user_id` in one query, 404s (never 403) if not found/foreign. Every router touching a cartera or its sub-resources (movimientos, posiciones) must depend on this rather than querying `Cartera`/`Movimiento` directly by id.
- Password reset: `PasswordResetToken` model, hashed token stored (`token_hash`), `used_at`/`expires_at` for single-use + expiry enforcement.
- Test fixtures `auth_client` / `second_auth_client` in `tests/conftest.py` — two independently-cookied `TestClient`s against the same in-memory DB, used for owner-vs-foreign authorization tests.
