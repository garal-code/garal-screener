"""
Tests unitarios para app/services/calculos.py

Se usan SimpleNamespace para simular objetos ORM sin necesidad de DB.
"""

from datetime import date
from types import SimpleNamespace

import pytest

from app.services.calculos import (
    VentaInvalidaError,
    agrupar_por_campo,
    calcular_plusvalia_latente,
    calcular_posicion_fifo,
    calcular_rentabilidad_periodo,
    calcular_resumen_cartera,
    fecha_inicio_periodo,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def compra(id, fecha, cantidad, precio, comision=0.0, tipo_cambio=None):
    return SimpleNamespace(
        id=id,
        tipo="compra",
        fecha=fecha,
        cantidad=cantidad,
        precio=precio,
        comision=comision,
        tipo_cambio=tipo_cambio,
    )


def venta(id, fecha, cantidad, precio, comision=0.0, tipo_cambio=None):
    return SimpleNamespace(
        id=id,
        tipo="venta",
        fecha=fecha,
        cantidad=cantidad,
        precio=precio,
        comision=comision,
        tipo_cambio=tipo_cambio,
    )


D1 = date(2024, 1, 1)
D2 = date(2024, 2, 1)
D3 = date(2024, 3, 1)

# ── calcular_posicion_fifo ────────────────────────────────────────────────────


class TestCalcularPosicionFifo:
    def test_compra_unica(self):
        movs = [compra(1, D1, 10, 100.0)]
        r = calcular_posicion_fifo(movs)
        assert r["cantidad_actual"] == 10
        assert r["coste_total"] == 1000.0
        assert r["precio_medio"] == 100.0
        assert r["plusvalia_realizada"] == 0.0

    def test_multiples_compras(self):
        movs = [
            compra(1, D1, 10, 100.0),
            compra(2, D2, 5, 200.0),
        ]
        r = calcular_posicion_fifo(movs)
        assert r["cantidad_actual"] == 15
        assert r["coste_total"] == 2000.0  # 10*100 + 5*200
        assert r["precio_medio"] == pytest.approx(133.3333, abs=0.001)

    def test_venta_parcial_fifo(self):
        movs = [
            compra(1, D1, 10, 100.0),  # lote a 100
            compra(2, D2, 5, 200.0),  # lote a 200
            venta(3, D3, 6, 150.0),  # vende 6 del primer lote (FIFO)
        ]
        r = calcular_posicion_fifo(movs)
        # Quedan 4 del lote a 100 + 5 a 200
        assert r["cantidad_actual"] == pytest.approx(9, abs=1e-6)
        assert r["coste_total"] == pytest.approx(4 * 100 + 5 * 200, abs=0.01)
        # P/L realizada: ingreso=6*150=900, coste=6*100=600 → +300
        assert r["plusvalia_realizada"] == pytest.approx(300.0, abs=0.01)

    def test_venta_total(self):
        movs = [
            compra(1, D1, 10, 100.0),
            venta(2, D2, 10, 120.0),
        ]
        r = calcular_posicion_fifo(movs)
        assert r["cantidad_actual"] == 0
        assert r["coste_total"] == 0.0
        assert r["plusvalia_realizada"] == pytest.approx(200.0)  # 10*(120-100)

    def test_venta_invalida_lanza_error(self):
        movs = [
            compra(1, D1, 5, 100.0),
            venta(2, D2, 10, 100.0),  # vende más de lo comprado
        ]
        with pytest.raises(VentaInvalidaError):
            calcular_posicion_fifo(movs)

    def test_venta_total_con_ruido_de_coma_flotante(self):
        # 10.8 - 10.1 = 0.7000000000000011 in float: selling the whole
        # position must not leave an uncoverable sliver
        movs = [
            compra(1, D1, 10.1, 40.0),
            compra(2, D2, 0.7, 41.0),
            venta(3, D3, 10.8, 42.0),
        ]
        resultado = calcular_posicion_fifo(movs)
        assert resultado["cantidad_actual"] == 0
        assert resultado["lotes_pendientes"] == []

    def test_venta_que_supera_por_poco_mas_que_ruido_lanza_error(self):
        movs = [
            compra(1, D1, 5, 100.0),
            venta(2, D2, 5.000001, 100.0),
        ]
        with pytest.raises(VentaInvalidaError):
            calcular_posicion_fifo(movs)

    def test_orden_deterministico_mismo_dia(self):
        """Dos compras en el mismo día → la de id menor se consume primero."""
        movs = [
            compra(2, D1, 5, 200.0),  # id mayor, precio mayor
            compra(1, D1, 5, 100.0),  # id menor, precio menor
            venta(3, D2, 5, 150.0),  # debe consumir el id=1 (100€) primero
        ]
        r = calcular_posicion_fifo(movs)
        # P/L = 5*(150-100) = 250
        assert r["plusvalia_realizada"] == pytest.approx(250.0, abs=0.01)

    def test_compra_con_comision(self):
        movs = [compra(1, D1, 10, 100.0, comision=10.0)]
        r = calcular_posicion_fifo(movs)
        # Precio unitario = 100 + 10/10 = 101
        assert r["precio_medio"] == pytest.approx(101.0)
        assert r["coste_total"] == pytest.approx(1010.0)

    def test_venta_con_comision(self):
        movs = [
            compra(1, D1, 10, 100.0),
            venta(2, D2, 10, 120.0, comision=5.0),
        ]
        r = calcular_posicion_fifo(movs)
        # ingreso = 10*120 - 5 = 1195, coste = 10*100 = 1000, P/L = 195
        assert r["plusvalia_realizada"] == pytest.approx(195.0, abs=0.01)

    def test_tipo_cambio_aplicado_en_compra(self):
        # Precio en USD: 100 USD, tipo_cambio=1.1 (EURUSD=X: 1 EUR = 1.1 USD)
        # precio_eur = precio_nativo / tipo_cambio = 100 / 1.1 ≈ 90.909 EUR/acción
        movs = [compra(1, D1, 10, 100.0, tipo_cambio=1.1)]
        r = calcular_posicion_fifo(movs)
        assert r["precio_medio"] == pytest.approx(100.0 / 1.1, rel=1e-4)

    def test_tipo_cambio_none_equivale_a_1(self):
        movs_con_none = [compra(1, D1, 10, 100.0, tipo_cambio=None)]
        movs_sin_tc = [compra(2, D1, 10, 100.0)]
        r1 = calcular_posicion_fifo(movs_con_none)
        r2 = calcular_posicion_fifo(movs_sin_tc)
        assert r1["precio_medio"] == r2["precio_medio"]

    def test_lista_vacia(self):
        r = calcular_posicion_fifo([])
        assert r["cantidad_actual"] == 0
        assert r["coste_total"] == 0.0
        assert r["plusvalia_realizada"] == 0.0


# ── calcular_plusvalia_latente ────────────────────────────────────────────────


class TestCalcularPlusvalia:
    def _posicion(self, cantidad=10, coste=1000.0, plusvalia_realizada=0.0):
        return {
            "cantidad_actual": cantidad,
            "coste_total": coste,
            "precio_medio": coste / cantidad if cantidad else 0,
            "plusvalia_realizada": plusvalia_realizada,
        }

    # ── Backwards-compatibility (sin fx_actual) ───────────────────────────────

    def test_ganancia_eur(self):
        pos = self._posicion(10, 1000.0)
        r = calcular_plusvalia_latente(pos, 120.0)
        assert r["valor_actual"] == 1200.0
        assert r["plusvalia_latente"] == 200.0
        assert r["rentabilidad_pct"] == pytest.approx(20.0)

    def test_perdida_eur(self):
        pos = self._posicion(10, 1000.0)
        r = calcular_plusvalia_latente(pos, 80.0)
        assert r["plusvalia_latente"] == -200.0
        assert r["rentabilidad_pct"] == pytest.approx(-20.0)

    def test_plusvalia_total_incluye_realizada(self):
        pos = self._posicion(10, 1000.0, plusvalia_realizada=50.0)
        r = calcular_plusvalia_latente(pos, 100.0)
        assert r["plusvalia_total"] == pytest.approx(50.0)

    def test_coste_cero_no_divide_por_cero(self):
        pos = self._posicion(0, 0.0)
        r = calcular_plusvalia_latente(pos, 100.0)
        assert r["rentabilidad_pct"] == 0.0

    def test_valor_actual_es_alias_de_valor_actual_eur(self):
        """valor_actual debe ser igual a valor_actual_eur (backwards-compat)."""
        pos = self._posicion(10, 1000.0)
        r = calcular_plusvalia_latente(pos, 120.0)
        assert r["valor_actual"] == r["valor_actual_eur"]

    # ── Dual-currency con fx_actual ───────────────────────────────────────────

    def test_usd_conversión_correcta(self):
        """
        Compra: 10 acciones a $150, tipo_cambio histórico 1.10 → coste = 10*150/1.10 = €1363.64
        Precio actual: $180, fx_actual (EURUSD=X) = 1.05
        valor_actual_eur = 10*180/1.05 = €1714.29
        valor_actual_nativo = 10*180 = $1800
        """
        # coste_total ya en EUR (calculado por FIFO con tipo_cambio histórico)
        pos = self._posicion(cantidad=10, coste=round(10 * 150 / 1.10, 2))
        r = calcular_plusvalia_latente(pos, precio_actual_nativo=180.0, fx_actual=1.05)

        assert r["valor_actual_nativo"] == pytest.approx(1800.0)
        assert r["valor_actual_eur"] == pytest.approx(1714.29, abs=0.01)
        assert r["plusvalia_latente"] == pytest.approx(
            1714.29 - round(10 * 150 / 1.10, 2), abs=0.01
        )
        assert r["rentabilidad_pct"] == pytest.approx(
            (r["plusvalia_latente"] / pos["coste_total"]) * 100, abs=0.01
        )

    def test_eur_fx_1_valor_nativo_igual_eur(self):
        """Para EUR: fx_actual=1.0 → valor_actual_nativo == valor_actual_eur."""
        pos = self._posicion(10, 1000.0)
        r = calcular_plusvalia_latente(pos, precio_actual_nativo=120.0, fx_actual=1.0)
        assert r["valor_actual_nativo"] == r["valor_actual_eur"]
        assert r["valor_actual_eur"] == pytest.approx(1200.0)

    def test_fx_alto_reduce_valor_eur(self):
        """EUR más fuerte (fx alto) reduce el valor en EUR de activo extranjero."""
        pos = self._posicion(10, 1000.0)
        r_bajo = calcular_plusvalia_latente(pos, 100.0, fx_actual=1.0)
        r_alto = calcular_plusvalia_latente(pos, 100.0, fx_actual=1.20)
        assert r_alto["valor_actual_eur"] < r_bajo["valor_actual_eur"]

    def test_nuevos_campos_presentes(self):
        """La respuesta debe incluir todos los campos del contrato."""
        pos = self._posicion(10, 1000.0)
        r = calcular_plusvalia_latente(pos, 100.0, fx_actual=1.05)
        for campo in (
            "valor_actual",
            "valor_actual_eur",
            "valor_actual_nativo",
            "plusvalia_latente",
            "rentabilidad_pct",
            "plusvalia_total",
        ):
            assert campo in r


# ── calcular_resumen_cartera ──────────────────────────────────────────────────


class TestCalcularResumen:
    def _pos(
        self,
        valor_actual=None,
        coste_total=1000.0,
        plusvalia_latente=None,
        plusvalia_realizada=0.0,
        cantidad_actual=10,
    ):
        return {
            "valor_actual": valor_actual,
            "coste_total": coste_total,
            "plusvalia_latente": plusvalia_latente,
            "plusvalia_realizada": plusvalia_realizada,
            "cantidad_actual": cantidad_actual,
        }

    def test_resumen_con_precios(self):
        posiciones = [
            self._pos(
                valor_actual=1200.0,
                coste_total=1000.0,
                plusvalia_latente=200.0,
                plusvalia_realizada=50.0,
            ),
            self._pos(
                valor_actual=800.0,
                coste_total=1000.0,
                plusvalia_latente=-200.0,
                plusvalia_realizada=0.0,
            ),
        ]
        r = calcular_resumen_cartera(posiciones)
        assert r["valor_total"] == 2000.0
        assert r["coste_total"] == 2000.0
        assert r["plusvalia_latente"] == 0.0
        assert r["plusvalia_realizada"] == 50.0
        assert r["plusvalia_total"] == 50.0
        assert r["num_posiciones"] == 2

    def test_resumen_sin_precios(self):
        """Sin precio actual, valor_total usa coste_total como fallback."""
        posiciones = [
            self._pos(valor_actual=None, coste_total=1000.0, plusvalia_latente=None)
        ]
        r = calcular_resumen_cartera(posiciones)
        assert r["valor_total"] == 1000.0  # fallback a coste_total
        assert r["plusvalia_latente"] == 0.0

    def test_resumen_vacio(self):
        r = calcular_resumen_cartera([])
        assert r["valor_total"] == 0.0
        assert r["rentabilidad_pct"] == 0.0
        assert r["num_posiciones"] == 0

    def test_rentabilidad_usa_plusvalia_total(self):
        """La rentabilidad debe basarse en plusvalia_total (latente + realizada)."""
        posiciones = [
            self._pos(
                valor_actual=1000.0,
                coste_total=1000.0,
                plusvalia_latente=0.0,  # sin ganancia latente
                plusvalia_realizada=100.0,  # pero sí realizada
            )
        ]
        r = calcular_resumen_cartera(posiciones)
        # rentabilidad = (0+100)/1000 * 100 = 10%
        assert r["rentabilidad_pct"] == pytest.approx(10.0)


# ── agrupar_por_campo ─────────────────────────────────────────────────────────


class TestAgruparPorCampo:
    def test_agrupacion_basica(self):
        posiciones = [
            {"sector": "Tecnología", "valor_actual": 1000.0, "coste_total": 800.0},
            {"sector": "Tecnología", "valor_actual": 500.0, "coste_total": 400.0},
            {"sector": "Salud", "valor_actual": 300.0, "coste_total": 200.0},
        ]
        r = agrupar_por_campo(posiciones, "sector")
        assert r[0]["nombre"] == "Tecnología"
        assert r[0]["valor"] == 1500.0
        assert r[1]["nombre"] == "Salud"

    def test_campo_none_va_a_sin_clasificar(self):
        posiciones = [
            {"sector": None, "valor_actual": 500.0, "coste_total": 400.0},
        ]
        r = agrupar_por_campo(posiciones, "sector")
        assert r[0]["nombre"] == "Sin clasificar"

    def test_pesos_suman_100(self):
        posiciones = [
            {"tipo": "accion", "valor_actual": 600.0, "coste_total": 500.0},
            {"tipo": "etf", "valor_actual": 400.0, "coste_total": 300.0},
        ]
        r = agrupar_por_campo(posiciones, "tipo")
        total_pct = sum(g["peso_pct"] for g in r)
        assert total_pct == pytest.approx(100.0, abs=0.1)

    def test_sin_valor_actual_usa_coste(self):
        posiciones = [
            {"sector": "Energía", "valor_actual": None, "coste_total": 700.0},
        ]
        r = agrupar_por_campo(posiciones, "sector")
        assert r[0]["valor"] == 700.0

    def test_lista_vacia(self):
        r = agrupar_por_campo([], "sector")
        assert r == []

    def test_orden_descendente_por_valor(self):
        posiciones = [
            {"pais": "España", "valor_actual": 100.0, "coste_total": 80.0},
            {"pais": "EEUU", "valor_actual": 900.0, "coste_total": 700.0},
        ]
        r = agrupar_por_campo(posiciones, "pais")
        assert r[0]["nombre"] == "EEUU"


# ── fecha_inicio_periodo ──────────────────────────────────────────────────────


class TestFechaInicioPeriodo:
    HOY = date(2026, 8, 1)

    def test_ytd(self):
        assert fecha_inicio_periodo("ytd", self.HOY) == date(2026, 1, 1)

    def test_1m(self):
        assert fecha_inicio_periodo("1m", self.HOY) == date(2026, 7, 1)

    def test_3m(self):
        assert fecha_inicio_periodo("3m", self.HOY) == date(2026, 5, 1)

    def test_1y(self):
        assert fecha_inicio_periodo("1y", self.HOY) == date(2025, 8, 1)

    def test_3y_cruza_decada(self):
        assert fecha_inicio_periodo("3y", self.HOY) == date(2023, 8, 1)

    def test_clamp_fin_de_mes_a_mes_mas_corto(self):
        """31 marzo - 1m debe caer en el último día de febrero (28/29), no el 31."""
        r = fecha_inicio_periodo("1m", date(2026, 3, 31))
        assert r == date(2026, 2, 28)

    def test_periodo_invalido_lanza_error(self):
        with pytest.raises(ValueError):
            fecha_inicio_periodo("5m", self.HOY)


# ── calcular_rentabilidad_periodo ─────────────────────────────────────────────


class TestCalcularRentabilidadPeriodo:
    ANTES = date(2024, 1, 1)
    FECHA_INICIO = date(2024, 6, 1)
    EN_PERIODO = date(2024, 7, 1)

    def test_posicion_previa_sin_movimientos_en_periodo(self):
        movs = [compra(1, self.ANTES, 10, 100.0)]
        r = calcular_rentabilidad_periodo(
            movs, self.FECHA_INICIO, precio_inicio_eur=150.0, precio_actual_eur=180.0
        )
        assert r["cantidad_actual"] == 10
        assert r["coste_total"] == 1500.0  # 10 * precio de inicio de periodo
        assert r["valor_actual"] == 1800.0
        assert r["plusvalia_latente"] == 300.0
        assert r["rentabilidad_pct"] == pytest.approx(20.0)

    def test_posicion_abierta_dentro_del_periodo_usa_precio_de_compra_real(self):
        movs = [compra(1, self.EN_PERIODO, 5, 100.0)]
        r = calcular_rentabilidad_periodo(
            movs, self.FECHA_INICIO, precio_inicio_eur=None, precio_actual_eur=120.0
        )
        assert r["coste_total"] == 500.0
        assert r["valor_actual"] == 600.0
        assert r["rentabilidad_pct"] == pytest.approx(20.0)

    def test_sin_precio_historico_devuelve_none(self):
        movs = [compra(1, self.ANTES, 10, 100.0)]
        r = calcular_rentabilidad_periodo(
            movs, self.FECHA_INICIO, precio_inicio_eur=None, precio_actual_eur=180.0
        )
        assert r is None

    def test_venta_dentro_del_periodo_de_lote_previo(self):
        movs = [
            compra(1, self.ANTES, 10, 100.0),
            venta(2, self.EN_PERIODO, 4, 200.0),
        ]
        r = calcular_rentabilidad_periodo(
            movs, self.FECHA_INICIO, precio_inicio_eur=150.0, precio_actual_eur=180.0
        )
        assert r["cantidad_actual"] == 6
        assert r["coste_total"] == 900.0
        assert r["plusvalia_realizada"] == 200.0  # (200*4) - (150*4)
        assert r["valor_actual"] == 1080.0
        assert r["plusvalia_latente"] == 180.0
        assert r["plusvalia_total"] == 380.0
        assert r["rentabilidad_pct"] == pytest.approx(380 / 900 * 100, abs=0.01)

    def test_sin_actividad_cantidad_inicio_cero_y_sin_movimientos_periodo(self):
        r = calcular_rentabilidad_periodo(
            [], self.FECHA_INICIO, precio_inicio_eur=None, precio_actual_eur=None
        )
        assert r["cantidad_actual"] == 0
        assert r["coste_total"] == 0.0
        assert r["rentabilidad_pct"] == 0.0
