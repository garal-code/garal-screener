"""
Tests unitarios para app/services/precios.py

Las llamadas a yfinance se mockean via unittest.mock.
Las llamadas a httpx (OpenFIGI) se mockean con AsyncMock.
"""

from datetime import date as _date
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from app.services.precios import (
    ProveedorPreciosError,
    buscar_ticker_por_isin,
    fin_ultimo_mes_completo,
    obtener_fx,
    obtener_fx_batch,
    obtener_fx_by_date,
    obtener_precio_actual,
    obtener_precio_by_date,
    obtener_precios_batch,
    obtener_precios_by_date_batch,
    obtener_precios_mensuales_batch,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def mock_httpx_response(json_data, status_code=200):
    """Construye un mock de httpx.Response."""
    response = MagicMock()
    response.json.return_value = json_data
    response.status_code = status_code
    response.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx

        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=response
        )
    return response


# ── obtener_precio_actual ─────────────────────────────────────────────────────


class TestObtenerPrecioActual:
    @pytest.mark.asyncio
    async def test_devuelve_precio(self):
        perfil = {"precio": 150.0, "nombre": "Apple", "tipo": "accion"}
        with patch("app.services.precios._obtener_info_yfinance", return_value=perfil):
            precio = await obtener_precio_actual("AAPL")
        assert precio == 150.0

    @pytest.mark.asyncio
    async def test_devuelve_none_cuando_yfinance_falla(self):
        with patch("app.services.precios._obtener_info_yfinance", return_value=None):
            precio = await obtener_precio_actual("TICKER_INVALIDO")
        assert precio is None

    @pytest.mark.asyncio
    async def test_ticker_vacio_devuelve_none(self):
        precio = await obtener_precio_actual("")
        assert precio is None

    @pytest.mark.asyncio
    async def test_perfil_sin_precio_devuelve_none(self):
        perfil = {"nombre": "Sin precio", "tipo": "accion", "precio": None}
        with patch("app.services.precios._obtener_info_yfinance", return_value=perfil):
            precio = await obtener_precio_actual("AAPL")
        assert precio is None


# ── obtener_precios_batch ─────────────────────────────────────────────────────


class TestObtenerPreciosBatch:
    @pytest.mark.asyncio
    async def test_batch_multiple_tickers(self):
        perfiles = {
            "AAPL": {"precio": 150.0},
            "MSFT": {"precio": 300.0},
        }

        def fake_yfinance(ticker):
            return perfiles.get(ticker)

        with patch(
            "app.services.precios._obtener_info_yfinance", side_effect=fake_yfinance
        ):
            result = await obtener_precios_batch(["AAPL", "MSFT"])
        assert result == {"AAPL": 150.0, "MSFT": 300.0}

    @pytest.mark.asyncio
    async def test_lista_vacia_no_llama_yfinance(self):
        with patch("app.services.precios._obtener_info_yfinance") as mock_yf:
            result = await obtener_precios_batch([])
        mock_yf.assert_not_called()
        assert result == {}

    @pytest.mark.asyncio
    async def test_ticker_sin_precio_excluido(self):
        """Un ticker cuyo yfinance devuelve None no aparece en el resultado."""
        perfiles = {
            "NVDA": {"precio": 500.0},
            "UNKNOWN": None,
        }

        def fake_yfinance(ticker):
            return perfiles.get(ticker)

        with patch(
            "app.services.precios._obtener_info_yfinance", side_effect=fake_yfinance
        ):
            result = await obtener_precios_batch(["NVDA", "UNKNOWN"])
        assert result == {"NVDA": 500.0}

    @pytest.mark.asyncio
    async def test_error_en_yfinance_excluye_ticker(self):
        """Si yfinance lanza excepción en un ticker, ese ticker se excluye."""
        call_count = 0

        def fake_yfinance(ticker):
            nonlocal call_count
            call_count += 1
            if ticker == "BAD":
                raise RuntimeError("network error")
            return {"precio": 100.0}

        with patch(
            "app.services.precios._obtener_info_yfinance", side_effect=fake_yfinance
        ):
            result = await obtener_precios_batch(["AAPL", "BAD"])
        assert "AAPL" in result
        assert "BAD" not in result


# ── buscar_ticker_por_isin ────────────────────────────────────────────────────


class TestBuscarTickerPorIsin:
    @pytest.mark.asyncio
    async def test_devuelve_ticker(self):
        # OpenFIGI devuelve lista con data: [{ticker, exchCode, ...}]
        data = [{"data": [{"ticker": "SAN", "exchCode": "SM"}]}]
        resp = mock_httpx_response(data)
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=AsyncMock(return_value=resp))
            )
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            ticker = await buscar_ticker_por_isin("ES0113900J37")
        assert ticker == "SAN.MC"

    @pytest.mark.asyncio
    async def test_sin_resultados_devuelve_none(self):
        # OpenFIGI responde pero sin data en el resultado
        data = [{}]
        resp = mock_httpx_response(data)
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(post=AsyncMock(return_value=resp))
            )
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            ticker = await buscar_ticker_por_isin("XX0000000000")
        assert ticker is None

    @pytest.mark.asyncio
    async def test_error_devuelve_none(self):
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                side_effect=Exception("network")
            )
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            ticker = await buscar_ticker_por_isin("XX0000000000")
        assert ticker is None


# ── obtener_fx_historico ──────────────────────────────────────────────────────


class TestObtenerFxHistorico:
    @pytest.mark.asyncio
    async def test_devuelve_float_cuando_hay_datos(self):
        with patch("app.services.precios._fetch_fx_by_date", return_value=1.085):
            result = await obtener_fx_by_date("USD", _date(2024, 1, 15))
        assert result == pytest.approx(1.085)

    @pytest.mark.asyncio
    async def test_eur_devuelve_1_sin_llamar_yfinance(self):
        with patch("app.services.precios._fetch_fx_by_date") as mock_fetch:
            result = await obtener_fx_by_date("EUR", _date(2024, 1, 15))
        assert result == 1.0
        mock_fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_devuelve_none_cuando_no_hay_datos(self):
        with patch("app.services.precios._fetch_fx_by_date", return_value=None):
            result = await obtener_fx_by_date("USD", _date(2024, 1, 15))
        assert result is None

    @pytest.mark.asyncio
    async def test_eur_mayusculas_minusculas(self):
        with patch("app.services.precios._fetch_fx_by_date") as mock_fetch:
            result = await obtener_fx_by_date("eur", _date(2024, 1, 15))
        assert result == 1.0
        mock_fetch.assert_not_called()

    def test_fetch_fx_historico_dataframe_vacio_devuelve_none(self):
        from app.services.precios import _fetch_fx_by_date

        empty_df = pd.DataFrame({"Close": []})
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = empty_df
            result = _fetch_fx_by_date("EURUSD=X", _date(2024, 1, 15))
        assert result is None

    def test_fetch_fx_historico_devuelve_primer_cierre(self):
        from app.services.precios import _fetch_fx_by_date

        df = pd.DataFrame({"Close": [1.085, 1.090, 1.092]})
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = df
            result = _fetch_fx_by_date("EURUSD=X", _date(2024, 1, 15))
        assert result == pytest.approx(1.085)


# ── obtener_precio_by_date ────────────────────────────────────────────────────


class TestObtenerPrecioHistorico:
    @pytest.mark.asyncio
    async def test_devuelve_float_cuando_hay_datos(self):
        with patch("app.services.precios._fetch_precio_by_date", return_value=4100.0):
            result = await obtener_precio_by_date("7751.T", _date(2024, 1, 15))
        assert result == pytest.approx(4100.0)

    @pytest.mark.asyncio
    async def test_ticker_vacio_devuelve_none_sin_llamar_yfinance(self):
        with patch("app.services.precios._fetch_precio_by_date") as mock_fetch:
            result = await obtener_precio_by_date("", _date(2024, 1, 15))
        assert result is None
        mock_fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_devuelve_none_cuando_no_hay_datos(self):
        with patch("app.services.precios._fetch_precio_by_date", return_value=None):
            result = await obtener_precio_by_date("7751.T", _date(2024, 1, 15))
        assert result is None

    def test_fetch_dataframe_vacio_devuelve_none(self):
        from app.services.precios import _fetch_precio_by_date

        empty_df = pd.DataFrame({"Close": []})
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = empty_df
            result = _fetch_precio_by_date("7751.T", _date(2024, 1, 15))
        assert result is None

    def test_fetch_devuelve_primer_cierre(self):
        from app.services.precios import _fetch_precio_by_date

        df = pd.DataFrame({"Close": [4100.0, 4150.0]})
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history.return_value = df
            result = _fetch_precio_by_date("7751.T", _date(2024, 1, 15))
        assert result == pytest.approx(4100.0)


class TestObtenerPreciosByDateBatch:
    @pytest.mark.asyncio
    async def test_lista_vacia_devuelve_dict_vacio(self):
        result = await obtener_precios_by_date_batch([], _date(2024, 1, 15))
        assert result == {}

    @pytest.mark.asyncio
    async def test_agrupa_resultados_validos_e_ignora_fallos(self):
        async def fake_fetch(ticker, fecha):
            return 100.0 if ticker == "AAPL" else None

        with patch(
            "app.services.precios.obtener_precio_by_date", side_effect=fake_fetch
        ):
            result = await obtener_precios_by_date_batch(
                ["AAPL", "BAD"], _date(2024, 1, 15)
            )
        assert result == {"AAPL": 100.0}


# ── obtener_fx_actual ─────────────────────────────────────────────────────────


class TestObtenerFxActual:
    @pytest.mark.asyncio
    async def test_devuelve_float(self):
        with patch("app.services.precios._fetch_fx", return_value=1.085):
            result = await obtener_fx("USD")
        assert result == pytest.approx(1.085)

    @pytest.mark.asyncio
    async def test_eur_devuelve_1_sin_llamar_yfinance(self):
        with patch("app.services.precios._fetch_fx") as mock_fetch:
            result = await obtener_fx("EUR")
        assert result == 1.0
        mock_fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_devuelve_none_cuando_falla(self):
        with patch("app.services.precios._fetch_fx", return_value=None):
            result = await obtener_fx("GBP")
        assert result is None


# ── obtener_fx_batch ──────────────────────────────────────────────────────────


class TestObtenerFxBatch:
    @pytest.mark.asyncio
    async def test_devuelve_dict_con_monedas(self):
        fx_map = {"USD": 1.085, "GBP": 0.857}

        async def fake_fx_actual(moneda):
            return fx_map.get(moneda.upper())

        with patch("app.services.precios.obtener_fx", side_effect=fake_fx_actual):
            result = await obtener_fx_batch(["USD", "GBP"])
        assert result == {"USD": pytest.approx(1.085), "GBP": pytest.approx(0.857)}

    @pytest.mark.asyncio
    async def test_lista_vacia_devuelve_dict_vacio(self):
        with patch("app.services.precios.obtener_fx") as mock_fx:
            result = await obtener_fx_batch([])
        mock_fx.assert_not_called()
        assert result == {}

    @pytest.mark.asyncio
    async def test_eur_excluido_del_batch(self):
        with patch("app.services.precios.obtener_fx") as mock_fx:
            result = await obtener_fx_batch(["EUR"])
        mock_fx.assert_not_called()
        assert result == {}

    @pytest.mark.asyncio
    async def test_moneda_sin_datos_excluida(self):
        async def fake_fx_actual(moneda):
            return 1.085 if moneda == "USD" else None

        with patch("app.services.precios.obtener_fx", side_effect=fake_fx_actual):
            result = await obtener_fx_batch(["USD", "JPY"])
        assert "USD" in result
        assert "JPY" not in result

    @pytest.mark.asyncio
    async def test_deduplicacion_monedas_repetidas(self):
        calls = []

        async def fake_fx_actual(moneda):
            calls.append(moneda)
            return 1.085

        with patch("app.services.precios.obtener_fx", side_effect=fake_fx_actual):
            await obtener_fx_batch(["USD", "USD", "usd"])
        # Solo debe llamarse una vez (set de monedas únicas)
        assert len(calls) == 1


# ── obtener_precios_mensuales_batch ───────────────────────────────────────────


def _df_descarga_multi(datos: dict[str, list[float]], fechas: list[str]):
    """Simula yf.download con varios tickers: columnas MultiIndex (Price, Ticker)."""
    columnas = pd.MultiIndex.from_tuples(
        [(campo, t) for campo in ("Close", "Open") for t in datos],
        names=["Price", "Ticker"],
    )
    valores = {
        (campo, t): serie for campo in ("Close", "Open") for t, serie in datos.items()
    }
    return pd.DataFrame(valores, index=pd.to_datetime(fechas), columns=columnas)


class TestFetchPreciosMensuales:
    FECHAS = ["2024-01-01", "2024-02-01", "2024-03-01"]

    def test_varios_tickers_devuelve_series_ordenadas(self):
        from app.services.precios import _fetch_precios_mensuales

        df = _df_descarga_multi(
            {"AAPL": [100.0, 110.0, 121.0], "MSFT": [50.0, 55.0, 60.5]}, self.FECHAS
        )
        with patch("yfinance.download", return_value=df) as mock_dl:
            result = _fetch_precios_mensuales(
                ["AAPL", "MSFT"], _date(2024, 1, 1), _date(2024, 3, 31)
            )
        # Una única descarga para todos los tickers, mensual y ajustada
        mock_dl.assert_called_once()
        kwargs = mock_dl.call_args.kwargs
        assert kwargs["interval"] == "1mo"
        assert kwargs["auto_adjust"] is True
        assert result == {
            "AAPL": [
                (_date(2024, 1, 1), 100.0),
                (_date(2024, 2, 1), 110.0),
                (_date(2024, 3, 1), 121.0),
            ],
            "MSFT": [
                (_date(2024, 1, 1), 50.0),
                (_date(2024, 2, 1), 55.0),
                (_date(2024, 3, 1), 60.5),
            ],
        }

    def test_un_ticker_con_columnas_planas(self):
        from app.services.precios import _fetch_precios_mensuales

        df = pd.DataFrame(
            {"Close": [10.0, 11.0], "Open": [9.0, 10.5]},
            index=pd.to_datetime(self.FECHAS[:2]),
        )
        with patch("yfinance.download", return_value=df):
            result = _fetch_precios_mensuales(
                ["SAN.MC"], _date(2024, 1, 1), _date(2024, 2, 28)
            )
        assert result == {
            "SAN.MC": [(_date(2024, 1, 1), 10.0), (_date(2024, 2, 1), 11.0)]
        }

    def test_un_ticker_con_columnas_multiindex(self):
        from app.services.precios import _fetch_precios_mensuales

        df = _df_descarga_multi({"SAN.MC": [10.0, 11.0]}, self.FECHAS[:2])
        with patch("yfinance.download", return_value=df):
            result = _fetch_precios_mensuales(
                ["SAN.MC"], _date(2024, 1, 1), _date(2024, 2, 28)
            )
        assert result == {
            "SAN.MC": [(_date(2024, 1, 1), 10.0), (_date(2024, 2, 1), 11.0)]
        }

    def test_ticker_sin_datos_se_omite_y_nan_se_descartan(self):
        from app.services.precios import _fetch_precios_mensuales

        nan = float("nan")
        df = _df_descarga_multi(
            {"AAPL": [100.0, nan, 121.0], "BAD": [nan, nan, nan]}, self.FECHAS
        )
        with patch("yfinance.download", return_value=df):
            result = _fetch_precios_mensuales(
                ["AAPL", "BAD"], _date(2024, 1, 1), _date(2024, 3, 31)
            )
        assert result == {
            "AAPL": [(_date(2024, 1, 1), 100.0), (_date(2024, 3, 1), 121.0)]
        }

    def test_fechas_se_normalizan_al_primer_dia_del_mes(self):
        from app.services.precios import _fetch_precios_mensuales

        # El mes en curso puede venir fechado a mitad de mes; si coincide con
        # otra fila del mismo mes prevalece la más reciente.
        df = _df_descarga_multi(
            {"AAPL": [100.0, 110.0, 112.0], "MSFT": [50.0, 55.0, 56.0]},
            ["2024-01-01", "2024-02-01", "2024-02-15"],
        )
        with patch("yfinance.download", return_value=df):
            result = _fetch_precios_mensuales(
                ["AAPL", "MSFT"], _date(2024, 1, 1), _date(2024, 2, 15)
            )
        assert result["AAPL"] == [
            (_date(2024, 1, 1), 100.0),
            (_date(2024, 2, 1), 112.0),
        ]

    def test_error_de_red_lanza_error_de_proveedor(self):
        from app.services.precios import _fetch_precios_mensuales

        # Un fallo del proveedor nunca debe confundirse con "sin datos"
        with patch("yfinance.download", side_effect=ConnectionError("timeout")):
            with pytest.raises(ProveedorPreciosError):
                _fetch_precios_mensuales(
                    ["AAPL"], _date(2024, 1, 1), _date(2024, 3, 31)
                )

    def test_descarga_vacia_lanza_error_de_proveedor(self):
        from app.services.precios import _fetch_precios_mensuales

        with patch("yfinance.download", return_value=pd.DataFrame()):
            with pytest.raises(ProveedorPreciosError):
                _fetch_precios_mensuales(
                    ["AAPL"], _date(2024, 1, 1), _date(2024, 3, 31)
                )

    def test_todo_nan_lanza_error_de_proveedor(self):
        from app.services.precios import _fetch_precios_mensuales

        # yfinance suele registrar el fallo y devolver columnas vacías en vez
        # de lanzar una excepción
        nan = float("nan")
        df = _df_descarga_multi(
            {"AAPL": [nan, nan, nan], "MSFT": [nan, nan, nan]}, self.FECHAS
        )
        with patch("yfinance.download", return_value=df):
            with pytest.raises(ProveedorPreciosError):
                _fetch_precios_mensuales(
                    ["AAPL", "MSFT"], _date(2024, 1, 1), _date(2024, 3, 31)
                )

    def test_mes_en_curso_nunca_se_devuelve(self):
        from app.services.precios import _fetch_precios_mensuales

        # Aunque el rango pedido incluya el mes en curso (incompleto), su fila
        # no se usa: solo cuentan meses ya cerrados.
        df = _df_descarga_multi(
            {"AAPL": [100.0, 110.0, 115.0]},
            ["2026-08-01", "2026-09-01", "2026-10-03"],
        )
        with (
            patch("app.services.precios._hoy", return_value=_date(2026, 10, 4)),
            patch("yfinance.download", return_value=df),
        ):
            result = _fetch_precios_mensuales(
                ["AAPL"], _date(2026, 8, 1), _date(2026, 10, 4)
            )
        assert result == {
            "AAPL": [(_date(2026, 8, 1), 100.0), (_date(2026, 9, 1), 110.0)]
        }

    def test_solo_el_mes_en_curso_no_es_fallo_del_proveedor(self):
        from app.services.precios import _fetch_precios_mensuales

        # Un valor recién listado solo tiene el mes en curso: sin histórico
        # utilizable, pero el proveedor sí respondió.
        df = _df_descarga_multi({"NEW": [10.0]}, ["2026-10-03"])
        with (
            patch("app.services.precios._hoy", return_value=_date(2026, 10, 4)),
            patch("yfinance.download", return_value=df),
        ):
            result = _fetch_precios_mensuales(
                ["NEW"], _date(2026, 8, 1), _date(2026, 10, 4)
            )
        assert result == {}


class TestFinUltimoMesCompleto:
    def test_ultimo_dia_del_mes_anterior(self):
        assert fin_ultimo_mes_completo(_date(2026, 10, 4)) == _date(2026, 9, 30)

    def test_primer_dia_del_mes_tampoco_cuenta_el_mes_en_curso(self):
        assert fin_ultimo_mes_completo(_date(2026, 3, 1)) == _date(2026, 2, 28)

    def test_enero_cierra_en_diciembre_del_anio_anterior(self):
        assert fin_ultimo_mes_completo(_date(2026, 1, 15)) == _date(2025, 12, 31)

    def test_por_defecto_usa_la_fecha_actual(self):
        with patch("app.services.precios._hoy", return_value=_date(2024, 3, 10)):
            assert fin_ultimo_mes_completo() == _date(2024, 2, 29)


class TestObtenerPreciosMensualesBatch:
    @pytest.mark.asyncio
    async def test_lista_vacia_no_llama_a_yfinance(self):
        with patch("app.services.precios._fetch_precios_mensuales") as mock_fetch:
            result = await obtener_precios_mensuales_batch(
                [], _date(2024, 1, 1), _date(2024, 3, 31)
            )
        assert result == {}
        mock_fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_deduplica_tickers_y_delega_en_una_descarga(self):
        serie = [(_date(2024, 1, 1), 100.0)]
        with patch(
            "app.services.precios._fetch_precios_mensuales",
            return_value={"AAPL": serie},
        ) as mock_fetch:
            result = await obtener_precios_mensuales_batch(
                ["AAPL", "AAPL", ""], _date(2024, 1, 1), _date(2024, 3, 31)
            )
        assert result == {"AAPL": serie}
        mock_fetch.assert_called_once_with(
            ["AAPL"], _date(2024, 1, 1), _date(2024, 3, 31)
        )

    @pytest.mark.asyncio
    async def test_error_de_proveedor_se_propaga(self):
        with patch(
            "app.services.precios._fetch_precios_mensuales",
            side_effect=ProveedorPreciosError("caído"),
        ):
            with pytest.raises(ProveedorPreciosError):
                await obtener_precios_mensuales_batch(
                    ["AAPL"], _date(2024, 1, 1), _date(2024, 3, 31)
                )
