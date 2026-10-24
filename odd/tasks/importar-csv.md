# Import Movements from a Broker CSV (issue #36)

File locator: `odd/tasks/importar-csv.md` · Engram mirror: `odd/importar-csv/tasks`
Branches: backend `feat/importar-csv` (from `origin/develop`, 9b6ed8d); frontend `feat/importar-csv` in
`investment-portfolio-ui` (from `origin/main`).

## Specs

- **S1** — Issue #36: "poder importar los movimiento en vez de meterlos a mano".
- **S2** — No AI. "leer el CSV y dejar que el usuario matche el CSV con las columnas que requerimos y lo pueda
  guardar ya par ausarlo siempre".
- **S3** — Generic: "pero no limites todo el diseño e implmentacion a estas dos plantillas". The Trading212 and
  Trade Republic exports are test cases only.
- **S4** — Preview: "antes de importar se le avisa al usuaario de lo que el ha apsado lo que se va ameter y lo que
  no y por que."
- **S5** — "Si no tiene ISIN no se mete claro, funcionamos por ISIN."
- **S6** — "Y todo lo que no sea compra o venta tampoco se mete"
- **S7** — Duplicates: the user chose "Por columna ID del broker (Recomendado)": the template maps a broker ID
  column, the ID is stored on the movement, and rows already imported are skipped.
- **S8** — "obviamente esto no lo subas a git": `transaction_templates/` is never committed (excluded via
  `.git/info/exclude`); test fixtures are synthetic and contain no personal data.

## Design

- **D1 Data** — New table `plantillas_importacion` (`id`, `user_id` FK cascade, `nombre` unique per user,
  `config` JSON text, `created_at`, `updated_at`). New nullable column `movimientos.referencia_externa` with
  unique (`cartera_id`, `referencia_externa`). One alembic migration.
- **D2 Template config** — `delimitador` (`,` `;` `\t` `|` or null = auto), `separador_decimal` (`.` or `,`),
  `formato_fecha` (null = ISO date/datetime, else a `strptime` format such as `%d/%m/%Y`), `columnas`
  {`fecha`, `isin`, `tipo`, `cantidad`, `precio` required; `comision` list summed; `tipo_cambio`,
  `moneda_precio`, `referencia` optional}, `valores_compra` / `valores_venta` (at least one
  of the two non-empty, disjoint, case-insensitive exact match; see L15), `invertir_tipo_cambio` (bool).
- **D3 Normalization** — Numbers use the template decimal separator, the other separator is a thousands
  separator, and the absolute value is taken (the type comes from the type column, so the sign is not used).
  Commission = sum of the mapped columns (empty = 0). `tipo_cambio` follows `calculos.py:153` (currency units
  per 1 EUR) and is inverted when `invertir_tipo_cambio` is set. When `moneda_precio` is mapped and the row
  says `EUR`, `tipo_cambio = 1.0`.
- **D4 Endpoints** (`/api/v1`, JSON body with the CSV as text in `contenido`, so no new dependency):
  `GET|POST /plantillas-importacion`, `PUT|DELETE /plantillas-importacion/{id}`;
  `POST /importaciones/inspeccionar` (delimiter, headers, sample rows, distinct values per column);
  `POST /carteras/{id}/importaciones/previsualizar` (read-only); `POST /carteras/{id}/importaciones`
  (confirm).
- **D5 Rows** — Each data row gets `importar` or `omitir` plus a reason in Spanish: not buy/sell, no ISIN,
  invalid ISIN/date/quantity/price/FX rate, duplicated in the file, already imported, ISIN not found,
  sale over the available position (existing movements + accepted earlier rows, in date order). Confirm inserts
  only the `importar` rows.
- **D6 Safety** — Preview writes nothing. Confirm recomputes the plan, requires the preview `huella`
  (409 when it differs), and inserts the rows and any new instruments in one transaction. Limits: `contenido`
  at most 2,000,000 characters, at most 5,000 data rows. A template column missing from the CSV header → 400.
- **D7 API contract** (all under `/api/v1`, cookie auth; unauthenticated → 401; body validation → 422):
  - `POST /importaciones/inspeccionar` body `{"contenido": str, "delimitador": ","|";"|"\t"|"|"|null}` → 200
    `{"delimitador", "columnas": [str], "filas_muestra": [[str]] (≤5), "valores_distintos": {col: [str]}}`
    (output of `services.importacion.inspeccionar`); `ImportacionError` → 400 `{"detail": <message>}`.
  - `POST /carteras/{cartera_id}/importaciones/previsualizar` body `{"contenido": str, "plantilla_id": int}` → 200
    `{"huella": str, "resumen": {"total", "importar", "omitir"}, "filas": [Fila]}`, `filas` ordered by `linea`.
    `Fila` = `{"linea": int, "estado": "importar"|"omitir", "motivo": str|null, "tipo", "fecha", "isin",
    "nombre", "cantidad", "precio", "comision", "tipo_cambio", "referencia"}`; rows skipped by normalization have
    every data field null; rows skipped later keep their normalized fields; `nombre` = instrument name when known.
    Missing/foreign cartera → 404 "Cartera no encontrada"; missing/foreign template → 404 "Plantilla no
    encontrada"; `ImportacionError` → 400. Writes nothing (no instruments, no movements).
  - `POST /carteras/{cartera_id}/importaciones` body `{"contenido", "plantilla_id", "huella"}` → 200
    `{"importados": int, "omitidos": int}`. Recomputed `huella` differs → 409 "La previsualización ya no coincide
    con los datos actuales. Vuelve a previsualizar." and nothing written. Same 400/404 as preview.
  - Reasons added after normalization, rows processed by (`fecha`, `linea`): "Duplicada en el archivo (ID X)"
    (an earlier line has the same `referencia`), "Ya importada (ID X)" (`referencia_externa` already in the
    cartera), "No se encontraron datos para el ISIN X" (not in `instrumentos` and enrichment fails),
    "Venta de N supera la posición disponible (M)" (FIFO position from existing cartera movements plus accepted
    earlier rows; numbers without trailing zeros).
  - `huella` = SHA-256 hex of the canonical JSON of `filas`.

## Tasks

- [x] T1 — S2, S7 · Backend model, migration, template config schema, template CRUD + tests · inline ·
  commit `f2f60dd`
- [x] T2 — S1, S3, S5, S6 · Backend CSV parsing/normalization service + unit tests (synthetic T212- and
  TR-shaped fixtures, plus a `;`/decimal-comma/`%d/%m/%Y` case) · inline · commit `b71b2ca`
- [x] T3 — S4, S5, S6, S7, D4-D7 · Backend inspect, preview, confirm endpoints + `docs/importacion.md` + tests ·
  `gentle-ai-worker` (context backstop) · commit `92589f6` (full suite 331 passed)
- [ ] T4 — S2, S4 · Frontend types, API client, import page (upload, mapping editor, save template, preview,
  confirm), sidebar link + tests · inline · commit: pending (UI repo)
- [ ] T5 — S1-S8 · Independent verification (high-risk item 1: migration and bulk data writes) ·
  `gentle-ai-verify` · verdict: pending

## Log

- **L1** (2026-10-22, user): "quiero implementar la issue 36. La complicacion que veo aqui es cada broker tendra su
  forma de exportar y seguramente difieran mucho, entonces se me ocurre pasarlo por la IA para que vea y
  clasifique cada columna, lo que implica meter IA que es un follon gordo.
  la otra opcion leer el CSV y dejar que el usuario matche el CSV con las columnas que requerimos y lo pueda
  guardar ya par ausarlo siempre"
- **L2** (user): "Sii si tratalo com cambio grande.
  Te he metido las dos templates en transaction_templates, obviamente esto no lo subas a git"
- **L3** (evidence): Trading212 sample is CSV-shaped (`Action`, `Time (UTC)`, `ISIN`, `No. of shares`,
  `Price / share`, `Currency (Price / share)`, `Exchange rate`, `Currency conversion fee`, `ID`). Trade Republic
  sample was a PDF statement; Trade Republic has a native CSV transaction export since April 2026.
- **L4** (user): "llevas razon ya lo tienes" — Trade Republic CSV-shaped sample (`date`, `type` BUY/SELL,
  `symbol` = ISIN, `shares` negative on sells, `price`, `fee` negative, `currency`, `fx_rate`,
  `transaction_id`).
- **L5** (user, question on error rows): "antes de importar se le avisa al usuaario de lo que el ha apsado lo que
  se va ameter y lo que no y por que. Si no tiene ISIN no se mete claro, funcionamos por ISIN. Y todo lo que no
  sea compra o venta tampoco se mete"
- **L6** (user, question on duplicates): "Por columna ID del broker (Recomendado)"
- **L7** (user): "pero no limites todo el diseño e implmentacion a estas dos plantillas"
- **L8** (evidence): T1 `f2f60dd` — 22 template tests; full suite 251 passed; ruff/black/mypy clean; migration
  upgrade → downgrade → upgrade round-trip on scratch SQLite, autogenerate shows no drift for the new objects.
  T2 — 36 service tests passed.
- **L9** (evidence): T3 by `gentle-ai-worker` mvjvv7rg-1-epw2 — RED 8 failed/32 errors, GREEN 63 in
  `tests/test_importacion.py`, full suite 328 passed. Parent spot check found that an accepted sale within the
  1e-9 tolerance broke `GET /carteras/{id}/resumen` with `VentaInvalidaError` (reproduced by
  `test_venta_dentro_de_la_tolerancia_no_rompe_el_resumen`).
- **L10** (evidence, pre-existing bug): the user's real Trade Republic pattern (two fractional buys, then selling
  the whole position) raises `VentaInvalidaError` in `calculos.calcular_posicion_fifo` because of float noise
  (synthetic equivalent: buy 10.1 + 0.7, sell 10.8; 10.8 - 10.1 = 0.7000000000000011), while the manual sale
  check (`movimientos.py:56`) accepts it.
  Fixed in `d66d223` (`fix(calculos)`): remainders up to 1e-9 count as sold; lot consumption unchanged.
  Differential vs HEAD on 3,000 random histories: 2,879 identical, 121 old-raised-now-ok, 0 other differences.
  Import sale check now tries the sale with that same FIFO (accepted iff FIFO covers it).
- **L11** (follow-up, not done): FIFO keeps float-noise "ghost lots" (quantity ~1e-15, shown as 0.0 in
  `lotes_pendientes`) and a meaningless `precio_medio` on such closed positions; pre-existing, left unchanged.
- **L12** (evidence): native review `review-c8b21f2c453f5eaa` (risk, resilience, reliability) of
  origin/develop..92589f6 approved and acknowledged; 14 non-blocking advisories.
- **L13** (evidence, T5 backend): `gentle-ai-verify` mvjwhasm-2-07qv — S1, S3, S5-S8, D1-D6 PASS; S4/D7 PARTIAL
  for B1 (blocking): a CSV field over 131,072 characters escaped as `csv.Error` → HTTP 500. Migration round-trip
  with seeded rows, ownership, atomicity, limits and existing endpoints (21 responses, only the L10 fix differs)
  verified. Fixed after the report: B1 → 400 "El CSV no se puede leer: ..."; duplicate IDs decided by file line
  as D7 says (was processing order); atomicity test added; fixtures no longer reuse the user's real quantities
  or prices (S8). Not verified: Postgres migration and real concurrent writers.
- **L14** (follow-ups, not done): confirm twice without an ID column duplicates rows; huella depends on the
  enrichment name between preview and confirm; FIFO re-simulated per sale (5,000 rows over a 10,000-movement
  cartera ≈ 21 s); same-day rows ordered by line, not time; "posición disponible" shows the position after all
  stored movements, not at the sale date; IntegrityError 409 not logged.
- **L15** (user, 2026-10-24): "En el back (lo que te recomiendo): que exija al menos un valor de compra o de
  venta, no uno de cada. Es lo correcto y además desbloquea los CSV que solo tienen ventas." Done inline: both
  lists default to `[]`; both empty → 422 "Debe haber al menos un valor de compra o de venta".
  Commit `70ea203`, full suite 339 passed.
- **L16** (evidence): native review `review-b3503c209af9d032` (risk, reliability) of origin/develop..70ea203
  approved and acknowledged; 7 non-blocking advisories, none on the L15 change: R1-001 / R3-sync-work-in-async
  (`importaciones.py:220-227`, preview/confirm CPU work blocks the event loop), R1-002 (`importaciones.py:179-181`,
  ISIN enrichment per unknown ISIN), confirm not idempotent without ID column, huella depends on live
  enrichment, instrument creation race (KeyError), same-day rows ordered by line. All but the race are in L14.
- **L17** (user, 2026-10-24): "si partelas en PR stackeadas yo mergeo". Backend delivered as stacked PRs in
  garal-code/garal-screener (repo moved from garal-labs): #40 `f2f60dd` → develop, #41 `b71b2ca`, #42 `d66d223`,
  #43 `92589f6` (≈1,490 lines: one cohesive flow, ≈720 tests + ≈280 docs), #44 `4f973a6`..this commit, each
  based on the previous branch `feat/importar-csv-0N-*`. pytest at each tip: 251/287/289/331/339 passed. User merges,
  in order, with merge commits.
