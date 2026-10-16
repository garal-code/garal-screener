"""
Servicio de metadatos y precios de instrumentos financieros.

- Ticker desde ISIN  → OpenFIGI (gratuito, sin API key)
- Metadatos + precio → yfinance (Yahoo Finance, sin API key)
- Tipos de cambio FX → yfinance (EUR{MONEDA}=X, ej. EURUSD=X)

Convenio FX de Yahoo Finance
-----------------------------
EURUSD=X devuelve cuántos USD vale 1 EUR (ej. 1.085).
Para convertir precio en moneda nativa a EUR: precio_nativo / fx
Funciones de este módulo siguen ese convenio y lo documentan en sus
firmas para evitar confusiones.
"""

import asyncio
from datetime import date, timedelta

import httpx
import pandas as pd
import yfinance as yf

_YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Mapeo país del ISIN → (exchCode OpenFIGI, sufijo Yahoo Finance)
_ISIN_EXCHANGE_MAP = {
    "ES": ("SM", ".MC"),  # BME Madrid
    "DE": ("GR", ".DE"),  # XETRA
    "FR": ("FP", ".PA"),  # Euronext Paris
    "GB": ("LN", ".L"),  # LSE
    "IT": ("IM", ".MI"),  # Borsa Italiana
    "NL": ("NA", ".AS"),  # Euronext Amsterdam
    "PT": ("PL", ".LS"),  # Euronext Lisboa
    "CH": ("SW", ".SW"),  # SIX Swiss
    "JP": ("JT", ".T"),  # TSE
    "US": ("US", ""),  # NYSE/NASDAQ — sin sufijo
    "IE": ("NA", ".AS"),  # ETFs irlandeses → Euronext Amsterdam (más completo en Yahoo)
    "LU": ("NA", ".AS"),  # ETFs luxemburgueses → Euronext Amsterdam
}


async def enriquecer_por_isin(isin: str) -> dict | None:
    """
    Resuelve ISIN → metadatos completos con estrategia en cascada:
    1. OpenFIGI → ticker → yfinance
    2. Si falla: búsqueda directa por ISIN en Yahoo → yfinance
    Devuelve dict con ticker, nombre, tipo, sector, pais, moneda, exchange.
    Devuelve None si todas las fuentes fallan.
    """
    loop = asyncio.get_event_loop()

    # 1. Intentar via OpenFIGI
    ticker = await _buscar_ticker_en_openfigi(isin)
    if ticker:
        perfil = await loop.run_in_executor(None, _obtener_info_yfinance, ticker)
        if perfil:
            return {
                "ticker": ticker,
                "nombre": perfil.get("nombre"),
                "tipo": perfil.get("tipo"),
                "sector": perfil.get("sector"),
                "pais": perfil.get("pais"),
                "moneda": perfil.get("moneda"),
                "exchange": perfil.get("exchange"),
            }
        print(
            f"[yfinance] Ticker {ticker} no encontrado, intentando búsqueda directa por ISIN..."
        )

    # 2. Fallback: búsqueda directa en Yahoo por ISIN, iterando candidatos
    candidatos = await _buscar_tickers_en_yahoo(isin)
    if not candidatos:
        print(f"[Yahoo Search] No se encontró ningún ticker para ISIN {isin}")
        return None

    for ticker in candidatos:
        perfil = await loop.run_in_executor(None, _obtener_info_yfinance, ticker)
        if perfil:
            return {
                "ticker": ticker,
                "nombre": perfil.get("nombre"),
                "tipo": perfil.get("tipo"),
                "sector": perfil.get("sector"),
                "pais": perfil.get("pais"),
                "moneda": perfil.get("moneda"),
                "exchange": perfil.get("exchange"),
            }

    print(
        f"[yfinance] Ningún candidato de Yahoo funcionó para ISIN {isin}: {candidatos}"
    )
    return None


async def _buscar_ticker_en_openfigi(isin: str) -> str | None:
    """
    Resuelve ISIN → ticker via OpenFIGI (gratuito, sin API key).
    Prioriza el exchange del país de origen del ISIN.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                "https://api.openfigi.com/v3/mapping",
                headers={"Content-Type": "application/json"},
                json=[{"idType": "ID_ISIN", "idValue": isin}],
            )
            r.raise_for_status()
            data = r.json()
            if not data or "data" not in data[0]:
                return None

            resultados = data[0]["data"]
            pais = isin[:2].upper()
            exch_code, sufijo = _ISIN_EXCHANGE_MAP.get(pais, ("", ""))

            # 1. Buscar en el exchange preferido del país de origen
            if exch_code:
                for item in resultados:
                    if item.get("exchCode") == exch_code:
                        return str(item.get("ticker", "")) + sufijo

            # 2. Fallback: preferir exchange US (NYSE/NASDAQ) si existe
            for item in resultados:
                if item.get("exchCode") == "US":
                    return str(item.get("ticker", ""))

            # 3. Último recurso: primer resultado
            ticker = resultados[0].get("ticker")
            return str(ticker) if ticker is not None else None
    except Exception as e:
        print(f"[OpenFIGI] Error resolviendo ISIN {isin}: {e}")
        return None


async def _buscar_tickers_en_yahoo(isin: str) -> list[str]:
    """
    Busca todos los tickers candidatos en Yahoo Finance por ISIN.
    Devuelve lista ordenada por relevancia para iterar hasta encontrar uno válido.
    """
    try:
        async with httpx.AsyncClient(
            timeout=10, headers=_YAHOO_HEADERS, follow_redirects=True
        ) as client:
            r = await client.get(
                "https://query1.finance.yahoo.com/v1/finance/search",
                params={"q": isin, "lang": "en-US", "type": "quotes"},
            )
            r.raise_for_status()
            quotes = r.json().get("quotes", [])
            return [q["symbol"] for q in quotes if q.get("symbol")]
    except Exception as e:
        print(f"[Yahoo Search] Error buscando ISIN {isin}: {e}")
        return []


def _obtener_info_yfinance(ticker: str) -> dict | None:
    """
    Obtiene metadatos + precio via yfinance (síncrono).
    Se ejecuta en un executor para no bloquear el event loop.
    """
    try:
        t = yf.Ticker(ticker)
        info = t.info

        if (
            not info
            or info.get("trailingPegRatio") is None
            and not info.get("longName")
        ):
            # yfinance devuelve un dict vacío o inútil si el ticker no existe
            return None

        quote_type = info.get("quoteType", "")
        tipo = _tipo_desde_quote_type(quote_type)

        precio = (
            info.get("regularMarketPrice")
            or info.get("currentPrice")
            or info.get("previousClose")
        )

        return {
            "nombre": info.get("longName") or info.get("shortName"),
            "tipo": tipo,
            "sector": info.get("sector"),
            "pais": info.get("country"),
            "moneda": info.get("currency"),
            "exchange": info.get("exchange"),
            "precio": float(precio) if precio else None,
        }
    except Exception as e:
        print(f"[yfinance] Error obteniendo info de {ticker}: {e}")
        return None


def _tipo_desde_quote_type(quote_type: str) -> str:
    mapping = {
        "EQUITY": "accion",
        "ETF": "etf",
        "MUTUALFUND": "fondo",
    }
    return mapping.get((quote_type or "").upper(), "otro")


async def obtener_precios_batch(tickers: list[str]) -> dict[str, float]:
    """Obtiene precios de múltiples tickers en paralelo via yfinance."""
    if not tickers:
        return {}

    resultados = await asyncio.gather(
        *[_obtener_precio_actual(ticker) for ticker in tickers], return_exceptions=True
    )
    # Sino existe ticker en yahoo no vamos a encontrar precios
    return {
        ticker: precio
        for ticker, precio in zip(tickers, resultados)
        if isinstance(precio, float)
    }  # noqa: B905


async def _obtener_precio_actual(ticker: str) -> float | None:
    """Obtiene el precio actual de un ticker via yfinance."""
    if not ticker:
        return None
    loop = asyncio.get_event_loop()
    perfil = await loop.run_in_executor(None, _obtener_info_yfinance, ticker)
    return perfil.get("precio") if perfil else None


async def obtener_precio_by_date(ticker: str, fecha: date) -> float | None:
    """
    Devuelve el precio de cierre de un ticker en una fecha concreta,
    en la moneda nativa del instrumento. Devuelve None si yfinance no
    tiene datos (ticker inválido, fecha muy antigua, mercado cerrado sin
    margen suficiente...).
    """
    if not ticker:
        return None
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_precio_by_date, ticker, fecha)


def _fetch_precio_by_date(ticker: str, fecha: date) -> float | None:
    """
    Obtiene el precio de cierre para un ticker en una fecha concreta.
    Añade hasta 5 días de margen para cubrir fines de semana y festivos.
    """
    try:
        end = fecha + timedelta(days=5)
        hist = yf.Ticker(ticker).history(start=str(fecha), end=str(end))
        if hist.empty:
            return None
        return float(hist["Close"].iloc[0])
    except Exception as e:
        print(f"[Precio histórico] Error obteniendo {ticker} para {fecha}: {e}")
        return None


async def obtener_precios_by_date_batch(
    tickers: list[str], fecha: date
) -> dict[str, float]:
    """Obtiene precios de cierre de múltiples tickers en una fecha, en paralelo."""
    if not tickers:
        return {}

    resultados = await asyncio.gather(
        *[obtener_precio_by_date(t, fecha) for t in tickers], return_exceptions=True
    )
    return {
        ticker: precio
        for ticker, precio in zip(tickers, resultados)
        if isinstance(precio, float)
    }  # noqa: B905


# ── Series históricas mensuales ───────────────────────────────────────────────


class ProveedorPreciosError(RuntimeError):
    """
    El proveedor de precios (yfinance) ha fallado o no ha devuelto ningún dato
    utilizable. Distinto de "un ticker no tiene histórico": la capa de API lo
    traduce a un error del servicio externo (503), nunca a datos insuficientes.
    """


def _hoy() -> date:
    """Fecha actual, aislada para poder fijarla en los tests."""
    return date.today()


def fin_ultimo_mes_completo(hoy: date | None = None) -> date:
    """
    Último día del último mes ya cerrado. El mes en curso (incompleto) nunca
    cuenta, ni siquiera el día 1: hoy=2026-10-04 -> 2026-09-30.
    """
    return (hoy or _hoy()).replace(day=1) - timedelta(days=1)


async def obtener_precios_mensuales_batch(
    tickers: list[str], fecha_inicio: date, fecha_fin: date
) -> dict[str, list[tuple[date, float]]]:
    """
    Obtiene las series de cierres mensuales ajustados (dividendos y splits)
    de varios tickers con una única descarga de yfinance.

    Devuelve {ticker: [(primer_dia_del_mes, cierre), ...]} ordenado por fecha,
    en la moneda nativa de cada instrumento. Solo incluye meses cerrados (nunca
    el mes en curso) y no posteriores a `fecha_fin`. Los tickers sin datos se
    omiten; si la descarga falla o no trae datos de ningún ticker lanza
    ProveedorPreciosError.
    """
    tickers_unicos = list(dict.fromkeys(t for t in tickers if t))
    if not tickers_unicos:
        return {}
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _fetch_precios_mensuales, tickers_unicos, fecha_inicio, fecha_fin
    )


def _fetch_precios_mensuales(
    tickers: list[str], fecha_inicio: date, fecha_fin: date
) -> dict[str, list[tuple[date, float]]]:
    """
    Descarga (síncrona) los cierres mensuales ajustados de todos los tickers.

    Las fechas se normalizan al primer día del mes para poder alinear
    instrumentos de mercados distintos; si yfinance devuelve dos filas del
    mismo mes prevalece la más reciente. Los NaN se descartan, igual que las
    filas del mes en curso (incompleto) o posteriores a `fecha_fin`.

    yfinance a menudo no lanza excepción ante un fallo: lo registra y devuelve
    columnas vacías o todo NaN. Por eso, si ningún ticker trae ni un cierre se
    considera fallo del proveedor y no "sin datos".
    """
    try:
        df = yf.download(
            tickers,
            start=str(fecha_inicio),
            end=str(fecha_fin + timedelta(days=1)),
            interval="1mo",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        raise ProveedorPreciosError(
            f"Error descargando precios mensuales de {tickers}: {e}"
        ) from e

    if df is None or df.empty or "Close" not in df.columns.get_level_values(0):
        raise ProveedorPreciosError(
            f"La descarga de precios mensuales de {tickers} no devolvió datos"
        )

    cierres = df["Close"]
    if isinstance(cierres, pd.Series):
        # Un solo ticker con columnas planas: la Serie es directamente su cierre
        cierres = cierres.to_frame(name=tickers[0])

    # Primer día del mes en curso: a partir de él nada está cerrado todavía
    mes_en_curso = _hoy().replace(day=1)
    hay_datos = False
    resultado: dict[str, list[tuple[date, float]]] = {}
    for ticker in tickers:
        if ticker not in cierres.columns:
            continue
        por_mes: dict[date, float] = {}
        for fecha, valor in cierres[ticker].dropna().sort_index().items():
            hay_datos = True
            marca = pd.Timestamp(fecha)
            mes = date(marca.year, marca.month, 1)
            if mes < mes_en_curso and mes <= fecha_fin:
                por_mes[mes] = float(valor)
        if por_mes:
            resultado[ticker] = sorted(por_mes.items())

    if not hay_datos:
        raise ProveedorPreciosError(
            f"La descarga de precios mensuales de {tickers} no trajo ningún cierre"
        )
    return resultado


# ── FX rate helpers ───────────────────────────────────────────────────────────


async def obtener_fx_by_date(moneda: str, fecha: date) -> float | None:
    """
    Devuelve el tipo de cambio EUR/moneda en una fecha concreta.

    Convenio: EURUSD=X → cuántos USD por 1 EUR (ej. 1.085).
    Para convertir precio_nativo → EUR: precio_nativo / resultado.

    Devuelve 1.0 para EUR. Devuelve None si yfinance no tiene datos.
    """
    if moneda.upper() == "EUR":
        return 1.0
    ticker = f"EUR{moneda.upper()}=X"
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_fx_by_date, ticker, fecha)


def _fetch_fx_by_date(ticker: str, fecha: date) -> float | None:
    """
    Obtiene el tipo de cambio de cierre para un par FX en una fecha concreta.
    Añade hasta 5 días de margen para cubrir fines de semana y festivos.
    Devuelve None si yfinance no tiene datos para ese par/fecha.
    """
    try:
        end = fecha + timedelta(days=5)
        hist = yf.Ticker(ticker).history(start=str(fecha), end=str(end))
        if hist.empty:
            return None
        return float(hist["Close"].iloc[0])
    except Exception as e:
        print(f"[FX histórico] Error obteniendo {ticker} para {fecha}: {e}")
        return None


async def obtener_fx(moneda: str) -> float | None:
    """
    Devuelve el tipo de cambio EUR/moneda actual.

    Convenio: EURUSD=X → cuántos USD por 1 EUR (ej. 1.085).
    Para convertir precio_nativo → EUR: precio_nativo / resultado.

    Devuelve 1.0 para EUR. Devuelve None si yfinance no tiene datos.
    """
    if moneda.upper() == "EUR":
        return 1.0
    ticker = f"EUR{moneda.upper()}=X"
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_fx, ticker)


def _fetch_fx(ticker: str) -> float | None:
    """
    Obtiene el tipo de cambio actual para un par FX via yfinance.
    Devuelve None si no hay datos disponibles.
    """
    try:
        info = yf.Ticker(ticker).info
        price = info.get("regularMarketPrice") or info.get("previousClose")
        return float(price) if price else None
    except Exception as e:
        print(f"[FX actual] Error obteniendo {ticker}: {e}")
        return None


async def obtener_fx_batch(monedas: list[str]) -> dict[str, float]:
    """
    Obtiene tipos de cambio EUR/moneda actuales para una lista de monedas.
    Las monedas EUR se omiten (no necesitan conversión).
    Devuelve {moneda: fx} para las monedas con datos disponibles.

    Convenio: valores siguen EUR{MONEDA}=X → precio_nativo / fx = EUR.
    """
    monedas_unicas = {m.upper() for m in monedas if m and m.upper() != "EUR"}
    if not monedas_unicas:
        return {}

    resultados = await asyncio.gather(
        *[obtener_fx(m) for m in monedas_unicas],
        return_exceptions=True,
    )
    return {
        moneda: fx
        for moneda, fx in zip(monedas_unicas, resultados)
        if isinstance(fx, float)
    }


# ── Public aliases (backwards-compatible API) ─────────────────────────────────


async def buscar_ticker_por_isin(isin: str) -> str | None:
    """Public alias for _buscar_ticker_en_openfigi."""
    return await _buscar_ticker_en_openfigi(isin)


async def obtener_precio_actual(ticker: str) -> float | None:
    """Public alias for _obtener_precio_actual."""
    return await _obtener_precio_actual(ticker)
