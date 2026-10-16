"""
Tests unitarios para app/services/markowitz.py

Cálculo puro (sin BD ni red). Como oráculo se usa la hoja de referencia
"Markowitz - Cartera Eficiente.xlsx": covarianza muestral de 5 activos y la
solución cerrada del caso sin restricciones (ventas en corto permitidas).
"""

from datetime import date

import numpy as np
import pytest

from app.services.markowitz import (
    DatosInsuficientesError,
    EstadisticasActivos,
    alinear_precios_mensuales,
    calcular_cartera_minima_varianza,
    calcular_estadisticas_activos,
    calcular_frontera_eficiente,
    calcular_rentabilidades_mensuales,
    evaluar_cartera,
    rentabilidades_cartera,
    separar_historicos_cortos,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

TICKERS_HOJA = ["AMZN", "AAPL", "NVDA", "MSFT", "TSLA"]

COVARIANZAS_HOJA = np.array(
    [
        [0.002986616, 0.000259343, 0.000381061, -0.000186111, 0.000130808],
        [0.000259343, 0.002357364, 7.48485e-06, 0.000350333, 9.86465e-05],
        [0.000381061, 7.48485e-06, 0.001427667, 3.79495e-05, 0.000110242],
        [-0.000186111, 0.000350333, 3.79495e-05, 0.002015061, -0.000124424],
        [0.000130808, 9.86465e-05, 0.000110242, -0.000124424, 0.003747111],
    ]
)

RENTABILIDADES_HOJA = np.array([0.013, 0.019, 0.043, 0.010, 0.020])


@pytest.fixture
def estadisticas_hoja() -> EstadisticasActivos:
    return EstadisticasActivos(
        tickers=TICKERS_HOJA,
        rentabilidades_esperadas=RENTABILIDADES_HOJA,
        matriz_covarianzas=COVARIANZAS_HOJA,
        n_observaciones=60,
    )


def _meses(n: int, inicio: date = date(2020, 1, 1)) -> list[date]:
    fechas = []
    anio, mes = inicio.year, inicio.month
    for _ in range(n):
        fechas.append(date(anio, mes, 1))
        anio, mes = (anio + 1, 1) if mes == 12 else (anio, mes + 1)
    return fechas


def _serie(precios: list[float]) -> list[tuple[date, float]]:
    return list(zip(_meses(len(precios)), precios, strict=True))


# Precios inventados pero deterministas, 13 meses -> 12 rentabilidades
PRECIOS_A = [100, 103, 101, 106, 108, 104, 110, 113, 111, 117, 115, 120, 124]
PRECIOS_B = [50, 49, 52, 51, 55, 57, 54, 58, 60, 59, 63, 62, 61]
PRECIOS_C = [20, 21, 21.5, 20.8, 22, 23.1, 22.4, 23.9, 24.5, 23.8, 25.2, 26, 25.5]


# ── Rentabilidades y estadísticas ─────────────────────────────────────────────


class TestRentabilidadesMensuales:
    def test_rentabilidades_simples_alineadas(self):
        precios = {
            "A": _serie([100.0, 110.0, 99.0]),
            "B": _serie([50.0, 55.0, 66.0]),
        }
        r = calcular_rentabilidades_mensuales(precios)
        assert r.tickers == ["A", "B"]
        assert r.fechas == [date(2020, 2, 1), date(2020, 3, 1)]
        np.testing.assert_allclose(r.matriz, [[0.10, 0.10], [-0.10, 0.20]])

    def test_mes_ausente_en_un_activo_descarta_ese_mes_para_todos(self):
        # B no tiene precio en marzo: se pierden las rentabilidades de marzo
        # (feb->mar) y abril (mar->abr); nunca se encadenan dos meses.
        meses = _meses(5)
        precios = {
            "A": list(zip(meses, [100.0, 110.0, 121.0, 133.1, 146.41], strict=True)),
            "B": [
                (m, p)
                for m, p in zip(meses, [10.0, 11.0, 0, 13.0, 14.3], strict=True)
                if p
            ],
        }
        r = calcular_rentabilidades_mensuales(precios)
        assert r.fechas == [date(2020, 2, 1), date(2020, 5, 1)]
        np.testing.assert_allclose(r.matriz, [[0.10, 0.10], [0.10, 0.10]])


class TestEstadisticasActivos:
    def test_media_y_covarianza_muestral_n_menos_1(self):
        precios = {
            "A": _serie(PRECIOS_A),
            "B": _serie(PRECIOS_B),
            "C": _serie(PRECIOS_C),
        }
        est = calcular_estadisticas_activos(precios)

        matriz = np.column_stack(
            [np.diff(p) / np.array(p[:-1]) for p in (PRECIOS_A, PRECIOS_B, PRECIOS_C)]
        )
        assert est.tickers == ["A", "B", "C"]
        assert est.n_observaciones == 12
        np.testing.assert_allclose(est.rentabilidades_esperadas, matriz.mean(axis=0))
        np.testing.assert_allclose(est.matriz_covarianzas, np.cov(matriz, rowvar=False))
        # Denominador n-1 (muestral), no n (poblacional)
        np.testing.assert_allclose(
            est.matriz_covarianzas[0, 0], matriz[:, 0].var(ddof=1)
        )

    def test_menos_de_dos_activos_lanza_error(self):
        with pytest.raises(DatosInsuficientesError, match="2 activos"):
            calcular_estadisticas_activos({"A": _serie(PRECIOS_A)})

    def test_activo_sin_rentabilidades_cuenta_como_ausente(self):
        with pytest.raises(DatosInsuficientesError, match="2 activos"):
            calcular_estadisticas_activos({"A": _serie(PRECIOS_A), "B": _serie([50.0])})

    def test_pocas_observaciones_lanza_error(self):
        # 12 precios -> 11 rentabilidades < mínimo de 12
        with pytest.raises(DatosInsuficientesError, match="observaciones"):
            calcular_estadisticas_activos(
                {"A": _serie(PRECIOS_A[:12]), "B": _serie(PRECIOS_B[:12])}
            )

    def test_minimo_de_observaciones_configurable(self):
        est = calcular_estadisticas_activos(
            {"A": _serie(PRECIOS_A[:4]), "B": _serie(PRECIOS_B[:4])},
            min_observaciones=3,
        )
        assert est.n_observaciones == 3


# ── Cartera de mínima varianza ────────────────────────────────────────────────


class TestCarteraMinimaVarianza:
    def test_oraculo_cerrado_de_la_hoja(self):
        # Caso sin restricciones: D = 1'S^-1 1, varianza mínima 1/D, rentabilidad B/D
        inversa = np.linalg.inv(COVARIANZAS_HOJA)
        unos = np.ones(5)
        d = unos @ inversa @ unos
        b = unos @ inversa @ RENTABILIDADES_HOJA
        assert d == pytest.approx(1881.05, abs=0.01)
        assert 1 / d == pytest.approx(0.000531617, rel=1e-5)
        assert b / d == pytest.approx(0.0237, abs=1e-4)

    def test_long_only_coincide_con_solucion_cerrada(self, estadisticas_hoja):
        # En la hoja los pesos sin restricciones ya son todos positivos, así que
        # la solución long-only debe coincidir con la cerrada S^-1 1 / D.
        inversa = np.linalg.inv(COVARIANZAS_HOJA)
        pesos_cerrados = inversa @ np.ones(5) / (np.ones(5) @ inversa @ np.ones(5))

        cartera = calcular_cartera_minima_varianza(estadisticas_hoja)

        pesos = [cartera.pesos[t] for t in TICKERS_HOJA]
        np.testing.assert_allclose(pesos, pesos_cerrados, atol=1e-3)
        np.testing.assert_allclose(
            pesos, [0.1325, 0.1674, 0.3193, 0.2491, 0.1317], atol=1e-3
        )
        assert cartera.volatilidad**2 == pytest.approx(0.000531617, rel=1e-4)
        assert cartera.volatilidad == pytest.approx(0.02306, abs=1e-4)
        assert cartera.rentabilidad == pytest.approx(0.02376, abs=1e-4)

    def test_long_only_no_admite_pesos_negativos(self):
        # Dos activos muy correlacionados con distinta varianza: sin restricciones
        # el óptimo vendería en corto el más volátil.
        est = EstadisticasActivos(
            tickers=["X", "Y"],
            rentabilidades_esperadas=np.array([0.01, 0.02]),
            matriz_covarianzas=np.array([[0.0010, 0.0018], [0.0018, 0.0040]]),
            n_observaciones=60,
        )
        cartera = calcular_cartera_minima_varianza(est)
        assert cartera.pesos == {"X": pytest.approx(1.0), "Y": pytest.approx(0.0)}
        assert cartera.volatilidad == pytest.approx(np.sqrt(0.0010))

    def test_covarianza_singular_lanza_error(self):
        precios = {"A": _serie(PRECIOS_A), "B": _serie([p * 2 for p in PRECIOS_A])}
        est = calcular_estadisticas_activos(precios)
        with pytest.raises(DatosInsuficientesError, match="singular"):
            calcular_cartera_minima_varianza(est)

    def test_activo_con_precio_constante_lanza_error(self):
        precios = {"A": _serie(PRECIOS_A), "B": _serie([10.0] * 13)}
        est = calcular_estadisticas_activos(precios)
        with pytest.raises(DatosInsuficientesError, match="singular"):
            calcular_cartera_minima_varianza(est)


# ── Frontera eficiente ────────────────────────────────────────────────────────


class TestFronteraEficiente:
    def test_pesos_validos_en_todos_los_puntos(self, estadisticas_hoja):
        frontera = calcular_frontera_eficiente(estadisticas_hoja, n_puntos=20)
        assert len(frontera.puntos) == 20
        for punto in frontera.puntos:
            assert list(punto.pesos) == TICKERS_HOJA
            assert all(0.0 <= w <= 1.0 for w in punto.pesos.values())
            assert sum(punto.pesos.values()) == pytest.approx(1.0, abs=1e-6)

    def test_rentabilidad_creciente_y_volatilidad_no_decreciente(
        self, estadisticas_hoja
    ):
        frontera = calcular_frontera_eficiente(estadisticas_hoja, n_puntos=20)
        rentabilidades = [p.rentabilidad for p in frontera.puntos]
        volatilidades = [p.volatilidad for p in frontera.puntos]
        assert all(np.diff(rentabilidades) > 0)
        assert all(np.diff(volatilidades) >= -1e-9)

    def test_minima_varianza_es_el_punto_menos_volatil(self, estadisticas_hoja):
        frontera = calcular_frontera_eficiente(estadisticas_hoja, n_puntos=20)
        minima = frontera.cartera_minima_varianza
        assert all(minima.volatilidad <= p.volatilidad + 1e-9 for p in frontera.puntos)
        assert frontera.puntos[0].rentabilidad == pytest.approx(minima.rentabilidad)

    def test_ultimo_punto_es_el_mejor_activo(self, estadisticas_hoja):
        frontera = calcular_frontera_eficiente(estadisticas_hoja, n_puntos=20)
        ultimo = frontera.puntos[-1]
        assert ultimo.pesos["NVDA"] == pytest.approx(1.0, abs=1e-6)
        assert ultimo.rentabilidad == pytest.approx(0.043, abs=1e-6)
        assert ultimo.volatilidad == pytest.approx(np.sqrt(0.001427667), abs=1e-6)

    def test_puntos_minimos(self, estadisticas_hoja):
        with pytest.raises(ValueError, match="n_puntos"):
            calcular_frontera_eficiente(estadisticas_hoja, n_puntos=1)

    def test_menos_de_dos_activos_lanza_error(self):
        est = EstadisticasActivos(
            tickers=["A"],
            rentabilidades_esperadas=np.array([0.01]),
            matriz_covarianzas=np.array([[0.001]]),
            n_observaciones=60,
        )
        with pytest.raises(DatosInsuficientesError, match="2 activos"):
            calcular_frontera_eficiente(est)

    def test_minima_varianza_es_el_mejor_activo_devuelve_frontera_degenerada(self):
        # El activo de menor varianza es también el más rentable: la frontera
        # long-only se reduce a un único punto.
        est = EstadisticasActivos(
            tickers=["X", "Y"],
            rentabilidades_esperadas=np.array([0.03, 0.01]),
            matriz_covarianzas=np.array([[0.0010, 0.0018], [0.0018, 0.0040]]),
            n_observaciones=60,
        )
        frontera = calcular_frontera_eficiente(est, n_puntos=20)
        assert len(frontera.puntos) == 1
        assert frontera.puntos[0].pesos["X"] == pytest.approx(1.0)


# ── Evaluación de una cartera arbitraria ──────────────────────────────────────


class TestEvaluarCartera:
    def test_rentabilidad_y_volatilidad_de_pesos_dados(self, estadisticas_hoja):
        pesos = dict.fromkeys(TICKERS_HOJA, 0.2)
        punto = evaluar_cartera(estadisticas_hoja, pesos)
        w = np.full(5, 0.2)
        assert punto.rentabilidad == pytest.approx(w @ RENTABILIDADES_HOJA)
        assert punto.volatilidad == pytest.approx(np.sqrt(w @ COVARIANZAS_HOJA @ w))

    def test_pesos_se_renormalizan_y_ausentes_valen_cero(self, estadisticas_hoja):
        punto = evaluar_cartera(estadisticas_hoja, {"NVDA": 3.0, "MSFT": 1.0})
        assert punto.pesos == {
            "AMZN": 0.0,
            "AAPL": 0.0,
            "NVDA": pytest.approx(0.75),
            "MSFT": pytest.approx(0.25),
            "TSLA": 0.0,
        }

    def test_pesos_nulos_lanzan_error(self, estadisticas_hoja):
        with pytest.raises(ValueError, match="pesos"):
            evaluar_cartera(estadisticas_hoja, {"OTRO": 1.0})


class TestSepararHistoricosCortos:
    def test_activos_con_menos_rentabilidades_que_el_minimo_se_separan(self):
        precios = {
            "A": _serie(PRECIOS_A),  # 13 precios -> 12 rentabilidades
            "NUEVO": _serie(PRECIOS_B[:6]),  # 6 precios -> 5 rentabilidades
            "C": _serie(PRECIOS_C),
            "VACIO": [],
        }
        suficientes, cortos = separar_historicos_cortos(precios, min_observaciones=12)
        assert list(suficientes) == ["A", "C"]
        assert suficientes["A"] == precios["A"]
        assert cortos == ["NUEVO", "VACIO"]

    def test_huecos_no_cuentan_como_rentabilidad(self):
        # 13 precios pero falta un mes intermedio: solo 11 rentabilidades propias
        serie = _serie(PRECIOS_A + [130])
        con_hueco = serie[:5] + serie[6:]
        suficientes, cortos = separar_historicos_cortos(
            {"A": con_hueco}, min_observaciones=12
        )
        assert suficientes == {}
        assert cortos == ["A"]


# ── Detalle del cálculo ───────────────────────────────────────────────────────


class TestAlinearPreciosMensuales:
    def test_meses_consecutivos_tienen_un_precio_mas_que_rentabilidades(self):
        precios = {
            "A": _serie(PRECIOS_A),
            "B": _serie(PRECIOS_B),
        }
        r = calcular_rentabilidades_mensuales(precios)
        fechas_precios, matriz = alinear_precios_mensuales(precios, r)

        assert fechas_precios == _meses(13)
        assert len(fechas_precios) == len(r.fechas) + 1
        assert matriz.shape == (13, 2)
        np.testing.assert_allclose(matriz[:, 0], PRECIOS_A)
        np.testing.assert_allclose(matriz[:, 1], PRECIOS_B)
        # Cada rentabilidad sale de dos cierres consecutivos del eje de precios
        np.testing.assert_allclose(r.matriz, matriz[1:] / matriz[:-1] - 1)

    def test_con_huecos_incluye_el_mes_anterior_de_cada_rentabilidad(self):
        meses = _meses(5)
        precios = {
            "A": list(zip(meses, [100.0, 110.0, 121.0, 133.1, 146.41], strict=True)),
            "B": [
                (m, p)
                for m, p in zip(meses, [10.0, 11.0, 0, 13.0, 14.3], strict=True)
                if p
            ],
        }
        r = calcular_rentabilidades_mensuales(precios)
        fechas_precios, matriz = alinear_precios_mensuales(precios, r)

        # Rentabilidades de feb y may: precios de ene, feb, abr y may
        assert r.fechas == [date(2020, 2, 1), date(2020, 5, 1)]
        assert fechas_precios == [
            date(2020, 1, 1),
            date(2020, 2, 1),
            date(2020, 4, 1),
            date(2020, 5, 1),
        ]
        np.testing.assert_allclose(
            matriz, [[100.0, 10.0], [110.0, 11.0], [133.1, 13.0], [146.41, 14.3]]
        )


class TestEstadisticasDescriptivas:
    def test_varianza_volatilidad_y_correlaciones(self):
        precios = {
            "A": _serie(PRECIOS_A),
            "B": _serie(PRECIOS_B),
            "C": _serie(PRECIOS_C),
        }
        r = calcular_rentabilidades_mensuales(precios)
        est = calcular_estadisticas_activos(precios)

        np.testing.assert_allclose(est.varianzas, r.matriz.var(axis=0, ddof=1))
        np.testing.assert_allclose(est.volatilidades, r.matriz.std(axis=0, ddof=1))
        correlaciones = est.matriz_correlaciones
        np.testing.assert_allclose(np.diag(correlaciones), 1.0)
        np.testing.assert_allclose(correlaciones, correlaciones.T)
        np.testing.assert_allclose(correlaciones, np.corrcoef(r.matriz, rowvar=False))

    def test_activo_sin_variacion_no_produce_valores_no_finitos(self):
        precios = {"A": _serie(PRECIOS_A), "B": _serie([10.0] * 13)}
        est = calcular_estadisticas_activos(precios)
        correlaciones = est.matriz_correlaciones
        assert np.all(np.isfinite(correlaciones))
        np.testing.assert_allclose(correlaciones, [[1.0, 0.0], [0.0, 1.0]])


class TestRentabilidadesCartera:
    def test_serie_mensual_es_pesos_por_rentabilidades(self):
        precios = {"A": _serie(PRECIOS_A), "B": _serie(PRECIOS_B)}
        r = calcular_rentabilidades_mensuales(precios)
        serie = rentabilidades_cartera(r, {"A": 0.25, "B": 0.75})
        assert serie.shape == (12,)
        np.testing.assert_allclose(serie, r.matriz @ np.array([0.25, 0.75]))

    def test_media_coincide_con_la_rentabilidad_de_la_cartera(self):
        precios = {
            "A": _serie(PRECIOS_A),
            "B": _serie(PRECIOS_B),
            "C": _serie(PRECIOS_C),
        }
        r = calcular_rentabilidades_mensuales(precios)
        est = calcular_estadisticas_activos(precios)
        punto = evaluar_cartera(est, {"A": 1.0, "B": 2.0, "C": 1.0})
        serie = rentabilidades_cartera(r, punto.pesos)
        assert serie.mean() == pytest.approx(punto.rentabilidad)
        assert serie.var(ddof=1) == pytest.approx(punto.varianza)


class TestPuntoCarteraVarianza:
    def test_varianza_es_el_cuadrado_de_la_volatilidad(self, estadisticas_hoja):
        frontera = calcular_frontera_eficiente(estadisticas_hoja, n_puntos=5)
        for punto in [*frontera.puntos, frontera.cartera_minima_varianza]:
            assert punto.varianza == pytest.approx(punto.volatilidad**2)
        assert frontera.cartera_minima_varianza.varianza == pytest.approx(
            0.000531617, rel=1e-4
        )
