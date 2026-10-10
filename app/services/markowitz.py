"""
Teoría de carteras de Markowitz (media-varianza), cálculo puro.

Sin BD ni red: recibe series de precios mensuales (ver
`precios.obtener_precios_mensuales_batch`) y devuelve dataclasses que la capa
de API traduce a esquemas.

Convenios (alineados con la hoja de referencia "Markowitz - Cartera Eficiente"):
- Rentabilidades simples mensuales, sin anualizar.
- Rentabilidad esperada = media muestral; covarianza muestral (denominador n-1).

A diferencia de la hoja (solución cerrada con ventas en corto), aquí la
frontera es **long-only**: 0 <= w <= 1 y sum(w) = 1, resuelta numéricamente
con SLSQP.
"""

from dataclasses import dataclass
from datetime import date

import numpy as np
import numpy.typing as npt
from scipy.optimize import minimize

Vector = npt.NDArray[np.float64]
Matriz = npt.NDArray[np.float64]

# 12 meses: por debajo, la covarianza de varios activos es poco fiable (y con
# menos observaciones que activos es directamente singular).
MIN_OBSERVACIONES = 12
N_PUNTOS_FRONTERA = 20

# Pesos por debajo de este umbral se consideran ruido numérico del optimizador
_UMBRAL_PESO = 1e-9
# Tolerancia para aceptar como cumplidas las restricciones de igualdad
_TOLERANCIA_RESTRICCION = 1e-7
# Autovalor mínimo relativo al máximo por debajo del cual la covarianza es singular
_TOLERANCIA_SINGULAR = 1e-10


class DatosInsuficientesError(ValueError):
    """Los datos no permiten un análisis de Markowitz fiable."""


@dataclass(frozen=True, eq=False)
class RentabilidadesMensuales:
    """Rentabilidades simples alineadas: matriz (n_meses x n_activos)."""

    tickers: list[str]
    fechas: list[date]
    matriz: Matriz


@dataclass(frozen=True, eq=False)
class EstadisticasActivos:
    """Rentabilidades esperadas y covarianzas mensuales, en el orden de `tickers`."""

    tickers: list[str]
    rentabilidades_esperadas: Vector
    matriz_covarianzas: Matriz
    n_observaciones: int

    @property
    def varianzas(self) -> Vector:
        """Varianza muestral (n-1) de cada activo: diagonal de la covarianza."""
        varianzas: Vector = np.diag(self.matriz_covarianzas).copy()
        return varianzas

    @property
    def volatilidades(self) -> Vector:
        """Desviación típica muestral (n-1) de cada activo."""
        volatilidades: Vector = np.sqrt(np.maximum(self.varianzas, 0.0))
        return volatilidades

    @property
    def matriz_correlaciones(self) -> Matriz:
        """
        Correlaciones derivadas de la covarianza: Σij / (σi·σj).

        Un activo sin variación (σ = 0) no tiene correlación definida: se
        devuelve 0 con el resto (y 1 en la diagonal) para no emitir NaN.
        """
        volatilidades = self.volatilidades
        denominador = np.outer(volatilidades, volatilidades)
        correlaciones: Matriz = np.divide(
            self.matriz_covarianzas,
            denominador,
            out=np.zeros_like(self.matriz_covarianzas),
            where=denominador > 0,
        )
        np.fill_diagonal(correlaciones, 1.0)
        return correlaciones


@dataclass(frozen=True)
class PuntoCartera:
    """Cartera concreta: rentabilidad y volatilidad mensuales, pesos por ticker."""

    rentabilidad: float
    volatilidad: float
    pesos: dict[str, float]

    @property
    def varianza(self) -> float:
        return self.volatilidad**2


@dataclass(frozen=True)
class FronteraEficiente:
    estadisticas: EstadisticasActivos
    cartera_minima_varianza: PuntoCartera
    # Ordenados por rentabilidad creciente; el primero es la de mínima varianza
    puntos: list[PuntoCartera]


# ── Rentabilidades y estadísticas ─────────────────────────────────────────────


def calcular_rentabilidades_mensuales(
    precios: dict[str, list[tuple[date, float]]],
) -> RentabilidadesMensuales:
    """
    Calcula rentabilidades simples mes a mes y las alinea entre activos.

    Solo se calcula la rentabilidad de un mes si existe precio del mes
    anterior (nunca se encadenan dos meses en uno). Después se conservan
    únicamente los meses con rentabilidad para todos los activos: un mes
    ausente en cualquier activo se descarta para todos. Los activos sin
    ninguna rentabilidad se omiten.
    """
    por_ticker: dict[str, dict[date, float]] = {}
    for ticker, serie in precios.items():
        ordenada = sorted((f, p) for f, p in serie if np.isfinite(p) and p > 0)
        rentabilidades = {
            fecha: precio / precio_anterior - 1
            for (fecha_anterior, precio_anterior), (fecha, precio) in zip(
                ordenada, ordenada[1:], strict=False
            )
            if _indice_mes(fecha) - _indice_mes(fecha_anterior) == 1
        }
        if rentabilidades:
            por_ticker[ticker] = rentabilidades

    tickers = list(por_ticker)
    fechas = (
        sorted(set.intersection(*(set(r) for r in por_ticker.values())))
        if tickers
        else []
    )
    matriz = np.array(
        [[por_ticker[t][f] for t in tickers] for f in fechas], dtype=np.float64
    ).reshape(len(fechas), len(tickers))
    return RentabilidadesMensuales(tickers=tickers, fechas=fechas, matriz=matriz)


def calcular_estadisticas_activos(
    precios: dict[str, list[tuple[date, float]]],
    min_observaciones: int = MIN_OBSERVACIONES,
) -> EstadisticasActivos:
    """
    Rentabilidad esperada (media) y covarianza muestral (n-1) de cada activo.

    Lanza DatosInsuficientesError si hay menos de 2 activos con histórico o
    menos de `min_observaciones` meses comunes.
    """
    return calcular_estadisticas_desde_rentabilidades(
        calcular_rentabilidades_mensuales(precios), min_observaciones
    )


def calcular_estadisticas_desde_rentabilidades(
    rentabilidades: RentabilidadesMensuales,
    min_observaciones: int = MIN_OBSERVACIONES,
) -> EstadisticasActivos:
    """Como `calcular_estadisticas_activos`, partiendo de rentabilidades ya alineadas."""
    _validar_numero_activos(len(rentabilidades.tickers))
    n_observaciones = len(rentabilidades.fechas)
    if n_observaciones < min_observaciones:
        raise DatosInsuficientesError(
            f"Se necesitan al menos {min_observaciones} observaciones mensuales "
            f"comunes a todos los activos (hay {n_observaciones})."
        )
    return EstadisticasActivos(
        tickers=rentabilidades.tickers,
        rentabilidades_esperadas=rentabilidades.matriz.mean(axis=0),
        matriz_covarianzas=np.atleast_2d(
            np.cov(rentabilidades.matriz, rowvar=False, ddof=1)
        ),
        n_observaciones=n_observaciones,
    )


def alinear_precios_mensuales(
    precios: dict[str, list[tuple[date, float]]],
    rentabilidades: RentabilidadesMensuales,
) -> tuple[list[date], Matriz]:
    """
    Cierres mensuales que intervienen en `rentabilidades`, alineados.

    El eje de precios contiene cada mes de `rentabilidades.fechas` y su mes
    anterior (primer día de cada mes), ordenados: con meses consecutivos tiene
    exactamente un elemento más que el de rentabilidades. Devuelve
    (fechas_precios, matriz) con filas = fechas_precios y columnas en el orden
    de `rentabilidades.tickers`. Todos los cierres existen: una rentabilidad
    solo se calcula con precio en su mes y en el anterior.
    """
    indices = sorted(
        {i for f in rentabilidades.fechas for i in (_indice_mes(f) - 1, _indice_mes(f))}
    )
    por_ticker = {
        ticker: {
            _indice_mes(f): p
            for f, p in sorted(precios[ticker])
            if np.isfinite(p) and p > 0
        }
        for ticker in rentabilidades.tickers
    }
    matriz = np.array(
        [[por_ticker[t][i] for t in rentabilidades.tickers] for i in indices],
        dtype=np.float64,
    ).reshape(len(indices), len(rentabilidades.tickers))
    return [_fecha_mes(i) for i in indices], matriz


def separar_historicos_cortos(
    precios: dict[str, list[tuple[date, float]]],
    min_observaciones: int = MIN_OBSERVACIONES,
) -> tuple[dict[str, list[tuple[date, float]]], list[str]]:
    """
    Separa los activos con al menos `min_observaciones` rentabilidades
    mensuales propias de los que no las tienen (p. ej. una salida a bolsa
    reciente), para excluirlos antes de alinear: si no, al recortar a los
    meses comunes un solo activo nuevo dejaría a toda la cartera sin datos.

    Devuelve (series_suficientes, tickers_cortos) conservando el orden.
    """
    suficientes: dict[str, list[tuple[date, float]]] = {}
    cortos: list[str] = []
    for ticker, serie in precios.items():
        n_rentabilidades = len(
            calcular_rentabilidades_mensuales({ticker: serie}).fechas
        )
        if n_rentabilidades >= min_observaciones:
            suficientes[ticker] = serie
        else:
            cortos.append(ticker)
    return suficientes, cortos


# ── Carteras ──────────────────────────────────────────────────────────────────


def estadisticas_cartera(
    pesos: Vector, rentabilidades_esperadas: Vector, covarianzas: Matriz
) -> tuple[float, float]:
    """Devuelve (rentabilidad, volatilidad): w·mu y sqrt(wᵀΣw)."""
    rentabilidad = float(pesos @ rentabilidades_esperadas)
    varianza = float(pesos @ covarianzas @ pesos)
    return rentabilidad, float(np.sqrt(max(varianza, 0.0)))


def evaluar_cartera(
    estadisticas: EstadisticasActivos, pesos: dict[str, float]
) -> PuntoCartera:
    """
    Sitúa una cartera arbitraria en el plano rentabilidad-volatilidad.

    Los tickers ausentes de `pesos` valen 0 y los que no están en
    `estadisticas` se ignoran; los pesos resultantes se renormalizan a 1.
    """
    vector = np.array(
        [pesos.get(t, 0.0) for t in estadisticas.tickers], dtype=np.float64
    )
    if np.any(vector < 0) or vector.sum() <= 0:
        raise ValueError(
            "Los pesos deben ser no negativos y sumar más de 0 entre los activos "
            "analizados."
        )
    return _punto(estadisticas, vector / vector.sum())


def rentabilidades_cartera(
    rentabilidades: RentabilidadesMensuales, pesos: dict[str, float]
) -> Vector:
    """
    Rentabilidad mensual de una cartera de pesos fijos: w·r de cada mes.

    Es la columna "PORTFOLIO" de la hoja; los tickers ausentes de `pesos`
    valen 0 y los pesos se usan tal cual (sin renormalizar).
    """
    vector = np.array(
        [pesos.get(t, 0.0) for t in rentabilidades.tickers], dtype=np.float64
    )
    serie: Vector = rentabilidades.matriz @ vector
    return serie


def calcular_cartera_minima_varianza(
    estadisticas: EstadisticasActivos,
) -> PuntoCartera:
    """Cartera long-only de mínima varianza: min wᵀΣw, sum(w) = 1, 0 <= w <= 1."""
    _validar_estadisticas(estadisticas)
    return _punto(estadisticas, _pesos_minima_varianza(estadisticas))


def calcular_frontera_eficiente(
    estadisticas: EstadisticasActivos, n_puntos: int = N_PUNTOS_FRONTERA
) -> FronteraEficiente:
    """
    Frontera eficiente long-only.

    Toma `n_puntos` rentabilidades objetivo equiespaciadas entre la de la
    cartera de mínima varianza y la del mejor activo (el máximo alcanzable
    sin apalancamiento) y, para cada una, minimiza la varianza. Los objetivos
    que el optimizador no resuelve se omiten. Si la cartera de mínima
    varianza ya es la más rentable, la frontera es ese único punto.
    """
    if n_puntos < 2:
        raise ValueError("n_puntos debe ser al menos 2.")
    _validar_estadisticas(estadisticas)

    mu = estadisticas.rentabilidades_esperadas
    pesos_minima = _pesos_minima_varianza(estadisticas)
    minima = _punto(estadisticas, pesos_minima)

    indice_mejor = int(np.argmax(mu))
    rentabilidad_maxima = float(mu[indice_mejor])
    rango = rentabilidad_maxima - minima.rentabilidad
    if rango <= _TOLERANCIA_RESTRICCION:
        return FronteraEficiente(estadisticas, minima, [minima])

    mejor_activo = np.zeros(len(mu))
    mejor_activo[indice_mejor] = 1.0

    puntos = [minima]
    objetivos = np.linspace(minima.rentabilidad, rentabilidad_maxima, n_puntos)
    for objetivo in objetivos[1:]:
        # Punto de partida factible: mezcla de la mínima varianza y el mejor
        # activo que alcanza exactamente la rentabilidad objetivo.
        alfa = (objetivo - minima.rentabilidad) / rango
        inicial = (1 - alfa) * pesos_minima + alfa * mejor_activo
        pesos = _minimizar_varianza(
            estadisticas.matriz_covarianzas, inicial, mu, float(objetivo)
        )
        if pesos is not None:
            puntos.append(_punto(estadisticas, pesos))
    return FronteraEficiente(estadisticas, minima, puntos)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _indice_mes(fecha: date) -> int:
    return fecha.year * 12 + fecha.month


def _fecha_mes(indice: int) -> date:
    """Inversa de `_indice_mes`: primer día del mes."""
    return date((indice - 1) // 12, (indice - 1) % 12 + 1, 1)


def _validar_numero_activos(n_activos: int) -> None:
    if n_activos < 2:
        raise DatosInsuficientesError(
            "Se necesitan al menos 2 activos con histórico de precios suficiente "
            f"(hay {n_activos})."
        )


def _validar_estadisticas(estadisticas: EstadisticasActivos) -> None:
    """Exige al menos 2 activos y una covarianza finita y no singular."""
    _validar_numero_activos(len(estadisticas.tickers))
    covarianzas = estadisticas.matriz_covarianzas
    if not np.all(np.isfinite(covarianzas)) or not np.all(
        np.isfinite(estadisticas.rentabilidades_esperadas)
    ):
        raise DatosInsuficientesError("Las estadísticas contienen valores no finitos.")
    autovalores = np.linalg.eigvalsh(covarianzas)
    if autovalores[-1] <= 0 or autovalores[0] <= _TOLERANCIA_SINGULAR * autovalores[-1]:
        raise DatosInsuficientesError(
            "La matriz de covarianzas es singular: hay activos sin variación o "
            "linealmente dependientes entre sí."
        )


def _pesos_minima_varianza(estadisticas: EstadisticasActivos) -> Vector:
    n_activos = len(estadisticas.tickers)
    pesos = _minimizar_varianza(
        estadisticas.matriz_covarianzas, np.full(n_activos, 1.0 / n_activos)
    )
    if pesos is None:
        raise DatosInsuficientesError(
            "No se pudo calcular la cartera de mínima varianza."
        )
    return pesos


def _minimizar_varianza(
    covarianzas: Matriz,
    inicial: Vector,
    rentabilidades_esperadas: Vector | None = None,
    objetivo: float | None = None,
) -> Vector | None:
    """
    min wᵀΣw  s.a. sum(w) = 1, 0 <= w <= 1 y, si se indica, w·mu = objetivo.

    Devuelve None si SLSQP no converge o no cumple las restricciones.
    """
    n_activos = len(inicial)
    unos = np.ones(n_activos)
    # Escalar la varianza a O(1) para que la tolerancia de SLSQP sea significativa
    escala = 1.0 / float(np.mean(np.diag(covarianzas)))

    restricciones: list[dict[str, object]] = [
        {"type": "eq", "fun": lambda w: w.sum() - 1.0, "jac": lambda w: unos}
    ]
    if rentabilidades_esperadas is not None and objetivo is not None:
        mu = rentabilidades_esperadas
        restricciones.append(
            {"type": "eq", "fun": lambda w: w @ mu - objetivo, "jac": lambda w: mu}
        )

    resultado = minimize(
        lambda w: escala * (w @ covarianzas @ w),
        inicial,
        jac=lambda w: 2.0 * escala * (covarianzas @ w),
        method="SLSQP",
        bounds=[(0.0, 1.0)] * n_activos,
        constraints=restricciones,
        options={"ftol": 1e-12, "maxiter": 500},
    )
    if not resultado.success:
        return None

    pesos = np.asarray(resultado.x, dtype=np.float64)
    if abs(pesos.sum() - 1.0) > _TOLERANCIA_RESTRICCION or (
        rentabilidades_esperadas is not None
        and objetivo is not None
        and abs(pesos @ rentabilidades_esperadas - objetivo) > _TOLERANCIA_RESTRICCION
    ):
        return None
    if pesos.min() < -_TOLERANCIA_RESTRICCION:
        return None

    limpios: Vector = np.where(pesos < _UMBRAL_PESO, 0.0, np.minimum(pesos, 1.0))
    normalizados: Vector = limpios / float(limpios.sum())
    return normalizados


def _punto(estadisticas: EstadisticasActivos, pesos: Vector) -> PuntoCartera:
    rentabilidad, volatilidad = estadisticas_cartera(
        pesos, estadisticas.rentabilidades_esperadas, estadisticas.matriz_covarianzas
    )
    return PuntoCartera(
        rentabilidad=rentabilidad,
        volatilidad=volatilidad,
        pesos={t: float(w) for t, w in zip(estadisticas.tickers, pesos, strict=True)},
    )
