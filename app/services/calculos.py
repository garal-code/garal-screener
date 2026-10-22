"""
Servicio de cálculos financieros.
Toda la lógica de FIFO, plusvalías y rentabilidades vive aquí.
"""

import calendar
from collections import deque
from datetime import date
from typing import Any

from app.models import Movimiento

PERIODOS_VALIDOS = {"1m", "2m", "3m", "6m", "1y", "2y", "3y", "ytd"}


class VentaInvalidaError(ValueError):
    """Se lanza cuando se intenta vender más de lo disponible en los lotes FIFO."""


# Float noise left when a sale consumes several lots (90.485243 - 84.575791 =
# 5.909452000000002): below this, a remainder is not a real quantity
TOLERANCIA_CANTIDAD = 1e-9


def fecha_inicio_periodo(periodo: str, hoy: date | None = None) -> date:
    """
    Calcula la fecha de inicio de un periodo de rentabilidad relativo a hoy.
    Periodos soportados: 1m, 2m, 3m, 6m, 1y, 2y, 3y, ytd.
    """
    if periodo not in PERIODOS_VALIDOS:
        raise ValueError(
            f"Periodo inválido: {periodo!r}. Usa uno de {sorted(PERIODOS_VALIDOS)}."
        )

    hoy = hoy or date.today()

    if periodo == "ytd":
        return date(hoy.year, 1, 1)

    meses_atras = {"1m": 1, "2m": 2, "3m": 3, "6m": 6, "1y": 12, "2y": 24, "3y": 36}[
        periodo
    ]
    total_meses = hoy.year * 12 + (hoy.month - 1) - meses_atras
    año, mes = divmod(total_meses, 12)
    mes += 1
    dia = min(hoy.day, calendar.monthrange(año, mes)[1])
    return date(año, mes, dia)


def calcular_posicion_fifo(
    movimientos: list[Movimiento], lote_inicial: dict | None = None
) -> dict:
    """
    Dado una lista de movimientos ordenados por fecha ASC,
    calcula la posición actual usando el método FIFO.

    Args:
        movimientos: movimientos a procesar (compras/ventas).
        lote_inicial: lote sintético opcional que se añade a la cola FIFO
            antes de procesar `movimientos`, con forma
            {"cantidad": float, "precio_eur": float, "fecha": date}.
            Se usa para calcular rentabilidad de un periodo: representa las
            acciones que ya se tenían al inicio del periodo, valoradas al
            precio de mercado de esa fecha en vez de su coste de compra real.

    Devuelve:
        cantidad_actual     → acciones/participaciones que tienes ahora
        coste_total         → lo que te costaron en EUR (con comisiones)
        precio_medio        → coste_total / cantidad_actual
        plusvalia_realizada → ganancia/pérdida de las ventas ya cerradas
        lotes               → lotes FIFO pendientes (para debug o detalle)
    """
    # Cola FIFO: cada elemento es [cantidad_restante, precio_eur, fecha]
    lotes: deque[list[Any]] = deque()
    if lote_inicial and lote_inicial["cantidad"] > 0:
        lotes.append(
            [
                lote_inicial["cantidad"],
                lote_inicial["precio_eur"],
                lote_inicial["fecha"],
            ]
        )
    plusvalia_realizada = 0.0

    # Orden determinístico: fecha ASC, id ASC para desempate intradía
    for movimientos_ordenados in sorted(movimientos, key=lambda m: (m.fecha, m.id)):
        precio_eur = _precio_en_eur(movimientos_ordenados)
        comision = movimientos_ordenados.comision or 0.0

        if movimientos_ordenados.tipo == "compra":
            # Añadimos lote: precio unitario incluyendo comisión prorrateada
            precio_unitario = precio_eur + (comision / movimientos_ordenados.cantidad)
            lotes.append(
                [
                    movimientos_ordenados.cantidad,
                    precio_unitario,
                    movimientos_ordenados.fecha,
                ]
            )

        elif movimientos_ordenados.tipo == "venta":
            cantidad_vender = movimientos_ordenados.cantidad
            coste_vendido = 0.0

            while cantidad_vender > TOLERANCIA_CANTIDAD and lotes:
                lote = lotes[0]
                cantidad_lote, precio_lote, _ = lote

                if cantidad_lote <= cantidad_vender:
                    # Consumimos el lote entero
                    coste_vendido += cantidad_lote * precio_lote
                    cantidad_vender -= cantidad_lote
                    lotes.popleft()
                else:
                    # Consumimos parte del lote
                    coste_vendido += cantidad_vender * precio_lote
                    lote[0] -= cantidad_vender
                    cantidad_vender = 0

            # Si después del while todavía queda por vender, los datos son inconsistentes
            if cantidad_vender > TOLERANCIA_CANTIDAD:
                raise VentaInvalidaError(
                    f"Movimiento id={movimientos_ordenados.id}: intento de vender {movimientos_ordenados.cantidad} unidades "
                    f"pero los lotes FIFO no tienen suficiente stock."
                )

            # Ingreso de la venta en EUR menos comisión
            # _precio_en_eur divide por tipo_cambio: precio_nativo / tc = EUR
            ingreso_venta = (
                _precio_en_eur(movimientos_ordenados) * movimientos_ordenados.cantidad
            ) - comision
            plusvalia_realizada += ingreso_venta - coste_vendido

    # Calculamos posición actual desde lotes restantes
    cantidad_actual = sum(lote[0] for lote in lotes)
    coste_total = sum(lote[0] * lote[1] for lote in lotes)
    precio_medio = coste_total / cantidad_actual if cantidad_actual > 0 else 0.0

    return {
        "cantidad_actual": round(cantidad_actual, 6),
        "coste_total": round(coste_total, 2),
        "precio_medio": round(precio_medio, 4),
        "plusvalia_realizada": round(plusvalia_realizada, 2),
        "lotes_pendientes": [
            {
                "cantidad": round(lote[0], 6),
                "precio_coste": round(lote[1], 4),
                "fecha": str(lote[2]),
            }
            for lote in lotes
        ],
    }


def _precio_en_eur(mov) -> float:
    """
    Convierte el precio del movimiento a EUR aplicando tipo de cambio.
    Convenio: tipo_cambio sigue EURUSD=X → cuántas unidades de moneda por 1 EUR.
    Para pasar precio nativo → EUR: precio_nativo / tipo_cambio.
    """
    return float(mov.precio) / _tipo_cambio(mov)


def _tipo_cambio(mov) -> float:
    """
    Devuelve el tipo de cambio a aplicar.
    Si no se ha introducido, asume 1.0 (misma moneda que EUR o ya en EUR).
    """
    # is not None para no confundir tipo_cambio=0.0 (inválido) con ausente
    return float(mov.tipo_cambio) if mov.tipo_cambio is not None else 1.0


def calcular_plusvalia_latente(
    posicion: dict,
    precio_actual_nativo: float,
    fx_actual: float = 1.0,
) -> dict:
    """
    Con la posición FIFO calculada, el precio actual en moneda nativa y el
    tipo de cambio actual, calcula la plusvalía latente en EUR y moneda nativa.

    Args:
        posicion: dict devuelto por calcular_posicion_fifo (coste_total en EUR).
        precio_actual_nativo: precio de mercado en la moneda nativa del instrumento.
        fx_actual: tipo de cambio EUR/moneda (convenio Yahoo Finance: EURUSD=X).
            Ej. fx_actual=1.085 significa 1 EUR = 1.085 USD.
            Para convertir a EUR: precio_nativo / fx_actual.
            Para EUR puro: fx_actual=1.0 (default, sin conversión).

    Returns:
        Dict con valor_actual_eur, valor_actual_nativo y métricas en EUR.
        valor_actual es alias de valor_actual_eur para backwards-compatibility.
    """
    cantidad = posicion["cantidad_actual"]
    coste = posicion[
        "coste_total"
    ]  # siempre en EUR (FIFO aplica tipo_cambio histórico)

    valor_actual_nativo = round(precio_actual_nativo * cantidad, 2)
    # fx_actual=1.0 para EUR → sin conversión; para USD: divide por fx
    valor_actual_eur = round(precio_actual_nativo / fx_actual * cantidad, 2)

    plusvalia_latente = round(valor_actual_eur - coste, 2)
    rentabilidad_pct = round((plusvalia_latente / coste * 100), 2) if coste > 0 else 0.0

    return {
        "valor_actual": valor_actual_eur,  # backwards-compat alias
        "valor_actual_eur": valor_actual_eur,
        "valor_actual_nativo": valor_actual_nativo,
        "plusvalia_latente": plusvalia_latente,
        "rentabilidad_pct": rentabilidad_pct,
        "plusvalia_total": round(
            plusvalia_latente + posicion["plusvalia_realizada"], 2
        ),
    }


def calcular_rentabilidad_periodo(
    movimientos: list[Movimiento],
    fecha_inicio,
    precio_inicio_eur: float | None,
    precio_actual_eur: float | None,
) -> dict | None:
    """
    Rentabilidad de una posición durante un periodo [fecha_inicio, hoy].

    Las acciones que ya se tenían antes de `fecha_inicio` se valoran a
    `precio_inicio_eur` (precio de mercado en esa fecha) en vez de a su coste
    de compra real; las compradas dentro del periodo usan su precio de compra
    real. Así el "coste" del periodo refleja el capital en juego al empezar
    a contar, igual que hacen la mayoría de brokers para rentabilidad por
    rango de fechas.

    Devuelve None si la posición ya existía antes del periodo pero no
    tenemos precio histórico para valorarla (dato no disponible).
    """
    movs_antes = [m for m in movimientos if m.fecha < fecha_inicio]
    movs_periodo = [m for m in movimientos if m.fecha >= fecha_inicio]

    cantidad_inicio = calcular_posicion_fifo(movs_antes)["cantidad_actual"]

    if cantidad_inicio > 0 and precio_inicio_eur is None:
        return None

    lote_inicial = (
        {
            "cantidad": cantidad_inicio,
            "precio_eur": precio_inicio_eur,
            "fecha": fecha_inicio,
        }
        if cantidad_inicio > 0
        else None
    )
    posicion = calcular_posicion_fifo(movs_periodo, lote_inicial=lote_inicial)

    cantidad_actual = posicion["cantidad_actual"]
    coste_total = posicion["coste_total"]
    plusvalia_realizada = posicion["plusvalia_realizada"]

    valor_actual = None
    plusvalia_latente = None
    if cantidad_actual > 0 and precio_actual_eur is not None:
        valor_actual = round(cantidad_actual * precio_actual_eur, 2)
        plusvalia_latente = round(valor_actual - coste_total, 2)

    plusvalia_total = round((plusvalia_latente or 0.0) + plusvalia_realizada, 2)
    rentabilidad_pct = (
        round(plusvalia_total / coste_total * 100, 2) if coste_total > 0 else 0.0
    )

    return {
        "cantidad_actual": cantidad_actual,
        "coste_total": coste_total,
        "valor_actual": valor_actual,
        "plusvalia_latente": plusvalia_latente,
        "plusvalia_realizada": round(plusvalia_realizada, 2),
        "plusvalia_total": plusvalia_total,
        "rentabilidad_pct": rentabilidad_pct,
    }


def calcular_resumen_cartera(posiciones: list[dict]) -> dict:
    """
    Agrega todas las posiciones en métricas globales de cartera.
    Cada posición debe tener: valor_actual, coste_total, plusvalia_latente,
    plusvalia_realizada, rentabilidad_pct.
    """
    valor_total = sum(
        p.get("valor_actual") or p.get("coste_total") or 0 for p in posiciones
    )
    coste_total = sum(p.get("coste_total", 0) for p in posiciones)
    plusvalia_latente = sum(p.get("plusvalia_latente") or 0 for p in posiciones)
    plusvalia_realizada = sum(p.get("plusvalia_realizada", 0) for p in posiciones)
    plusvalia_total = plusvalia_latente + plusvalia_realizada
    # Rentabilidad sobre coste total (latente + realizada)
    rentabilidad_total_pct = (
        round((plusvalia_total / coste_total * 100), 2) if coste_total > 0 else 0.0
    )

    return {
        "valor_total": round(valor_total, 2),
        "coste_total": round(coste_total, 2),
        "plusvalia_latente": round(plusvalia_latente, 2),
        "plusvalia_realizada": round(plusvalia_realizada, 2),
        "plusvalia_total": round(plusvalia_total, 2),
        "rentabilidad_pct": rentabilidad_total_pct,
        "num_posiciones": len(
            [p for p in posiciones if p.get("cantidad_actual", 0) > 0]
        ),
    }


def agrupar_por_campo(posiciones: list[dict], campo: str) -> list[dict]:
    """
    Agrupa posiciones por sector, país, tipo, etc.
    Usa valor_actual si hay precio; si no, usa coste_total como fallback.
    Devuelve lista ordenada de mayor a menor peso.
    """
    grupos: dict[str, float] = {}

    for pos in posiciones:
        clave = pos.get(campo) or "Sin clasificar"
        # valor_actual puede ser None si FMP no devolvio precio para ese ticker
        valor = pos.get("valor_actual") or pos.get("coste_total") or 0
        grupos[clave] = grupos.get(clave, 0) + valor

    valor_total = sum(grupos.values())

    resultado = [
        {
            "nombre": k,
            "valor": round(v, 2),
            "peso_pct": round((v / valor_total * 100), 2) if valor_total > 0 else 0,
        }
        for k, v in grupos.items()
    ]
    return sorted(resultado, key=lambda x: x["valor"], reverse=True)


# ── Helpers privados ──────────────────────────────────────────────────────────
