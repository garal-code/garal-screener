# Missing FX Rate Must Not Default to 1.0

File locator: `odd/tasks/fx-fallback.md`
Engram mirror: `odd/fx-fallback/tasks`
Branches: backend `fix/fx-fallback` (stacked on `docs/markowitz-calculo`, 09b5319);
frontend `fix/sin-tipo-cambio` in `investment-portfolio-ui` (stacked on `feat/analisis-05-metodologia`).

## Problem / Why

`resumen_cartera` (`app/routers/posiciones.py:77-81`) uses `fx_rates.get(moneda, 1.0)`: when the current
EUR/currency rate is unavailable, a non-EUR position is valued as if 1 unit = 1 EUR, silently. The fake EUR value
feeds the dashboard (position value, latent gain, return, totals), `/analisis` grouping, and the efficient
frontier (current-portfolio weights, `peso_excluido`, rebalancing moves). Found while documenting the
calculation (`docs/markowitz.md` section 17, note 3).

## Decisions (user, 2026-10-11)

- A missing FX rate is treated like a missing price: EUR-denominated fields of that position are `null`; native
  fields stay. Summary totals fall back to cost, as they already do for missing prices.
- The efficient frontier excludes such positions with a new reason `sin_tipo_cambio` (backend Literal + frontend
  label "sin tipo de cambio").

## Tasks

- [x] T1 — Backend: no 1.0 fallback for non-EUR currencies without a rate; EUR fields `null`, `fx_actual` null;
  frontier exclusion `sin_tipo_cambio` (evaluated after `sin_precio_actual`); schema Literal + comments; remove the
  stale question comment; update `docs/markowitz.md` (exclusion reasons, note 3 resolved); tests for summary and
  frontier.
- [x] T2 — Frontend: `MotivoExclusion` + label "sin tipo de cambio"; verify dashboard/positions table render a
  position with native price but null EUR values without NaN or misleading figures; tests.

## Checks

- Backend: focused `pytest tests/test_api.py -q`; closure `make check`.
- Frontend: touched tests, `npm test`, `npx tsc --noEmit`.
- Test-first: RED before GREEN.

## Progress / Evidence

- 2026-10-11: task document created.
- 2026-10-11 T1: `resumen_cartera` resolves `fx_rates.get(moneda)` with no default (EUR / missing moneda keep
  1.0); with price but no FX it returns native price/value only, EUR fields + `fx_actual` null, one warning per
  currency. Frontier: `_motivo_sin_valor_eur` adds `sin_tipo_cambio` (after `sin_precio_actual`, not in
  `peso_excluido`); `MotivoExclusion` + schema comments/descriptions fixed; `docs/markowitz.md` sections 3, 8,
  17 and citations updated. RED: 4 new tests failed on the 1.0 fallback (fx_actual 1.0, `/analisis` 1500 vs
  cost 1363.64, frontier analysed the USD ticker, no warning). GREEN: `tests/test_api.py` 86 passed; full
  pytest 220 passed; ruff + mypy clean; `black --check` not run (black not installed in `.venv`).
- 2026-10-11 T1 parent follow-up: `/rentabilidad` (`rentabilidad_periodo`) also defaulted the current FX to 1.0
  (the start FX already went to `tickers_sin_dato`). Now None, like `resumen_cartera`: the position keeps no EUR
  value. RED: new `test_rentabilidad_sin_fx_actual_no_asume_1` failed (`1500.0 is None`);
  `test_rentabilidad_posicion_previa_al_periodo` now passes an explicit `{"USD": 1.0}`. black is not in `.venv`;
  run via `uvx black` (formatting applied). GREEN: ruff, black --check, mypy clean; pytest 221 passed.
- 2026-10-14 T2 (`investment-portfolio-ui` `fix/sin-tipo-cambio`, e58dbb2): `MotivoExclusion` + label
  "sin tipo de cambio"; `Posicion` EUR fields nullable; `PositionViewModel.precioActualEur` is undefined when
  unknown (no price, or foreign without FX; never the native price or 0) and new `sinTipoCambio`; the position
  row shows "—" for an unknown EUR price and "<native> · sin tipo de cambio" in the detail. RED: 6 tests failed
  (view model + row + dashboard). GREEN: `npx vitest run` 263 passed; `npx tsc --noEmit` clean.
