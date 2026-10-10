# Markowitz Efficient Frontier

File locator: `odd/tasks/markowitz-efficient-frontier.md`
Engram mirror: `odd/markowitz-efficient-frontier/tasks`
Branch: `feat/markowitz-frontera-eficiente` (stacked on `chore/serena-setup`, PR #23)

## Objective

Expose an endpoint that analyses a portfolio's open positions with Markowitz mean-variance
theory: variance-covariance matrix, long-only efficient frontier, minimum-variance portfolio,
and where the current portfolio sits relative to the frontier.

## Problem / Why

The user models efficient portfolios by hand in a spreadsheet ("Markowitz - Cartera Eficiente.xlsx",
Google Drive). The app has no historical price series and no portfolio-risk analysis, so this
cannot be done for the real portfolio inside the product.

## Reference model (spreadsheet)

- Periodic (monthly) simple returns per asset, sample covariance (n-1 denominator).
- Closed-form, short-selling-allowed frontier: A = mu'S^-1 mu, B = 1'S^-1 mu, D = 1'S^-1 1;
  min-variance return = B/D, min variance = 1/D. No annualisation.
- This feature deliberately departs from the spreadsheet: **long-only** (0 <= w <= 1, sum w = 1),
  solved numerically. The closed form is kept only as a test oracle for the unconstrained case.

## Scope

- Backend only (`garal-screener`). Frontend chart is a separate future task in `investment-portfolio-ui`.
- Assets = open positions (quantity > 0) of an owned cartera.
- Monthly returns from yfinance adjusted close; lookback default 5 years (60 months), configurable.
- ~20 frontier points (configurable), minimum-variance portfolio, current portfolio point
  (weights = current EUR market value share of each position).

## Constraints

- Ownership: cartera fetched through the ownership-checked dependency (`get_owned_cartera`); foreign cartera -> 404.
- External price fetches batched (one yfinance download for all tickers), mocked in tests.
- Spanish domain naming in code/DB/schemas, consistent with the codebase.
- Add `numpy` and `scipy` to `requirements.txt` explicitly.
- Monthly figures, no annualisation (matches spreadsheet); expose the frequency in the response.

## Acceptance criteria

- `GET /api/v1/carteras/{cartera_id}/frontera-eficiente?anios=5&puntos=20` returns:
  tickers, covariance matrix, expected monthly returns, frontier points (return, volatility, weights),
  minimum-variance portfolio, current portfolio (return, volatility, weights).
- All frontier weights are in [0, 1] and sum to 1 (tolerance 1e-6).
- Minimum-variance volatility <= volatility of every frontier point and of the current portfolio.
- Fewer than 2 assets with usable history, or fewer than 12 common monthly observations -> 400 with a clear
  `detail` (consistent with existing business-rule errors, e.g. `movimientos.py` overselling). 422 stays
  reserved for FastAPI request validation (user decision, 2026-10-04).
- Price provider (yfinance) failure -> 503, never reported as insufficient data (user decision, 2026-10-04).
- The analysis window ends at the last fully completed month; the current month is never used.
- Positions whose own history is shorter than the minimum are excluded before the calculation and listed
  in the response (`excluidos`), so one recent listing cannot make the whole analysis fail.
- Returns stay in each asset's native currency, as in the reference spreadsheet (no FX risk modelled).
- Foreign or missing cartera -> 404.

## Tasks

- [x] T1 — Historical monthly price series: batch fetch of adjusted monthly closes in `app/services/precios.py` + tests with mocked yfinance.
- [x] T2 — Pure Markowitz math service `app/services/markowitz.py` (returns, covariance, min-variance, long-only frontier via scipy SLSQP, portfolio stats) + deterministic tests (spreadsheet data as fixture; closed-form oracle for unconstrained case) + `numpy`/`scipy` in `requirements.txt`.
- [x] T3 — Endpoint + schemas in `app/routers/posiciones.py` / `app/schemas.py`, wiring positions -> weights -> service, API tests (owner, foreign 404, insufficient data 400, provider failure 503, current month excluded, short-history ticker excluded). Includes review follow-ups: distinguish provider failure from no data in `obtener_precios_mensuales_batch`; end the window at the last completed month.
- [x] T4 — Calculation transparency (user request 2026-10-04, after reviewing the PRs): extend the frontier response so the frontend can show how the result was computed, like the reference spreadsheet: (1) inputs: months used, monthly adjusted close per asset, monthly return matrix; (2) per-asset stats: expected return, variance, volatility, correlation matrix; (3) current portfolio: EUR value per position and the portfolio's monthly return series; (4) frontier points: target return, variance, volatility, weights; (5) ~~short-selling reference~~ (removed by user decision, see Progress). Always included, no extra query param. Delivered as a PR stacked on #26 (`feat/markowitz-04-detalle-calculo`).

## Route declaration

- T1–T4: delegated direct (one writer). Trigger: writer trigger (2+ non-trivial files) and preparation trigger (reading routers/services/tests to prepare writes).

## Checks

- Focused: `.venv/bin/python -m pytest tests/test_precios.py tests/test_markowitz.py tests/test_api.py -q`
- Task closure: `make check` (ruff, black --check, mypy, full pytest)
- Test-first: RED before GREEN for each task where a deterministic test applies.

## Delivery

- Forecast: ~550-650 authored changed lines (T1 ~120, T2 ~250, T3 ~220) -> exceeds the ~400 budget.
- Strategy: ask-on-risk (default); chain strategy `stacked-to-main` (user choice, 2026-10-04).
- Slices: PR-T1 `9d7ca27` -> develop (stacked on `chore/serena-setup` until #23 merges);
  PR-T2 `47fb7de` -> T1 branch; PR-T3 -> T2 branch.
- Opened 2026-10-04: #24 `feat/markowitz-01-precios-mensuales` -> `chore/serena-setup` (+307/-0);
  #25 `feat/markowitz-02-calculo` -> #24 branch (+636/-3, size:exception: ~310 lines are tests);
  #26 `feat/markowitz-frontera-eficiente` -> #25 branch (+760/-20, size:exception: endpoint contract
  verified end to end, ~280 lines of API tests). Repo does not delete merged branches: retarget #24 to
  `develop` by hand after #23 merges.
- RDD for fix commit `3b4da49`: assessed medium, `under_budget` (136 lines), no review due.
- Running count: T1 ~221 + T2 ~624 + T3 ~606 authored lines (incl. tests and this doc).

## Progress / Evidence

- 2026-10-04: feature document created; branch created.
- 2026-10-04 T1 done (delegated writer). `obtener_precios_mensuales_batch(tickers, fecha_inicio, fecha_fin)
  -> dict[str, list[tuple[date, float]]]`: one `yf.download(interval="1mo", auto_adjust=True)`, dates
  normalised to the 1st of the month (latest row wins within a month), NaN dropped, tickers without
  data omitted, download errors logged -> `{}`. Handles flat (single ticker) and MultiIndex columns.
  RED: collection ImportError (`obtener_precios_mensuales_batch` missing). GREEN:
  `pytest tests/test_precios.py -q` -> 41 passed. Commit: `9d7ca27` feat(precios).
- 2026-10-04 T2 done (delegated writer). `app/services/markowitz.py`: aligned monthly simple returns
  (a month missing in any asset is dropped for all; returns never span two months), mean + sample
  covariance (n-1), long-only min-variance and frontier via SLSQP (analytic jacobians, objective
  scaled to O(1), feasible warm start), `DatosInsuficientesError` for <2 assets, <12 common monthly
  observations, or singular covariance. `numpy>=2.0`, `scipy>=1.13` added to requirements.
  Key oracle: long-only min-var weights match the spreadsheet closed form S^-1 1 / D (atol 1e-3),
  variance 1/D = 0.000531617. RED: collection ImportError (module missing). GREEN:
  `pytest tests/test_precios.py tests/test_markowitz.py -q` -> 63 passed; `make check` -> 179 passed.
  Commit: `feat(markowitz)` (see `git log`).

- 2026-10-04 RDD: committed range develop..47fb7de (chore #23 + T1 + T2) assessed high; user granted;
  4-lens native review approved and acknowledged (lineage `review-f637dde9ce5f3c9d`, authority burned).
  Non-blocking advisory findings (follow-ups, not review blockers):
  - R3/R4 WARNING `app/services/precios.py:316-331`: a yfinance outage returns `{}` and would surface as
    "insufficient data" (422) instead of an upstream error.
  - R3 WARNING `app/services/precios.py:342-345`: the current, partial month is included as a full month.
  - R3 SUGGESTION `app/services/markowitz.py:169-177`: `evaluar_cartera` does not reject NaN weights.
  - R3 SUGGESTION `app/services/markowitz.py:218-229`: silent omission of unsolved frontier points untested.
  - R2 SUGGESTION `app/services/markowitz.py:211-213`: tolerance constant reuse.
  - R2 (chore #23) `app/routers/posiciones.py:70`, `app/routers/movimientos.py:54`: question/WARN notes as
    comments; `.serena/memories/conventions.md:6` contradicts another memory.

- 2026-10-04 decision (user): the current, incomplete month is never used; the analysis window always
  ends at the last fully completed month. Folded into T3.

- 2026-10-04 T3 done (delegated writer). Two work-unit commits:
  - `6cf111e` fix(precios): `ProveedorPreciosError` raised when `yf.download` raises, returns an empty
    frame, or no ticker has a single close (all-NaN); rows of the current month or after `fecha_fin` are
    dropped; `fin_ultimo_mes_completo()` (today via patchable `_hoy`). Resolves review R3/R4
    (precios.py:316-331) and R3 (precios.py:342-345). No other callers of the monthly fetch existed.
  - feat(posiciones) (this commit): `GET /carteras/{id}/frontera-eficiente?anios=5&puntos=20`
    (`anios` 1..20, `puntos` 2..100 -> FastAPI 422). Ownership via `get_owned_cartera` (404). Open
    positions and EUR values reused from `resumen_cartera` (FIFO + batch prices/FX, as `/analisis`).
    Window `[date(fin.year - anios, fin.month, 1), fin_ultimo_mes_completo()]` -> anios*12 returns.
    `markowitz.separar_historicos_cortos` excludes tickers with < 12 own monthly returns before
    alignment. `excluidos` lists `{ticker, motivo}` (sin_ticker, sin_precio_actual, sin_historico,
    historico_insuficiente); `peso_excluido` = excluded EUR value share; current weights renormalised
    over included assets. < 2 valued positions (checked before fetching) or `DatosInsuficientesError`
    -> 400 with Spanish detail listing exclusions; `ProveedorPreciosError` -> 503.
  RED: collection ImportError in test_api/test_precios/test_markowitz (`ProveedorPreciosError`,
  `separar_historicos_cortos` missing). GREEN: `.venv/bin/python -m pytest tests/test_api.py
  tests/test_precios.py tests/test_markowitz.py -q` -> 147 passed; `make check` -> ruff/black/mypy
  clean, 201 passed.

- 2026-10-04 RDD: T3-only range (47fb7de..62757ae, medium) declined by user; whole branch
  develop..62757ae (high, 25 files / 2011 lines) granted, 4-lens review approved and acknowledged
  (lineage `review-e26c363d8228b2bd`, authority burned). Parent spot check: focused tests 147 passed.
  Non-blocking advisory WARNINGs on T3 (candidates for a follow-up fix before PRs):
  - R4 `app/routers/posiciones.py:467-472`: CPU-bound SLSQP runs inside the async handler and blocks the event loop.
  - R4 `app/routers/posiciones.py:445-452`: the 503 path does not log the provider failure.
  - R3 `app/routers/posiciones.py:415-425`: a NaN current EUR value can propagate into the weights.
  - R2 `app/schemas.py:211-215`: schema readability warning.
  Suggestions: R1 `posiciones.py:386-389`; R2 `markowitz.py:237`, `.gitignore:43`; R3 untested frontier
  point omission and singular-covariance API path. Chore-only notes (posiciones.py:70, movimientos.py:54,
  `.serena/memories/conventions.md:6`) repeat the earlier review.
- `.serena/project.yml` was rewritten by Serena itself (regenerated language list); not part of this feature.

- 2026-10-04 T3 review follow-up (delegated writer), commit `fix(posiciones)` (see `git log`); resolves the
  four T3 WARNINGs:
  - R4 event loop: the pure calculation (`calcular_estadisticas_activos`, `calcular_frontera_eficiente`,
    `evaluar_cartera`) moved to `_calcular_frontera` and awaited via `fastapi.concurrency.run_in_threadpool`;
    `DatosInsuficientesError` -> 400 unchanged.
  - R4 logging: `ProveedorPreciosError` logged with `logger.warning` (module logger, as in
    `app/auth/router.py`) with cartera id and error; the 503 detail stays generic.
  - R3 NaN: `_es_valor_positivo` treats None, non-finite or <= 0 EUR values as `sin_precio_actual`, so
    current weights and `peso_excluido` are always finite.
  - R2 schema: `ActivoExcluido.motivo` typed as `MotivoExclusion` Literal with each reason documented;
    JSON field names/shapes unchanged (OpenAPI now exposes the enum).
  RED: 4 new API tests failed (no log record; NaN/inf ticker kept as an asset or reported as `sin_historico`;
  `run_in_threadpool` absent from the router). GREEN: `.venv/bin/python -m pytest tests/test_api.py
  tests/test_precios.py tests/test_markowitz.py -q` -> 151 passed; `make check` -> ruff/black/mypy clean,
  205 passed.

- 2026-10-04 T4 done (delegated writer), commit `feat(posiciones)` on `feat/markowitz-04-detalle-calculo`
  (see `git log`). Additive: every existing response field keeps its name and shape.
  - `markowitz.py` (pure): `calcular_estadisticas_desde_rentabilidades` (returns computed once and reused),
    `alinear_precios_mensuales` (price axis = each return month plus its previous month, so with consecutive
    months it has exactly one more element), `rentabilidades_cartera` (w·r per month, the spreadsheet's
    PORTFOLIO column), `EstadisticasActivos.varianzas/volatilidades/matriz_correlaciones` (correlation 0
    off-diagonal for a zero-variance asset, never NaN), `PuntoCartera.varianza`, and
    `calcular_referencia_con_cortos` -> `ReferenciaConCortos` (S^-1, A, B, D, AD-B^2, unconstrained
    min-variance S^-1 1 / D) or None under the existing singularity criterion.
  - Router: `_calcular_frontera` (threadpool) now returns `_ResultadoFrontera` with all of the above; the
    handler only maps to schemas.
  - Response: every point (`frontera[]`, `cartera_minima_varianza`, `cartera_actual`) gains `varianza`;
    `rentabilidad` documented as the target return on frontier points. New `detalle`:
    `datos {fechas_precios, precios[fechas_precios x tickers], fechas, rentabilidades[fechas x tickers]}`,
    `estadisticas {varianzas, volatilidades, matriz_correlaciones}` (means/covariance stay at the root),
    `cartera_actual {valores_eur, rentabilidades[fechas]}`,
    `referencia_con_cortos {matriz_covarianzas_inversa, a, b, d, a_d_menos_b2, cartera_minima_varianza} | null`.
  - Spreadsheet oracle: D = 1881.054 (matches); the fixture's mu is rounded, so A and B are asserted
    against the recomputation (1.5351 / 44.693 vs the sheet's 1.5397 / 44.597); unconstrained weights
    (0.1325, 0.1674, 0.3193, 0.2491, 0.1317), variance 0.000531617.
  RED: `tests/test_markowitz.py` collection ImportError (`alinear_precios_mensuales`,
  `calcular_referencia_con_cortos`, `rentabilidades_cartera` missing); `test_detalle_del_calculo` failed
  with KeyError `detalle`. GREEN: `.venv/bin/python -m pytest tests/test_api.py tests/test_precios.py
  tests/test_markowitz.py -q` -> 162 passed; `make check` -> ruff/black/mypy clean, 216 passed.

- 2026-10-04 decision (user): short selling will never be part of the analysis, so the
  short-selling reference (T4 item 5: `referencia_con_cortos`, `ReferenciaConCortos`,
  `calcular_referencia_con_cortos`, `ReferenciaConCortosOut`) was removed from the API and the
  service. The closed form remains only inside the T2 oracle test (computed locally in the test).
  `make check`: ruff/black/mypy clean, 213 passed. Delivered in #27.
- 2026-10-04 PRs: #23 merged; #24 retargeted to `develop` automatically; #27 opened for T4;
  #28 (`chore/serena-project-config`) holds the Serena-regenerated `.serena/project.yml`.
- 2026-10-04 RDD: full chain (old develop..2012f86) approved (`review-c4db9f224d6799ed`). Follow-up
  WARNING: `app/services/precios.py:371-390` partial provider failure is reported as `sin_historico`.

## Next step

User merges #24 -> #25 -> #26 -> #27 bottom-up, deleting each merged branch so GitHub retargets the
next PR to `develop`; #28 is independent. Follow-ups: partial provider failure motivo; polish the
detail block as the frontend needs it; frontend chart in `investment-portfolio-ui`.
