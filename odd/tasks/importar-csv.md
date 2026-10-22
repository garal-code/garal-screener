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
  `moneda_precio`, `referencia` optional}, `valores_compra` / `valores_venta` (non-empty, disjoint,
  case-insensitive exact match), `invertir_tipo_cambio` (bool).
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

## Tasks

- [x] T1 — S2, S7 · Backend model, migration, template config schema, template CRUD + tests · inline ·
  commit `f2f60dd`
- [x] T2 — S1, S3, S5, S6 · Backend CSV parsing/normalization service + unit tests (synthetic T212- and
  TR-shaped fixtures, plus a `;`/decimal-comma/`%d/%m/%Y` case) · inline · commit: the one adding
  `app/services/importacion.py`
- [ ] T3 — S4, S5, S6, S7 · Backend inspect, preview, confirm endpoints + `docs/importacion.md` + tests · inline
  · commit: pending
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
