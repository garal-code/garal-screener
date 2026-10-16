# Portfolio Calculations (`app/services/calculos.py`)

- Cost basis method: **FIFO** — `calcular_posicion_fifo` consumes buy lots in purchase order when a sale occurs. Any new cost-basis logic must preserve FIFO semantics; do not switch to average-cost without an explicit decision.
- `VentaInvalidaError` — raised when a sale would exceed held quantity (overselling); handle explicitly in callers rather than letting FIFO math silently go negative.
- `PERIODOS_VALIDOS` — whitelist of accepted period codes (e.g. 1m/3m/6m/1y/ytd/all — check current value before adding a new period option) used by `fecha_inicio_periodo` and `calcular_rentabilidad_periodo`.
- Currency conversion: `_precio_en_eur` / `_tipo_cambio` convert non-EUR prices to EUR for aggregate KPIs — instrument-level figures can remain in native currency (dual-currency display), see recent `feat(posiciones)` work exposing both.
- `calcular_resumen_cartera` — aggregate 4 top-level KPIs; known past bug area: period changes must propagate to per-instrument figures too, not just the top-level KPIs (see git history around rentabilidad-por-periodo).
- Price/FX lookups for calculations go through `app/services/precios.py` batch functions (`obtener_precios_batch`, `obtener_fx_batch`, `obtener_precios_by_date_batch`) to avoid N+1 external API calls — reuse these when adding new calculations rather than calling per-instrument fetchers in a loop.
