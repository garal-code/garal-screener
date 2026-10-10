import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth.security import get_owned_cartera
from app.database import get_db
from app.services import calculos, markowitz, precios

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/carteras", tags=["Posiciones"])


@router.get("/{cartera_id}/resumen", response_model=schemas.ResumenCartera)
async def resumen_cartera(
    cartera: models.Cartera = Depends(get_owned_cartera),
    db: Session = Depends(get_db),
):
    """Posiciones con rentabilidades calculadas en tiempo real. Valores en EUR y moneda nativa."""
    instrumentos = (
        db.query(models.Instrumento)
        .join(models.Movimiento)
        .filter(models.Movimiento.cartera_id == cartera.id)
        .distinct()
        .all()
    )

    tickers = [str(i.ticker) for i in instrumentos if i.ticker]
    monedas = [i.moneda for i in instrumentos if i.moneda and i.moneda.upper() != "EUR"]

    # Fetch precios actuales y tipos de cambio en paralelo
    precios_actuales, fx_rates = await asyncio.gather(
        (
            precios.obtener_precios_batch(tickers)
            if tickers
            else asyncio.sleep(0, result={})
        ),
        precios.obtener_fx_batch(monedas) if monedas else asyncio.sleep(0, result={}),
    )

    posiciones_out = []
    plusvalia_realizada_cerradas = 0.0  # acumula P/L de posiciones ya cerradas

    for instrumento in instrumentos:
        movs = (
            db.query(models.Movimiento)
            .filter(
                models.Movimiento.instrumento_id == instrumento.id,
                models.Movimiento.cartera_id == cartera.id,
            )
            .all()
        )

        posicion_fifo = calculos.calcular_posicion_fifo(movs)

        if posicion_fifo["cantidad_actual"] <= 0:
            # Posición cerrada: acumulamos su P/L realizada para el resumen global
            plusvalia_realizada_cerradas += posicion_fifo["plusvalia_realizada"]
            continue

        precio_actual = (
            precios_actuales.get(str(instrumento.ticker))
            if instrumento.ticker
            else None
        )

        # Resolver FX actual: 1.0 para EUR o moneda desconocida
        moneda = instrumento.moneda
        # por que aqui se devuelve uno por defecto si luego ya hay un if else
        fx = (
            fx_rates.get(moneda.upper(), 1.0)
            if moneda and moneda.upper() != "EUR"
            else 1.0
        )

        plusvalias = {}
        precio_actual_eur = None
        if precio_actual is not None:
            plusvalias = calculos.calcular_plusvalia_latente(
                posicion_fifo, precio_actual, fx_actual=fx
            )
            precio_actual_eur = round(precio_actual / fx, 4)

        posiciones_out.append(
            schemas.PosicionOut(
                instrumento=schemas.InstrumentoOut.model_validate(instrumento),
                cantidad_actual=posicion_fifo["cantidad_actual"],
                coste_total=posicion_fifo["coste_total"],
                precio_medio=posicion_fifo["precio_medio"],
                plusvalia_realizada=posicion_fifo["plusvalia_realizada"],
                precio_actual=precio_actual,
                valor_actual=plusvalias.get("valor_actual"),
                plusvalia_latente=plusvalias.get("plusvalia_latente"),
                rentabilidad_pct=plusvalias.get("rentabilidad_pct"),
                plusvalia_total=plusvalias.get("plusvalia_total"),
                # Dual-currency
                precio_actual_eur=precio_actual_eur,
                precio_actual_nativo=precio_actual,
                valor_actual_eur=plusvalias.get("valor_actual_eur"),
                valor_actual_nativo=plusvalias.get("valor_actual_nativo"),
                moneda_nativa=moneda,
                fx_actual=fx if precio_actual is not None else None,
            )
        )

    resumen = calculos.calcular_resumen_cartera(
        [p.model_dump() for p in posiciones_out]
    )
    resumen["plusvalia_realizada"] = round(
        resumen["plusvalia_realizada"] + plusvalia_realizada_cerradas, 2
    )
    resumen["plusvalia_total"] = round(
        resumen["plusvalia_latente"] + resumen["plusvalia_realizada"], 2
    )

    return schemas.ResumenCartera(
        cartera=schemas.CarteraOut.model_validate(cartera),
        posiciones=posiciones_out,
        **resumen,
    )


@router.post("/{cartera_id}/backfill-fx")
async def backfill_fx(
    cartera: models.Cartera = Depends(get_owned_cartera),
    db: Session = Depends(get_db),
):
    """
    Rellena el tipo_cambio histórico para movimientos sin él.
    Solo afecta a instrumentos con moneda != EUR.
    Útil para corregir movimientos importados sin tipo de cambio.
    """
    movimientos = (
        db.query(models.Movimiento)
        .join(models.Instrumento)
        .filter(
            models.Movimiento.cartera_id == cartera.id,
            models.Movimiento.tipo_cambio.is_(None),
            models.Instrumento.moneda.isnot(None),
            models.Instrumento.moneda != "EUR",
        )
        .all()
    )

    # Agrupar por (moneda, fecha) para minimizar llamadas a yfinance
    # str() narrowing: el query ya filtra moneda.isnot(None), pero mypy no lo sabe
    pares_unicos: set[tuple[str, date]] = {
        (str(mov.instrumento.moneda), mov.fecha) for mov in movimientos
    }

    fx_cache: dict[tuple, float | None] = {}
    for moneda, fecha in pares_unicos:
        fx_cache[(moneda, fecha)] = await precios.obtener_fx_by_date(moneda, fecha)

    actualizados = 0
    omitidos = 0
    for mov in movimientos:
        fx = fx_cache.get((mov.instrumento.moneda, mov.fecha))
        if fx is not None:
            mov.tipo_cambio = fx
            actualizados += 1
        else:
            omitidos += 1

    db.commit()
    return {"actualizados": actualizados, "omitidos": omitidos}


@router.get("/{cartera_id}/analisis", response_model=schemas.AnalisisCartera)
async def analisis_cartera(
    cartera: models.Cartera = Depends(get_owned_cartera),
    db: Session = Depends(get_db),
):
    """Desglose por sector, pais, tipo y moneda.

    Ownership is authorized once here via `get_owned_cartera`, then the
    already-authorized `cartera` is forwarded straight into
    `resumen_cartera` (called as a plain function, not through FastAPI's
    routing) so the internal call path doesn't re-derive or skip
    authorization — see design.md's note on this call site.
    """
    resumen = await resumen_cartera(cartera=cartera, db=db)
    posiciones = [p.model_dump() for p in resumen.posiciones]

    posiciones_enriquecidas = []
    for p in posiciones:
        instr = p["instrumento"]
        posiciones_enriquecidas.append(
            {
                **p,
                "sector": instr.get("sector"),
                "pais": instr.get("pais"),
                "tipo": instr.get("tipo"),
                "moneda": instr.get("moneda"),
            }
        )

    return schemas.AnalisisCartera(
        por_sector=[
            schemas.GrupoAnalisis(**g)
            for g in calculos.agrupar_por_campo(posiciones_enriquecidas, "sector")
        ],
        por_pais=[
            schemas.GrupoAnalisis(**g)
            for g in calculos.agrupar_por_campo(posiciones_enriquecidas, "pais")
        ],
        por_tipo=[
            schemas.GrupoAnalisis(**g)
            for g in calculos.agrupar_por_campo(posiciones_enriquecidas, "tipo")
        ],
        por_moneda=[
            schemas.GrupoAnalisis(**g)
            for g in calculos.agrupar_por_campo(posiciones_enriquecidas, "moneda")
        ],
    )


@router.get("/{cartera_id}/rentabilidad", response_model=schemas.RentabilidadCartera)
async def rentabilidad_periodo(
    periodo: str,
    cartera: models.Cartera = Depends(get_owned_cartera),
    db: Session = Depends(get_db),
):
    """
    Rentabilidad de la cartera en un rango de tiempo (1m, 2m, 3m, 6m, 1y, 2y, 3y, ytd).

    Las posiciones que ya existían al inicio del periodo se valoran con su
    precio de mercado en esa fecha (no su coste de compra original); las
    posiciones abiertas dentro del periodo usan su precio de compra real.
    """
    if periodo not in calculos.PERIODOS_VALIDOS:
        raise HTTPException(
            status_code=400,
            detail=f"periodo inválido, usa uno de: {sorted(calculos.PERIODOS_VALIDOS)}",
        )

    hoy = date.today()
    fecha_inicio = calculos.fecha_inicio_periodo(periodo, hoy)

    instrumentos = (
        db.query(models.Instrumento)
        .join(models.Movimiento)
        .filter(models.Movimiento.cartera_id == cartera.id)
        .distinct()
        .all()
    )

    tickers = [str(i.ticker) for i in instrumentos if i.ticker]
    monedas_unicas = {
        i.moneda.upper() for i in instrumentos if i.moneda and i.moneda.upper() != "EUR"
    }

    precios_actuales, precios_inicio, fx_actual_rates, fx_inicio_resultados = (
        await asyncio.gather(
            (
                precios.obtener_precios_batch(tickers)
                if tickers
                else asyncio.sleep(0, result={})
            ),
            (
                precios.obtener_precios_by_date_batch(tickers, fecha_inicio)
                if tickers
                else asyncio.sleep(0, result={})
            ),
            (
                precios.obtener_fx_batch(list(monedas_unicas))
                if monedas_unicas
                else asyncio.sleep(0, result={})
            ),
            (
                asyncio.gather(
                    *[
                        precios.obtener_fx_by_date(m, fecha_inicio)
                        for m in monedas_unicas
                    ],
                    return_exceptions=True,
                )
                if monedas_unicas
                else asyncio.sleep(0, result=[])
            ),
        )
    )
    fx_inicio_rates = {
        moneda: fx
        for moneda, fx in zip(monedas_unicas, fx_inicio_resultados)
        if isinstance(fx, float)
    }  # noqa: B905

    posiciones_out = []
    tickers_sin_dato = []

    for instrumento in instrumentos:
        movs = (
            db.query(models.Movimiento)
            .filter(
                models.Movimiento.instrumento_id == instrumento.id,
                models.Movimiento.cartera_id == cartera.id,
            )
            .all()
        )

        movs_antes = [m for m in movs if m.fecha < fecha_inicio]
        movs_periodo = [m for m in movs if m.fecha >= fecha_inicio]
        cantidad_inicio = calculos.calcular_posicion_fifo(movs_antes)["cantidad_actual"]
        if cantidad_inicio == 0 and not movs_periodo:
            continue  # sin actividad relevante en este periodo

        moneda = instrumento.moneda

        fx_actual = (
            fx_actual_rates.get(moneda.upper(), 1.0)
            if moneda and moneda.upper() != "EUR"
            else 1.0
        )
        precio_actual_nativo = (
            precios_actuales.get(str(instrumento.ticker))
            if instrumento.ticker
            else None
        )
        precio_actual_eur = (
            round(precio_actual_nativo / fx_actual, 4)
            if precio_actual_nativo is not None and fx_actual is not None
            else None
        )

        fx_inicio = (
            fx_inicio_rates.get(moneda.upper())
            if moneda and moneda.upper() != "EUR"
            else 1.0
        )
        precio_inicio_nativo = (
            precios_inicio.get(str(instrumento.ticker)) if instrumento.ticker else None
        )
        precio_inicio_eur = (
            round(precio_inicio_nativo / fx_inicio, 4)
            if precio_inicio_nativo is not None and fx_inicio is not None
            else None
        )

        resultado = calculos.calcular_rentabilidad_periodo(
            movs, fecha_inicio, precio_inicio_eur, precio_actual_eur
        )
        if resultado is None:
            tickers_sin_dato.append(instrumento.ticker or instrumento.isin)
            continue

        posiciones_out.append(
            schemas.PosicionRentabilidadOut(
                instrumento=schemas.InstrumentoOut.model_validate(instrumento),
                cantidad_actual=resultado["cantidad_actual"],
                coste_total=resultado["coste_total"],
                valor_actual=resultado["valor_actual"],
                plusvalia_latente=resultado["plusvalia_latente"],
                plusvalia_realizada=resultado["plusvalia_realizada"],
                plusvalia_total=resultado["plusvalia_total"],
                rentabilidad_pct=resultado["rentabilidad_pct"],
                moneda_nativa=moneda,
            )
        )

    resumen = calculos.calcular_resumen_cartera(
        [p.model_dump() for p in posiciones_out]
    )

    return schemas.RentabilidadCartera(
        periodo=periodo,
        fecha_inicio=fecha_inicio,
        fecha_fin=hoy,
        valor_total=resumen["valor_total"],
        coste_total=resumen["coste_total"],
        plusvalia_latente=resumen["plusvalia_latente"],
        plusvalia_realizada=resumen["plusvalia_realizada"],
        plusvalia_total=resumen["plusvalia_total"],
        rentabilidad_pct=resumen["rentabilidad_pct"],
        posiciones=posiciones_out,
        tickers_sin_dato=tickers_sin_dato,
    )


@router.get(
    "/{cartera_id}/frontera-eficiente",
    response_model=schemas.FronteraEficienteCartera,
)
async def frontera_eficiente(
    anios: int = Query(5, ge=1, le=20, description="Años de histórico mensual"),
    puntos: int = Query(
        markowitz.N_PUNTOS_FRONTERA, ge=2, le=100, description="Puntos de la frontera"
    ),
    cartera: models.Cartera = Depends(get_owned_cartera),
    db: Session = Depends(get_db),
):
    """
    Frontera eficiente de Markowitz (long-only) de las posiciones abiertas.

    Rentabilidades simples mensuales en la moneda nativa de cada activo, sin
    anualizar, en una ventana de `anios` años que acaba en el último mes
    cerrado (el mes en curso nunca se usa). La cartera actual se pondera por
    el valor de mercado EUR de cada posición.

    Como en `/analisis`, la autorización se resuelve una vez con
    `get_owned_cartera` y la cartera ya autorizada se pasa a `resumen_cartera`,
    que aporta las posiciones abiertas (FIFO) valoradas con precios y FX batch.
    """
    resumen = await resumen_cartera(cartera=cartera, db=db)

    excluidos: list[schemas.ActivoExcluido] = []
    valor_por_ticker: dict[str, float] = {}
    for posicion in resumen.posiciones:
        instrumento = posicion.instrumento
        if not instrumento.ticker:
            excluidos.append(
                schemas.ActivoExcluido(ticker=instrumento.isin, motivo="sin_ticker")
            )
        elif not _es_valor_positivo(posicion.valor_actual_eur):
            excluidos.append(
                schemas.ActivoExcluido(
                    ticker=instrumento.ticker, motivo="sin_precio_actual"
                )
            )
        else:
            valor_por_ticker[instrumento.ticker] = (
                valor_por_ticker.get(instrumento.ticker, 0.0)
                + posicion.valor_actual_eur
            )

    if len(valor_por_ticker) < 2:
        # Sin dos activos valorados no tiene sentido consultar el histórico
        raise HTTPException(
            status_code=400,
            detail=_detalle_datos_insuficientes(
                "Se necesitan al menos 2 posiciones abiertas con ticker y precio "
                f"actual (hay {len(valor_por_ticker)}).",
                excluidos,
            ),
        )

    fecha_fin = precios.fin_ultimo_mes_completo()
    # Desde el mes equivalente de hace `anios` años: anios*12 rentabilidades
    fecha_inicio = date(fecha_fin.year - anios, fecha_fin.month, 1)
    try:
        series = await precios.obtener_precios_mensuales_batch(
            list(valor_por_ticker), fecha_inicio, fecha_fin
        )
    except precios.ProveedorPreciosError as e:
        logger.warning(
            "Fallo del proveedor de precios en la frontera eficiente de la "
            "cartera %s: %s",
            cartera.id,
            e,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "No se pudieron obtener los precios históricos del proveedor de "
                "precios. Inténtalo de nuevo más tarde."
            ),
        ) from e

    # Un activo con histórico corto (salida a bolsa reciente) se excluye antes
    # de alinear para que no deje sin datos a toda la cartera
    suficientes, cortos = markowitz.separar_historicos_cortos(series)
    for ticker in valor_por_ticker:
        if ticker in cortos:
            excluidos.append(
                schemas.ActivoExcluido(ticker=ticker, motivo="historico_insuficiente")
            )
        elif ticker not in suficientes:
            excluidos.append(
                schemas.ActivoExcluido(ticker=ticker, motivo="sin_historico")
            )

    try:
        # SLSQP es CPU intensivo: se ejecuta en un hilo para no bloquear el event loop
        resultado = await run_in_threadpool(
            _calcular_frontera, suficientes, valor_por_ticker, puntos
        )
    except markowitz.DatosInsuficientesError as e:
        raise HTTPException(
            status_code=400, detail=_detalle_datos_insuficientes(str(e), excluidos)
        ) from e

    estadisticas = resultado.estadisticas
    frontera = resultado.frontera
    valor_total = sum(valor_por_ticker.values())
    valor_incluido = sum(valor_por_ticker[t] for t in estadisticas.tickers)

    return schemas.FronteraEficienteCartera(
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        n_observaciones=estadisticas.n_observaciones,
        tickers=estadisticas.tickers,
        rentabilidades_esperadas=estadisticas.rentabilidades_esperadas.tolist(),
        matriz_covarianzas=estadisticas.matriz_covarianzas.tolist(),
        frontera=[_punto_cartera_out(p) for p in frontera.puntos],
        cartera_minima_varianza=_punto_cartera_out(frontera.cartera_minima_varianza),
        cartera_actual=_punto_cartera_out(resultado.actual),
        excluidos=excluidos,
        peso_excluido=(valor_total - valor_incluido) / valor_total,
        detalle=_detalle_calculo_out(resultado, valor_por_ticker),
    )


def _es_valor_positivo(valor: float | None) -> bool:
    """Un valor EUR utilizable como peso: conocido, finito y mayor que cero."""
    return valor is not None and math.isfinite(valor) and valor > 0


@dataclass(frozen=True, eq=False)
class _ResultadoFrontera:
    """Todo lo calculado en el hilo, listo para mapear a esquemas."""

    rentabilidades: markowitz.RentabilidadesMensuales
    fechas_precios: list[date]
    matriz_precios: markowitz.Matriz
    estadisticas: markowitz.EstadisticasActivos
    frontera: markowitz.FronteraEficiente
    actual: markowitz.PuntoCartera
    rentabilidades_actual: markowitz.Vector


def _calcular_frontera(
    series: dict[str, list[tuple[date, float]]],
    valor_por_ticker: dict[str, float],
    puntos: int,
) -> _ResultadoFrontera:
    """Cálculo puro (sin E/S) de estadísticas, frontera, cartera actual y detalle."""
    rentabilidades = markowitz.calcular_rentabilidades_mensuales(series)
    estadisticas = markowitz.calcular_estadisticas_desde_rentabilidades(rentabilidades)
    frontera = markowitz.calcular_frontera_eficiente(estadisticas, n_puntos=puntos)
    actual = markowitz.evaluar_cartera(
        estadisticas, {t: valor_por_ticker[t] for t in estadisticas.tickers}
    )
    fechas_precios, matriz_precios = markowitz.alinear_precios_mensuales(
        series, rentabilidades
    )
    return _ResultadoFrontera(
        rentabilidades=rentabilidades,
        fechas_precios=fechas_precios,
        matriz_precios=matriz_precios,
        estadisticas=estadisticas,
        frontera=frontera,
        actual=actual,
        rentabilidades_actual=markowitz.rentabilidades_cartera(
            rentabilidades, actual.pesos
        ),
    )


def _punto_cartera_out(punto: markowitz.PuntoCartera) -> schemas.PuntoCarteraOut:
    return schemas.PuntoCarteraOut(
        rentabilidad=punto.rentabilidad,
        volatilidad=punto.volatilidad,
        varianza=punto.varianza,
        pesos=punto.pesos,
    )


def _detalle_calculo_out(
    resultado: _ResultadoFrontera, valor_por_ticker: dict[str, float]
) -> schemas.DetalleCalculoFrontera:
    estadisticas = resultado.estadisticas
    return schemas.DetalleCalculoFrontera(
        datos=schemas.DatosEntradaFrontera(
            fechas_precios=resultado.fechas_precios,
            precios=resultado.matriz_precios.tolist(),
            fechas=resultado.rentabilidades.fechas,
            rentabilidades=resultado.rentabilidades.matriz.tolist(),
        ),
        estadisticas=schemas.EstadisticasActivosOut(
            varianzas=estadisticas.varianzas.tolist(),
            volatilidades=estadisticas.volatilidades.tolist(),
            matriz_correlaciones=estadisticas.matriz_correlaciones.tolist(),
        ),
        cartera_actual=schemas.DetalleCarteraActual(
            valores_eur={t: valor_por_ticker[t] for t in estadisticas.tickers},
            rentabilidades=resultado.rentabilidades_actual.tolist(),
        ),
    )


def _detalle_datos_insuficientes(
    motivo: str, excluidos: list[schemas.ActivoExcluido]
) -> str:
    detalle = f"No hay datos suficientes para calcular la frontera eficiente. {motivo}"
    if excluidos:
        detalle += " Excluidos: " + ", ".join(
            f"{e.ticker} ({e.motivo})" for e in excluidos
        )
        detalle += "."
    return detalle
