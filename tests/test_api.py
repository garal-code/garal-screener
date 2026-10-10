"""
Tests de integración para los endpoints de la API.
Todos los tests usan SQLite en memoria (ver conftest.py).
Las llamadas externas (FMP, Anthropic) se mockean.
"""

import logging
import re
from contextlib import ExitStack
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from app import models
from app.auth.security import generate_reset_token
from app.services.precios import ProveedorPreciosError

BASE = "/api/v1"

# ── Carteras ──────────────────────────────────────────────────────────────────


class TestCarteras:
    def test_crear_cartera(self, auth_client):
        r = auth_client.post(f"{BASE}/carteras", json={"nombre": "Mi cartera"})
        assert r.status_code == 200
        data = r.json()
        assert data["nombre"] == "Mi cartera"
        assert "id" in data

    def test_crear_cartera_con_descripcion(self, auth_client):
        r = auth_client.post(
            f"{BASE}/carteras",
            json={"nombre": "Cartera 2", "descripcion": "Inversiones largo plazo"},
        )
        assert r.status_code == 200
        assert r.json()["descripcion"] == "Inversiones largo plazo"

    def test_listar_carteras_incluye_la_por_defecto(self, auth_client):
        # Registration auto-creates "Mi Cartera Principal" — a fresh user is
        # never truly cartera-less.
        r = auth_client.get(f"{BASE}/carteras")
        assert r.status_code == 200
        assert [c["nombre"] for c in r.json()] == ["Mi Cartera Principal"]

    def test_listar_carteras_con_datos(self, auth_client):
        auth_client.post(f"{BASE}/carteras", json={"nombre": "A"})
        auth_client.post(f"{BASE}/carteras", json={"nombre": "B"})
        r = auth_client.get(f"{BASE}/carteras")
        assert r.status_code == 200
        assert len(r.json()) == 3  # default cartera + A + B

    def test_eliminar_cartera(self, auth_client):
        cartera_id = auth_client.post(f"{BASE}/carteras", json={"nombre": "X"}).json()[
            "id"
        ]
        r = auth_client.delete(f"{BASE}/carteras/{cartera_id}")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        # "X" is gone, only the default cartera from registration remains
        carteras = auth_client.get(f"{BASE}/carteras").json()
        assert [c["nombre"] for c in carteras] == ["Mi Cartera Principal"]

    def test_eliminar_cartera_inexistente(self, auth_client):
        r = auth_client.delete(f"{BASE}/carteras/9999")
        assert r.status_code == 404


# ── Movimientos ───────────────────────────────────────────────────────────────

MOCK_IA = {
    "nombre": "Apple Inc.",
    "tipo": "accion",
    "sector": "Tecnología",
    "pais": "Estados Unidos",
    "moneda": "USD",
    "exchange": "NASDAQ",
}


def mock_precios(fx_rates=None, precios_historicos=None):
    """Contexto que parchea las llamadas externas de precios/yfinance.

    Args:
        fx_rates: dict opcional con tipos de cambio a simular, ej. {"USD": 1.085}.
            Por defecto {} → fx fallback 1.0 para todas las monedas (comportamiento anterior).
        precios_historicos: dict opcional con precios históricos a simular para
            /rentabilidad, ej. {"AAPL": 100.0}. Por defecto {"AAPL": 100.0}.
    """
    if fx_rates is None:
        fx_rates = {}
    if precios_historicos is None:
        precios_historicos = {"AAPL": 100.0}

    stack = ExitStack()
    stack.enter_context(
        patch.multiple(
            "app.routers.movimientos.precios",
            enriquecer_por_isin=AsyncMock(return_value={**MOCK_IA, "ticker": "AAPL"}),
        )
    )
    stack.enter_context(
        patch.multiple(
            "app.routers.posiciones.precios",
            obtener_precios_batch=AsyncMock(return_value={"AAPL": 150.0}),
            obtener_precios_by_date_batch=AsyncMock(return_value=precios_historicos),
            obtener_fx_batch=AsyncMock(return_value=fx_rates),
            obtener_fx_by_date=AsyncMock(return_value=1.085),
        )
    )
    return stack


class TestMovimientos:
    def _cartera_id(self, client):
        return client.post(f"{BASE}/carteras", json={"nombre": "Test"}).json()["id"]

    def test_crear_movimiento_compra(self, auth_client):
        cartera_id = self._cartera_id(auth_client)
        with mock_precios():
            r = auth_client.post(
                f"{BASE}/movimientos",
                json={
                    "cartera_id": cartera_id,
                    "isin": "US0378331005",
                    "tipo": "compra",
                    "fecha": "2024-01-15",
                    "cantidad": 10,
                    "precio": 180.0,
                    "comision": 5.0,
                },
            )
        assert r.status_code == 200
        data = r.json()
        assert data["tipo"] == "compra"
        assert data["instrumento"]["isin"] == "US0378331005"

    def test_isin_se_normaliza_a_mayusculas(self, auth_client):
        cartera_id = self._cartera_id(auth_client)
        with mock_precios():
            r = auth_client.post(
                f"{BASE}/movimientos",
                json={
                    "cartera_id": cartera_id,
                    "isin": "us0378331005",  # en minúsculas
                    "tipo": "compra",
                    "fecha": "2024-01-15",
                    "cantidad": 5,
                    "precio": 180.0,
                },
            )
        assert r.status_code == 200
        assert r.json()["instrumento"]["isin"] == "US0378331005"

    def test_crear_movimiento_cartera_inexistente(self, auth_client):
        with mock_precios():
            r = auth_client.post(
                f"{BASE}/movimientos",
                json={
                    "cartera_id": 9999,
                    "isin": "US0378331005",
                    "tipo": "compra",
                    "fecha": "2024-01-15",
                    "cantidad": 10,
                    "precio": 180.0,
                },
            )
        assert r.status_code == 404

    def test_venta_valida(self, auth_client):
        cartera_id = self._cartera_id(auth_client)
        with mock_precios():
            # Compra primero
            auth_client.post(
                f"{BASE}/movimientos",
                json={
                    "cartera_id": cartera_id,
                    "isin": "US0378331005",
                    "tipo": "compra",
                    "fecha": "2024-01-15",
                    "cantidad": 10,
                    "precio": 180.0,
                },
            )
            # Luego vende parte
            r = auth_client.post(
                f"{BASE}/movimientos",
                json={
                    "cartera_id": cartera_id,
                    "isin": "US0378331005",
                    "tipo": "venta",
                    "fecha": "2024-06-01",
                    "cantidad": 5,
                    "precio": 200.0,
                },
            )
        assert r.status_code == 200

    def test_venta_supera_stock_retorna_400(self, auth_client):
        cartera_id = self._cartera_id(auth_client)
        with mock_precios():
            auth_client.post(
                f"{BASE}/movimientos",
                json={
                    "cartera_id": cartera_id,
                    "isin": "US0378331005",
                    "tipo": "compra",
                    "fecha": "2024-01-15",
                    "cantidad": 5,
                    "precio": 180.0,
                },
            )
            r = auth_client.post(
                f"{BASE}/movimientos",
                json={
                    "cartera_id": cartera_id,
                    "isin": "US0378331005",
                    "tipo": "venta",
                    "fecha": "2024-06-01",
                    "cantidad": 10,  # más de lo comprado
                    "precio": 200.0,
                },
            )
        assert r.status_code == 400

    def test_listar_movimientos(self, auth_client):
        cartera_id = self._cartera_id(auth_client)
        with mock_precios():
            auth_client.post(
                f"{BASE}/movimientos",
                json={
                    "cartera_id": cartera_id,
                    "isin": "US0378331005",
                    "tipo": "compra",
                    "fecha": "2024-01-15",
                    "cantidad": 10,
                    "precio": 180.0,
                },
            )
        r = auth_client.get(f"{BASE}/carteras/{cartera_id}/movimientos")
        assert r.status_code == 200
        assert len(r.json()) == 1

    def test_listar_movimientos_cartera_inexistente(self, auth_client):
        r = auth_client.get(f"{BASE}/carteras/9999/movimientos")
        assert r.status_code == 404

    def test_eliminar_movimiento(self, auth_client):
        cartera_id = self._cartera_id(auth_client)
        with mock_precios():
            mov_id = auth_client.post(
                f"{BASE}/movimientos",
                json={
                    "cartera_id": cartera_id,
                    "isin": "US0378331005",
                    "tipo": "compra",
                    "fecha": "2024-01-15",
                    "cantidad": 10,
                    "precio": 180.0,
                },
            ).json()["id"]
        r = auth_client.delete(f"{BASE}/movimientos/{mov_id}")
        assert r.status_code == 200

    def test_eliminar_movimiento_inexistente(self, auth_client):
        r = auth_client.delete(f"{BASE}/movimientos/9999")
        assert r.status_code == 404

    def test_tipo_movimiento_invalido_retorna_422(self, auth_client):
        cartera_id = self._cartera_id(auth_client)
        r = auth_client.post(
            f"{BASE}/movimientos",
            json={
                "cartera_id": cartera_id,
                "isin": "US0378331005",
                "tipo": "transferencia",  # inválido
                "fecha": "2024-01-15",
                "cantidad": 10,
                "precio": 180.0,
            },
        )
        assert r.status_code == 422

    def test_comision_negativa_retorna_422(self, auth_client):
        cartera_id = self._cartera_id(auth_client)
        r = auth_client.post(
            f"{BASE}/movimientos",
            json={
                "cartera_id": cartera_id,
                "isin": "US0378331005",
                "tipo": "compra",
                "fecha": "2024-01-15",
                "cantidad": 10,
                "precio": 180.0,
                "comision": -5.0,  # inválida
            },
        )
        assert r.status_code == 422


# ── Resumen de cartera ────────────────────────────────────────────────────────


class TestResumenCartera:
    def _setup_cartera_con_compra(self, client):
        cartera_id = client.post(f"{BASE}/carteras", json={"nombre": "Test"}).json()[
            "id"
        ]
        with mock_precios():
            client.post(
                f"{BASE}/movimientos",
                json={
                    "cartera_id": cartera_id,
                    "isin": "US0378331005",
                    "tipo": "compra",
                    "fecha": "2024-01-15",
                    "cantidad": 10,
                    "precio": 100.0,
                },
            )
        return cartera_id

    def test_resumen_con_precio(self, auth_client):
        cartera_id = self._setup_cartera_con_compra(auth_client)
        with mock_precios():
            r = auth_client.get(f"{BASE}/carteras/{cartera_id}/resumen")
        assert r.status_code == 200
        data = r.json()
        assert data["num_posiciones"] == 1
        assert data["valor_total"] == pytest.approx(1500.0)  # 10 * 150 (precio mock)
        assert data["coste_total"] == pytest.approx(1000.0)

    def test_resumen_cartera_inexistente(self, auth_client):
        with mock_precios():
            r = auth_client.get(f"{BASE}/carteras/9999/resumen")
        assert r.status_code == 404

    def test_resumen_posicion_cerrada_no_pierde_plusvalia(self, auth_client):
        """P/L realizada de posiciones cerradas debe aparecer en el resumen."""
        cartera_id = auth_client.post(
            f"{BASE}/carteras", json={"nombre": "Test"}
        ).json()["id"]
        with mock_precios():
            # Compra y venta total → posición cerrada
            auth_client.post(
                f"{BASE}/movimientos",
                json={
                    "cartera_id": cartera_id,
                    "isin": "US0378331005",
                    "tipo": "compra",
                    "fecha": "2024-01-01",
                    "cantidad": 10,
                    "precio": 100.0,
                },
            )
            auth_client.post(
                f"{BASE}/movimientos",
                json={
                    "cartera_id": cartera_id,
                    "isin": "US0378331005",
                    "tipo": "venta",
                    "fecha": "2024-06-01",
                    "cantidad": 10,
                    "precio": 120.0,
                },
            )
            r = auth_client.get(f"{BASE}/carteras/{cartera_id}/resumen")
        assert r.status_code == 200
        data = r.json()
        # La posición está cerrada → no aparece en posiciones
        assert data["num_posiciones"] == 0
        # Pero la plusvalía realizada sí debe estar: 10*(120-100) = 200
        assert data["plusvalia_realizada"] == pytest.approx(200.0, abs=0.01)


# ── Rentabilidad por periodo ──────────────────────────────────────────────────


class TestRentabilidadPeriodo:
    def _setup_cartera_con_compra(self, client, fecha="2024-01-15", cantidad=10):
        cartera_id = client.post(f"{BASE}/carteras", json={"nombre": "Test"}).json()[
            "id"
        ]
        with mock_precios():
            client.post(
                f"{BASE}/movimientos",
                json={
                    "cartera_id": cartera_id,
                    "isin": "US0378331005",
                    "tipo": "compra",
                    "fecha": fecha,
                    "cantidad": cantidad,
                    "precio": 100.0,
                },
            )
        return cartera_id

    def test_rentabilidad_posicion_previa_al_periodo(self, auth_client):
        cartera_id = self._setup_cartera_con_compra(auth_client)
        with mock_precios(precios_historicos={"AAPL": 100.0}):
            r = auth_client.get(
                f"{BASE}/carteras/{cartera_id}/rentabilidad", params={"periodo": "1y"}
            )
        assert r.status_code == 200
        data = r.json()
        assert data["periodo"] == "1y"
        assert len(data["posiciones"]) == 1
        # precio histórico 100 USD / fx histórico mock 1.085 = 92.1659 EUR/acción
        assert data["coste_total"] == pytest.approx(921.66, abs=0.01)
        assert data["valor_total"] == pytest.approx(1500.0)  # 10 * precio mock actual
        assert data["plusvalia_latente"] == pytest.approx(578.34, abs=0.01)
        assert data["rentabilidad_pct"] == pytest.approx(62.75, abs=0.01)
        assert data["tickers_sin_dato"] == []

    def test_rentabilidad_periodo_invalido(self, auth_client):
        cartera_id = auth_client.post(
            f"{BASE}/carteras", json={"nombre": "Test"}
        ).json()["id"]
        r = auth_client.get(
            f"{BASE}/carteras/{cartera_id}/rentabilidad", params={"periodo": "5m"}
        )
        assert r.status_code == 400

    def test_rentabilidad_sin_precio_historico_va_a_tickers_sin_dato(self, auth_client):
        cartera_id = self._setup_cartera_con_compra(auth_client)
        with mock_precios(precios_historicos={}):
            r = auth_client.get(
                f"{BASE}/carteras/{cartera_id}/rentabilidad", params={"periodo": "1y"}
            )
        assert r.status_code == 200
        data = r.json()
        assert data["posiciones"] == []
        assert data["tickers_sin_dato"] == ["AAPL"]

    def test_rentabilidad_cartera_inexistente(self, auth_client):
        with mock_precios():
            r = auth_client.get(
                f"{BASE}/carteras/9999/rentabilidad", params={"periodo": "1y"}
            )
        assert r.status_code == 404


# ── Frontera eficiente (Markowitz) ────────────────────────────────────────────

# "Hoy" fijo: la ventana debe acabar en el último mes cerrado (septiembre)
HOY_FRONTERA = date(2026, 10, 4)
ULTIMO_MES = date(2026, 9, 1)


def _meses_hasta(ultimo: date, n: int) -> list[date]:
    """Los `n` primeros de mes consecutivos que terminan en `ultimo`."""
    indice = ultimo.year * 12 + ultimo.month - 1
    return [
        date((indice - k) // 12, (indice - k) % 12 + 1, 1) for k in range(n - 1, -1, -1)
    ]


def _serie_mensual(
    n: int, semilla: int, ultimo: date = ULTIMO_MES
) -> list[tuple[date, float]]:
    """Serie de `n` cierres mensuales deterministas (paseo aleatorio con semilla)."""
    rng = np.random.default_rng(semilla)
    rentabilidades = rng.normal(0.01, 0.05, n - 1)
    precios = 100.0 * np.cumprod(np.concatenate([[1.0], 1.0 + rentabilidades]))
    return list(zip(_meses_hasta(ultimo, n), map(float, precios), strict=True))


class TestFronteraEficiente:
    # ticker -> (cantidad, precio actual); valor EUR: 1000, 1000, 2000
    POSICIONES = {"AAA": (10, 100.0), "BBB": (20, 50.0), "CCC": (100, 20.0)}

    def _crear_cartera(self, client, posiciones: dict[str, tuple[int, float]]):
        """Cartera con una compra en EUR por ticker (ISIN ficticio por ticker)."""
        cartera_id = client.post(f"{BASE}/carteras", json={"nombre": "Test"}).json()[
            "id"
        ]
        enriquecer = AsyncMock(
            side_effect=lambda isin: {
                **MOCK_IA,
                "moneda": "EUR",
                "ticker": isin.removeprefix("XX"),
            }
        )
        with patch("app.routers.movimientos.precios.enriquecer_por_isin", enriquecer):
            for ticker, (cantidad, _) in posiciones.items():
                r = client.post(
                    f"{BASE}/movimientos",
                    json={
                        "cartera_id": cartera_id,
                        "isin": f"XX{ticker}",
                        "tipo": "compra",
                        "fecha": "2024-01-15",
                        "cantidad": cantidad,
                        "precio": 10.0,
                    },
                )
                assert r.status_code == 200
        return cartera_id

    def _mock_precios(self, posiciones, series=None, error=None):
        """Parchea precios actuales, FX, series mensuales y la fecha de hoy.

        Devuelve (contexto, mock de obtener_precios_mensuales_batch).
        """
        mensuales = (
            AsyncMock(side_effect=error)
            if error
            else AsyncMock(return_value=series if series is not None else {})
        )
        contexto = patch.multiple(
            "app.routers.posiciones.precios",
            obtener_precios_batch=AsyncMock(
                return_value={t: precio for t, (_, precio) in posiciones.items()}
            ),
            obtener_fx_batch=AsyncMock(return_value={}),
            obtener_precios_mensuales_batch=mensuales,
            _hoy=lambda: HOY_FRONTERA,
        )
        return contexto, mensuales

    def _series_completas(self, tickers):
        return {t: _serie_mensual(61, semilla) for semilla, t in enumerate(tickers)}

    def test_frontera_de_la_cartera(self, auth_client):
        cartera_id = self._crear_cartera(auth_client, self.POSICIONES)
        series = self._series_completas(self.POSICIONES)
        contexto, mensuales = self._mock_precios(self.POSICIONES, series)
        with contexto:
            r = auth_client.get(
                f"{BASE}/carteras/{cartera_id}/frontera-eficiente",
                params={"anios": 5, "puntos": 10},
            )
        assert r.status_code == 200, r.text
        data = r.json()

        # Ventana: 5 años que acaban en el último mes cerrado (nunca el actual)
        assert data["fecha_inicio"] == "2021-09-01"
        assert data["fecha_fin"] == "2026-09-30"
        mensuales.assert_called_once()
        args = mensuales.call_args.args
        assert sorted(args[0]) == ["AAA", "BBB", "CCC"]
        assert args[1:] == (date(2021, 9, 1), date(2026, 9, 30))

        assert data["frecuencia"] == "mensual"
        assert data["n_observaciones"] == 60
        assert sorted(data["tickers"]) == ["AAA", "BBB", "CCC"]
        assert len(data["rentabilidades_esperadas"]) == 3
        cov = np.array(data["matriz_covarianzas"])
        assert cov.shape == (3, 3)
        np.testing.assert_allclose(cov, cov.T)
        assert data["excluidos"] == []
        assert data["peso_excluido"] == pytest.approx(0.0)

        minima = data["cartera_minima_varianza"]
        actual = data["cartera_actual"]
        assert 1 <= len(data["frontera"]) <= 10
        for punto in [*data["frontera"], minima, actual]:
            pesos = punto["pesos"]
            assert set(pesos) == {"AAA", "BBB", "CCC"}
            assert all(0.0 <= w <= 1.0 for w in pesos.values())
            assert sum(pesos.values()) == pytest.approx(1.0, abs=1e-6)
            assert minima["volatilidad"] <= punto["volatilidad"] + 1e-12

        # Pesos actuales = valor de mercado EUR de cada posición / total
        assert actual["pesos"] == {
            "AAA": pytest.approx(0.25),
            "BBB": pytest.approx(0.25),
            "CCC": pytest.approx(0.5),
        }

    def test_detalle_del_calculo(self, auth_client):
        cartera_id = self._crear_cartera(auth_client, self.POSICIONES)
        series = self._series_completas(self.POSICIONES)
        with self._mock_precios(self.POSICIONES, series)[0]:
            r = auth_client.get(
                f"{BASE}/carteras/{cartera_id}/frontera-eficiente",
                params={"anios": 5, "puntos": 10},
            )
        assert r.status_code == 200, r.text
        data = r.json()
        tickers = data["tickers"]
        n_activos, n_meses = len(tickers), data["n_observaciones"]
        detalle = data["detalle"]

        # Datos de entrada: un cierre más que rentabilidades (meses consecutivos)
        datos = detalle["datos"]
        assert len(datos["fechas"]) == n_meses == 60
        assert datos["fechas"][0] == "2021-10-01"
        assert datos["fechas"][-1] == "2026-09-01"
        assert len(datos["fechas_precios"]) == n_meses + 1
        assert datos["fechas_precios"][0] == data["fecha_inicio"]
        assert datos["fechas_precios"][1:] == datos["fechas"]
        precios_matriz = np.array(datos["precios"])
        rentabilidades = np.array(datos["rentabilidades"])
        assert precios_matriz.shape == (n_meses + 1, n_activos)
        assert rentabilidades.shape == (n_meses, n_activos)
        for j, ticker in enumerate(tickers):
            np.testing.assert_allclose(
                precios_matriz[:, j], [p for _, p in series[ticker]]
            )
        np.testing.assert_allclose(
            rentabilidades, precios_matriz[1:] / precios_matriz[:-1] - 1
        )
        np.testing.assert_allclose(
            data["rentabilidades_esperadas"], rentabilidades.mean(axis=0)
        )

        # Estadísticas por activo
        estadisticas = detalle["estadisticas"]
        np.testing.assert_allclose(
            estadisticas["varianzas"], rentabilidades.var(axis=0, ddof=1)
        )
        np.testing.assert_allclose(
            estadisticas["volatilidades"], rentabilidades.std(axis=0, ddof=1)
        )
        correlaciones = np.array(estadisticas["matriz_correlaciones"])
        assert correlaciones.shape == (n_activos, n_activos)
        np.testing.assert_allclose(
            correlaciones, np.corrcoef(rentabilidades, rowvar=False)
        )

        # Cartera actual: valores EUR y serie mensual = pesos · rentabilidades
        actual = detalle["cartera_actual"]
        assert actual["valores_eur"] == {
            "AAA": pytest.approx(1000.0),
            "BBB": pytest.approx(1000.0),
            "CCC": pytest.approx(2000.0),
        }
        pesos = np.array([data["cartera_actual"]["pesos"][t] for t in tickers])
        assert len(actual["rentabilidades"]) == n_meses
        np.testing.assert_allclose(actual["rentabilidades"], rentabilidades @ pesos)
        assert np.mean(actual["rentabilidades"]) == pytest.approx(
            data["cartera_actual"]["rentabilidad"]
        )

        # Cada punto expone su varianza (= volatilidad²)
        for punto in [
            *data["frontera"],
            data["cartera_minima_varianza"],
            data["cartera_actual"],
        ]:
            assert punto["varianza"] == pytest.approx(punto["volatilidad"] ** 2)

    def test_historico_corto_o_inexistente_se_excluye_y_se_informa(self, auth_client):
        posiciones = {
            **self.POSICIONES,
            "NEW": (10, 100.0),  # recién listado: 6 meses
            "SIN": (10, 100.0),  # sin ningún dato histórico
        }
        cartera_id = self._crear_cartera(auth_client, posiciones)
        series = {
            **self._series_completas(self.POSICIONES),
            "NEW": _serie_mensual(6, semilla=99),
        }
        with self._mock_precios(posiciones, series)[0]:
            r = auth_client.get(f"{BASE}/carteras/{cartera_id}/frontera-eficiente")
        assert r.status_code == 200, r.text
        data = r.json()
        assert sorted(data["tickers"]) == ["AAA", "BBB", "CCC"]
        assert sorted(data["excluidos"], key=lambda e: e["ticker"]) == [
            {"ticker": "NEW", "motivo": "historico_insuficiente"},
            {"ticker": "SIN", "motivo": "sin_historico"},
        ]
        # 2000 EUR excluidos de 6000 EUR
        assert data["peso_excluido"] == pytest.approx(1 / 3)
        # Pesos actuales renormalizados sobre los activos incluidos
        assert data["cartera_actual"]["pesos"] == {
            "AAA": pytest.approx(0.25),
            "BBB": pytest.approx(0.25),
            "CCC": pytest.approx(0.5),
        }

    def test_menos_de_dos_activos_retorna_400(self, auth_client):
        posiciones = {"AAA": (10, 100.0)}
        cartera_id = self._crear_cartera(auth_client, posiciones)
        contexto, mensuales = self._mock_precios(posiciones)
        with contexto:
            r = auth_client.get(f"{BASE}/carteras/{cartera_id}/frontera-eficiente")
        assert r.status_code == 400
        assert "al menos 2" in r.json()["detail"]
        # Sin activos suficientes no se llega a consultar el histórico
        mensuales.assert_not_called()

    def test_un_solo_activo_con_historico_suficiente_retorna_400(self, auth_client):
        posiciones = {"AAA": (10, 100.0), "NEW": (10, 100.0)}
        cartera_id = self._crear_cartera(auth_client, posiciones)
        series = {"AAA": _serie_mensual(61, 0), "NEW": _serie_mensual(6, 1)}
        with self._mock_precios(posiciones, series)[0]:
            r = auth_client.get(f"{BASE}/carteras/{cartera_id}/frontera-eficiente")
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert "al menos 2" in detail
        assert "NEW" in detail

    def test_pocas_observaciones_comunes_retorna_400(self, auth_client):
        cartera_id = self._crear_cartera(auth_client, self.POSICIONES)
        # Cada activo tiene 12 rentabilidades propias, pero no coinciden en el tiempo
        series = {
            "AAA": _serie_mensual(61, 0),
            "BBB": _serie_mensual(13, 1, ultimo=date(2022, 9, 1)),
            "CCC": _serie_mensual(13, 2),
        }
        with self._mock_precios(self.POSICIONES, series)[0]:
            r = auth_client.get(f"{BASE}/carteras/{cartera_id}/frontera-eficiente")
        assert r.status_code == 400
        assert "observaciones" in r.json()["detail"]

    def test_fallo_del_proveedor_retorna_503(self, auth_client):
        cartera_id = self._crear_cartera(auth_client, self.POSICIONES)
        error = ProveedorPreciosError("yfinance caído")
        with self._mock_precios(self.POSICIONES, error=error)[0]:
            r = auth_client.get(f"{BASE}/carteras/{cartera_id}/frontera-eficiente")
        assert r.status_code == 503
        assert "precios" in r.json()["detail"]

    def test_fallo_del_proveedor_se_registra_en_el_log(self, auth_client, caplog):
        cartera_id = self._crear_cartera(auth_client, self.POSICIONES)
        error = ProveedorPreciosError("yfinance caído")
        caplog.set_level(logging.WARNING, logger="app.routers.posiciones")
        with self._mock_precios(self.POSICIONES, error=error)[0]:
            r = auth_client.get(f"{BASE}/carteras/{cartera_id}/frontera-eficiente")
        assert r.status_code == 503
        # El detalle HTTP no filtra el error interno; el log sí lo conserva
        assert "yfinance caído" not in r.json()["detail"]
        registros = [
            rec for rec in caplog.records if rec.name == "app.routers.posiciones"
        ]
        assert len(registros) == 1
        assert registros[0].levelno >= logging.WARNING
        mensaje = registros[0].getMessage()
        assert str(cartera_id) in mensaje
        assert "yfinance caído" in mensaje

    @pytest.mark.parametrize("precio", [float("nan"), float("inf")])
    def test_valor_actual_no_finito_se_excluye_sin_precio_actual(
        self, auth_client, precio
    ):
        posiciones = {**self.POSICIONES, "NAN": (10, precio)}
        cartera_id = self._crear_cartera(auth_client, posiciones)
        series = self._series_completas(self.POSICIONES)
        with self._mock_precios(posiciones, series)[0]:
            r = auth_client.get(f"{BASE}/carteras/{cartera_id}/frontera-eficiente")
        assert r.status_code == 200, r.text
        data = r.json()
        assert sorted(data["tickers"]) == ["AAA", "BBB", "CCC"]
        assert data["excluidos"] == [{"ticker": "NAN", "motivo": "sin_precio_actual"}]
        # Sin valor conocido no cuenta en el peso excluido
        assert data["peso_excluido"] == pytest.approx(0.0)
        assert data["cartera_actual"]["pesos"] == {
            "AAA": pytest.approx(0.25),
            "BBB": pytest.approx(0.25),
            "CCC": pytest.approx(0.5),
        }

    def test_optimizacion_se_ejecuta_fuera_del_event_loop(self, auth_client):
        from fastapi.concurrency import run_in_threadpool

        cartera_id = self._crear_cartera(auth_client, self.POSICIONES)
        series = self._series_completas(self.POSICIONES)
        espia = AsyncMock(side_effect=run_in_threadpool)
        with (
            self._mock_precios(self.POSICIONES, series)[0],
            patch("app.routers.posiciones.run_in_threadpool", espia),
        ):
            r = auth_client.get(f"{BASE}/carteras/{cartera_id}/frontera-eficiente")
        assert r.status_code == 200, r.text
        espia.assert_awaited_once()

    @pytest.mark.parametrize(
        "params", [{"anios": 0}, {"anios": 21}, {"puntos": 1}, {"puntos": 101}]
    )
    def test_parametros_invalidos_retornan_422(self, auth_client, params):
        cartera_id = auth_client.post(
            f"{BASE}/carteras", json={"nombre": "Test"}
        ).json()["id"]
        r = auth_client.get(
            f"{BASE}/carteras/{cartera_id}/frontera-eficiente", params=params
        )
        assert r.status_code == 422

    def test_cartera_inexistente_retorna_404(self, auth_client):
        r = auth_client.get(f"{BASE}/carteras/9999/frontera-eficiente")
        assert r.status_code == 404

    def test_cartera_ajena_retorna_404(self, auth_client, second_auth_client):
        foreign_id = self._crear_cartera(second_auth_client, self.POSICIONES)
        contexto, mensuales = self._mock_precios(self.POSICIONES)
        with contexto:
            r = auth_client.get(f"{BASE}/carteras/{foreign_id}/frontera-eficiente")
        assert r.status_code == 404
        mensuales.assert_not_called()


# ── Instrumentos ──────────────────────────────────────────────────────────────


class TestInstrumentos:
    def test_patch_instrumento(self, auth_client):
        cartera_id = auth_client.post(
            f"{BASE}/carteras", json={"nombre": "Test"}
        ).json()["id"]
        with mock_precios():
            mov = auth_client.post(
                f"{BASE}/movimientos",
                json={
                    "cartera_id": cartera_id,
                    "isin": "US0378331005",
                    "tipo": "compra",
                    "fecha": "2024-01-15",
                    "cantidad": 5,
                    "precio": 180.0,
                },
            ).json()
        instrumento_id = mov["instrumento"]["id"]
        # /instrumentos is not cartera-scoped and stays unauthenticated per
        # design/tasks scope — Phase 3 only covers carteras/movimientos/posiciones.
        r = auth_client.patch(
            f"{BASE}/instrumentos/{instrumento_id}",
            json={
                "sector": "Consumo Básico",
                "pais": "Canadá",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["sector"] == "Consumo Básico"
        assert data["pais"] == "Canadá"

    def test_patch_instrumento_inexistente(self, client):
        r = client.patch(f"{BASE}/instrumentos/9999", json={"sector": "X"})
        assert r.status_code == 404

    def test_listar_instrumentos(self, auth_client):
        cartera_id = auth_client.post(
            f"{BASE}/carteras", json={"nombre": "Test"}
        ).json()["id"]
        with mock_precios():
            auth_client.post(
                f"{BASE}/movimientos",
                json={
                    "cartera_id": cartera_id,
                    "isin": "US0378331005",
                    "tipo": "compra",
                    "fecha": "2024-01-15",
                    "cantidad": 5,
                    "precio": 180.0,
                },
            )
        r = auth_client.get(f"{BASE}/instrumentos")
        assert r.status_code == 200
        assert len(r.json()) == 1


# ── Backfill FX ───────────────────────────────────────────────────────────────


class TestBackfillFx:
    def _setup(self, client):
        """Crea cartera con un movimiento USD sin tipo_cambio."""
        cartera_id = client.post(f"{BASE}/carteras", json={"nombre": "FX Test"}).json()[
            "id"
        ]
        with mock_precios():
            client.post(
                f"{BASE}/movimientos",
                json={
                    "cartera_id": cartera_id,
                    "isin": "US0378331005",
                    "tipo": "compra",
                    "fecha": "2024-01-15",
                    "cantidad": 10,
                    "precio": 150.0,
                    # sin tipo_cambio → queda en NULL
                },
            )
        return cartera_id

    def test_backfill_actualiza_movimientos_usd(self, auth_client):
        cartera_id = self._setup(auth_client)
        with mock_precios():
            r = auth_client.post(f"{BASE}/carteras/{cartera_id}/backfill-fx")
        assert r.status_code == 200
        data = r.json()
        assert data["actualizados"] == 1
        assert data["omitidos"] == 0

    def test_backfill_cartera_inexistente_retorna_404(self, auth_client):
        r = auth_client.post(f"{BASE}/carteras/9999/backfill-fx")
        assert r.status_code == 404

    def test_backfill_no_toca_movimientos_eur(self, auth_client):
        """Movimientos de instrumentos EUR no deben modificarse."""
        cartera_id = auth_client.post(
            f"{BASE}/carteras", json={"nombre": "EUR Test"}
        ).json()["id"]
        mock_eur = {**MOCK_IA, "moneda": "EUR", "ticker": "SAN"}
        with ExitStack() as stack:
            stack.enter_context(
                patch.multiple(
                    "app.routers.movimientos.precios",
                    enriquecer_por_isin=AsyncMock(return_value=mock_eur),
                )
            )
            stack.enter_context(
                patch.multiple(
                    "app.routers.posiciones.precios",
                    obtener_precios_batch=AsyncMock(return_value={"SAN": 4.0}),
                    obtener_fx_batch=AsyncMock(return_value={}),
                    obtener_fx_by_date=AsyncMock(return_value=1.085),
                )
            )
            auth_client.post(
                f"{BASE}/movimientos",
                json={
                    "cartera_id": cartera_id,
                    "isin": "ES0113900J37",
                    "tipo": "compra",
                    "fecha": "2024-01-15",
                    "cantidad": 100,
                    "precio": 3.5,
                },
            )
            r = auth_client.post(f"{BASE}/carteras/{cartera_id}/backfill-fx")
        assert r.status_code == 200
        data = r.json()
        # No hay movimientos USD/no-EUR → nada que actualizar
        assert data["actualizados"] == 0
        assert data["omitidos"] == 0

    def test_backfill_omite_cuando_yfinance_sin_datos(self, auth_client):
        cartera_id = self._setup(auth_client)
        with ExitStack() as stack:
            stack.enter_context(
                patch.multiple(
                    "app.routers.posiciones.precios",
                    obtener_precios_batch=AsyncMock(return_value={"AAPL": 150.0}),
                    obtener_fx_batch=AsyncMock(return_value={}),
                    obtener_fx_by_date=AsyncMock(return_value=None),  # sin datos
                )
            )
            r = auth_client.post(f"{BASE}/carteras/{cartera_id}/backfill-fx")
        assert r.status_code == 200
        data = r.json()
        assert data["actualizados"] == 0
        assert data["omitidos"] == 1


# ── Dual-currency en resumen ──────────────────────────────────────────────────


class TestResumenDualCurrency:
    def _setup_usd(self, client, fx_rates=None):
        cartera_id = client.post(
            f"{BASE}/carteras", json={"nombre": "USD Test"}
        ).json()["id"]
        with mock_precios(fx_rates=fx_rates or {}):
            client.post(
                f"{BASE}/movimientos",
                json={
                    "cartera_id": cartera_id,
                    "isin": "US0378331005",
                    "tipo": "compra",
                    "fecha": "2024-01-15",
                    "cantidad": 10,
                    "precio": 150.0,
                    "tipo_cambio": 1.10,
                },
            )
        return cartera_id

    def test_resumen_incluye_campos_dual_currency(self, auth_client):
        cartera_id = self._setup_usd(auth_client, fx_rates={"USD": 1.05})
        with mock_precios(fx_rates={"USD": 1.05}):
            r = auth_client.get(f"{BASE}/carteras/{cartera_id}/resumen")
        assert r.status_code == 200
        pos = r.json()["posiciones"][0]
        assert "valor_actual_eur" in pos
        assert "valor_actual_nativo" in pos
        assert "moneda_nativa" in pos
        assert "fx_actual" in pos

    def test_moneda_nativa_correcta(self, auth_client):
        cartera_id = self._setup_usd(auth_client, fx_rates={"USD": 1.05})
        with mock_precios(fx_rates={"USD": 1.05}):
            r = auth_client.get(f"{BASE}/carteras/{cartera_id}/resumen")
        pos = r.json()["posiciones"][0]
        assert pos["moneda_nativa"] == "USD"

    def test_valor_actual_es_alias_de_valor_actual_eur(self, auth_client):
        """valor_actual debe ser igual a valor_actual_eur (backwards-compat)."""
        cartera_id = self._setup_usd(auth_client, fx_rates={"USD": 1.05})
        with mock_precios(fx_rates={"USD": 1.05}):
            r = auth_client.get(f"{BASE}/carteras/{cartera_id}/resumen")
        pos = r.json()["posiciones"][0]
        assert pos["valor_actual"] == pos["valor_actual_eur"]

    def test_valor_actual_nativo_en_moneda_nativa(self, auth_client):
        """valor_actual_nativo = precio_actual * cantidad (sin conversión FX)."""
        cartera_id = self._setup_usd(auth_client, fx_rates={"USD": 1.05})
        with mock_precios(fx_rates={"USD": 1.05}):
            r = auth_client.get(f"{BASE}/carteras/{cartera_id}/resumen")
        pos = r.json()["posiciones"][0]
        # precio mock = 150, cantidad = 10 → 1500 USD
        assert pos["valor_actual_nativo"] == pytest.approx(1500.0)

    def test_valor_actual_eur_aplica_fx(self, auth_client):
        """valor_actual_eur = precio_nativo / fx * cantidad."""
        cartera_id = self._setup_usd(auth_client, fx_rates={"USD": 1.05})
        with mock_precios(fx_rates={"USD": 1.05}):
            r = auth_client.get(f"{BASE}/carteras/{cartera_id}/resumen")
        pos = r.json()["posiciones"][0]
        # precio mock=150, fx=1.05, cantidad=10 → 150/1.05*10 ≈ 1428.57
        assert pos["valor_actual_eur"] == pytest.approx(150.0 / 1.05 * 10, abs=0.01)


# ── Ownership authorization (Phase 3/4, PR3) ───────────────────────────────────


class TestOwnershipAuthorization:
    """Unauthenticated-rejected and owner-vs-foreign scenarios across
    carteras, movimientos, and posiciones — see specs/cartera-ownership,
    specs/carteras, specs/movimientos, specs/posiciones."""

    # -- Unauthenticated access is rejected ----------------------------------

    def test_carteras_require_authentication(self, client):
        assert client.get(f"{BASE}/carteras").status_code == 401
        assert client.post(f"{BASE}/carteras", json={"nombre": "X"}).status_code == 401

    def test_movimientos_require_authentication(self, client):
        assert client.get(f"{BASE}/carteras/1/movimientos").status_code == 401
        with mock_precios():
            r = client.post(
                f"{BASE}/movimientos",
                json={
                    "cartera_id": 1,
                    "isin": "US0378331005",
                    "tipo": "compra",
                    "fecha": "2024-01-15",
                    "cantidad": 10,
                    "precio": 180.0,
                },
            )
        assert r.status_code == 401

    def test_posiciones_require_authentication(self, client):
        assert client.get(f"{BASE}/carteras/1/resumen").status_code == 401
        assert client.get(f"{BASE}/carteras/1/analisis").status_code == 401
        assert client.post(f"{BASE}/carteras/1/backfill-fx").status_code == 401
        assert client.get(f"{BASE}/carteras/1/frontera-eficiente").status_code == 401

    # -- Carteras are scoped to the owner -------------------------------------

    def test_list_carteras_scoped_to_owner(self, auth_client, second_auth_client):
        auth_client.post(f"{BASE}/carteras", json={"nombre": "Mine"})
        second_auth_client.post(f"{BASE}/carteras", json={"nombre": "Theirs"})
        r = auth_client.get(f"{BASE}/carteras")
        assert r.status_code == 200
        # Each user's own default "Mi Cartera Principal" (from registration)
        # plus what they created — never the other user's carteras.
        assert [c["nombre"] for c in r.json()] == ["Mi Cartera Principal", "Mine"]

    def test_delete_foreign_cartera_returns_404(self, auth_client, second_auth_client):
        foreign_id = second_auth_client.post(
            f"{BASE}/carteras", json={"nombre": "Theirs"}
        ).json()["id"]
        r = auth_client.delete(f"{BASE}/carteras/{foreign_id}")
        assert r.status_code == 404

    # -- Movimientos are authorized through the parent cartera's owner -------

    def test_create_movimiento_in_foreign_cartera_returns_404(
        self, auth_client, second_auth_client
    ):
        foreign_id = second_auth_client.post(
            f"{BASE}/carteras", json={"nombre": "Theirs"}
        ).json()["id"]
        with mock_precios():
            r = auth_client.post(
                f"{BASE}/movimientos",
                json={
                    "cartera_id": foreign_id,
                    "isin": "US0378331005",
                    "tipo": "compra",
                    "fecha": "2024-01-15",
                    "cantidad": 10,
                    "precio": 180.0,
                },
            )
        assert r.status_code == 404

    def test_list_movimientos_in_foreign_cartera_returns_404(
        self, auth_client, second_auth_client
    ):
        foreign_id = second_auth_client.post(
            f"{BASE}/carteras", json={"nombre": "Theirs"}
        ).json()["id"]
        r = auth_client.get(f"{BASE}/carteras/{foreign_id}/movimientos")
        assert r.status_code == 404

    def test_delete_movimiento_in_foreign_cartera_returns_404(
        self, auth_client, second_auth_client
    ):
        foreign_id = second_auth_client.post(
            f"{BASE}/carteras", json={"nombre": "Theirs"}
        ).json()["id"]
        with mock_precios():
            mov_id = second_auth_client.post(
                f"{BASE}/movimientos",
                json={
                    "cartera_id": foreign_id,
                    "isin": "US0378331005",
                    "tipo": "compra",
                    "fecha": "2024-01-15",
                    "cantidad": 10,
                    "precio": 180.0,
                },
            ).json()["id"]
        r_foreign = auth_client.delete(f"{BASE}/movimientos/{mov_id}")
        r_missing = auth_client.delete(f"{BASE}/movimientos/999999")

        assert r_foreign.status_code == 404
        assert r_missing.status_code == 404
        assert r_foreign.json()["detail"] == r_missing.json()["detail"]
        assert r_foreign.json()["detail"] == "Movimiento no encontrado"

    # -- Posiciones endpoints are authorized through cartera ownership -------

    def test_resumen_de_cartera_foreign_returns_404(
        self, auth_client, second_auth_client
    ):
        foreign_id = second_auth_client.post(
            f"{BASE}/carteras", json={"nombre": "Theirs"}
        ).json()["id"]
        with mock_precios():
            r = auth_client.get(f"{BASE}/carteras/{foreign_id}/resumen")
        assert r.status_code == 404

    def test_analisis_de_cartera_foreign_returns_404(
        self, auth_client, second_auth_client
    ):
        foreign_id = second_auth_client.post(
            f"{BASE}/carteras", json={"nombre": "Theirs"}
        ).json()["id"]
        with mock_precios():
            r = auth_client.get(f"{BASE}/carteras/{foreign_id}/analisis")
        assert r.status_code == 404

    def test_backfill_fx_de_cartera_foreign_returns_404(
        self, auth_client, second_auth_client
    ):
        foreign_id = second_auth_client.post(
            f"{BASE}/carteras", json={"nombre": "Theirs"}
        ).json()["id"]
        r = auth_client.post(f"{BASE}/carteras/{foreign_id}/backfill-fx")
        assert r.status_code == 404


# ── Health check ──────────────────────────────────────────────────────────────


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ── Auth ──────────────────────────────────────────────────────────────────────

AUTH_BASE = f"{BASE}/auth"


class TestAuth:
    def test_register_login_me(self, client):
        r = client.post(
            f"{AUTH_BASE}/register",
            json={"email": "new-user@example.com", "password": "supersecret1"},
        )
        assert r.status_code == 201
        assert r.json()["email"] == "new-user@example.com"

        r = client.post(
            f"{AUTH_BASE}/login",
            json={"email": "new-user@example.com", "password": "supersecret1"},
        )
        assert r.status_code == 200
        assert "access_token" in r.cookies

        r = client.get(f"{AUTH_BASE}/me")
        assert r.status_code == 200
        assert r.json()["email"] == "new-user@example.com"

    def test_register_creates_default_cartera(self, client):
        r = client.post(
            f"{AUTH_BASE}/register",
            json={"email": "fresh-user@example.com", "password": "supersecret1"},
        )
        assert r.status_code == 201

        client.post(
            f"{AUTH_BASE}/login",
            json={"email": "fresh-user@example.com", "password": "supersecret1"},
        )

        r = client.get(f"{BASE}/carteras")
        assert r.status_code == 200
        carteras = r.json()
        assert len(carteras) == 1
        assert carteras[0]["nombre"] == "Mi Cartera Principal"

    def test_register_duplicate_email_rejected(self, client):
        payload = {"email": "dup@example.com", "password": "supersecret1"}
        client.post(f"{AUTH_BASE}/register", json=payload)
        r = client.post(f"{AUTH_BASE}/register", json=payload)
        assert r.status_code == 400

    def test_register_password_length_boundaries(self, client):
        r_short = client.post(
            f"{AUTH_BASE}/register",
            json={"email": "short-pass@example.com", "password": "1234567"},
        )
        r_long = client.post(
            f"{AUTH_BASE}/register",
            json={"email": "long-pass@example.com", "password": "x" * 73},
        )
        assert r_short.status_code == 422
        assert r_long.status_code == 422

    def test_register_invalid_email_format_returns_422(self, client):
        r = client.post(
            f"{AUTH_BASE}/register",
            json={"email": "not-an-email", "password": "supersecret1"},
        )
        assert r.status_code == 422

    def test_login_wrong_password_rejected(self, client):
        client.post(
            f"{AUTH_BASE}/register",
            json={"email": "wrongpass@example.com", "password": "supersecret1"},
        )
        r = client.post(
            f"{AUTH_BASE}/login",
            json={"email": "wrongpass@example.com", "password": "not-the-password"},
        )
        assert r.status_code == 401
        assert "access_token" not in r.cookies

    def test_login_unknown_email_rejected(self, client):
        r = client.post(
            f"{AUTH_BASE}/login",
            json={"email": "ghost@example.com", "password": "whatever123"},
        )
        assert r.status_code == 401

    def test_me_without_session_rejected(self, client):
        r = client.get(f"{AUTH_BASE}/me")
        assert r.status_code == 401

    def test_logout_clears_cookie(self, auth_client):
        r = auth_client.get(f"{AUTH_BASE}/me")
        assert r.status_code == 200

        r = auth_client.post(f"{AUTH_BASE}/logout")
        assert r.status_code == 200

        r = auth_client.get(f"{AUTH_BASE}/me")
        assert r.status_code == 401

    def test_logout_without_session_is_safe(self, client):
        r = client.post(f"{AUTH_BASE}/logout")
        assert r.status_code == 200
        assert r.json()["ok"] is True


class TestPasswordReset:
    EMAIL = "reset-me@example.com"
    PASSWORD = "original-password1"

    def _register(self, client):
        client.post(
            f"{AUTH_BASE}/register",
            json={"email": self.EMAIL, "password": self.PASSWORD},
        )

    def _captured_token(self, client, caplog):
        """Registers the test user and captures the dev-stub reset token from logs."""
        self._register(client)
        caplog.set_level(logging.INFO, logger="app.auth.router")
        r = client.post(f"{AUTH_BASE}/forgot-password", json={"email": self.EMAIL})
        assert r.status_code == 200
        match = re.search(r"token=(\S+)", caplog.text)
        assert match, "expected the dev stub to log a reset token"
        return match.group(1)

    def test_forgot_password_does_not_leak_existence(self, client):
        self._register(client)
        r_known = client.post(
            f"{AUTH_BASE}/forgot-password", json={"email": self.EMAIL}
        )
        r_unknown = client.post(
            f"{AUTH_BASE}/forgot-password", json={"email": "nobody@example.com"}
        )
        assert r_known.status_code == 200
        assert r_unknown.status_code == 200
        assert r_known.json() == r_unknown.json()

    def test_reset_password_success_and_single_use(self, client, caplog):
        token = self._captured_token(client, caplog)

        r = client.post(
            f"{AUTH_BASE}/reset-password",
            json={"token": token, "new_password": "brand-new-password1"},
        )
        assert r.status_code == 200

        # Old password no longer works, new one does.
        r = client.post(
            f"{AUTH_BASE}/login",
            json={"email": self.EMAIL, "password": self.PASSWORD},
        )
        assert r.status_code == 401
        r = client.post(
            f"{AUTH_BASE}/login",
            json={"email": self.EMAIL, "password": "brand-new-password1"},
        )
        assert r.status_code == 200

        # Token is single-use: reusing it fails.
        r = client.post(
            f"{AUTH_BASE}/reset-password",
            json={"token": token, "new_password": "another-password1"},
        )
        assert r.status_code == 400

    def test_reset_password_garbage_token_rejected(self, client):
        self._register(client)
        r = client.post(
            f"{AUTH_BASE}/reset-password",
            json={"token": "not-a-real-token", "new_password": "whatever-new-1"},
        )
        assert r.status_code == 400

    def test_reset_password_expired_token_rejected(self, client, db_session):
        self._register(client)
        user = (
            db_session.query(models.User)
            .filter(models.User.email == self.EMAIL)
            .first()
        )
        assert user is not None

        raw_token, token_hash = generate_reset_token()
        db_session.add(
            models.PasswordResetToken(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        )
        db_session.commit()

        r = client.post(
            f"{AUTH_BASE}/reset-password",
            json={"token": raw_token, "new_password": "new-password-123"},
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "Invalid, expired, or already-used token"
