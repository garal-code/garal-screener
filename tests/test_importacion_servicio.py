"""
Unit tests for the CSV import parsing and normalization service.

Fixtures are synthetic: they mimic the column layout of Trading212 and Trade
Republic exports plus a generic European-style CSV, with made-up values.
"""

from datetime import date

import pytest

from app.schemas import PlantillaConfig
from app.services.importacion import (
    MAX_FILAS,
    FilaNormalizada,
    FilaOmitida,
    ImportacionError,
    inspeccionar,
    leer_csv,
    normalizar_fila,
    validar_columnas,
)

T212_CSV = (
    "Action,Time (UTC),ISIN,Ticker,Name,ID,No. of shares,Price / share,"
    "Currency (Price / share),Exchange rate,Result,Currency (Result),Total,"
    "Currency (Total),Currency conversion fee,Currency (Currency conversion fee)\n"
    "Deposit,2025-01-02 10:00:00,,,,DEP001,,,,,,,500.00,EUR,,\n"
    "Market buy,2025-02-04 08:00:01,ES0000000001,AAA,Alpha,EOF0001,"
    "75.1234560000,13.4000000000,EUR,1.00000000,,,1006.65,EUR,,\n"
    "Market sell,2025-06-11 17:07:48,US0000000002,BBB,Beta,EOF0002,"
    "21.6543210000,15.2200000000,USD,1.08765432,9.99,EUR,302.97,EUR,0.33,EUR\n"
    "Dividend (Ordinary),2025-07-01 09:00:00,US0000000002,BBB,Beta,DIV0001,"
    "19.5,0.10,USD,1.15,,,1.70,EUR,,\n"
)

T212_CONFIG = PlantillaConfig.model_validate(
    {
        "delimitador": ",",
        "columnas": {
            "fecha": "Time (UTC)",
            "isin": "ISIN",
            "tipo": "Action",
            "cantidad": "No. of shares",
            "precio": "Price / share",
            "comision": ["Currency conversion fee"],
            "tipo_cambio": "Exchange rate",
            "moneda_precio": "Currency (Price / share)",
            "referencia": "ID",
        },
        "valores_compra": ["Market buy", "Limit buy"],
        "valores_venta": ["Market sell", "Limit sell"],
    }
)

TR_CSV = (
    "datetime,date,account_type,category,type,asset_class,name,symbol,shares,"
    "price,amount,fee,tax,currency,original_amount,original_currency,fx_rate,"
    "description,transaction_id\n"
    "2026-01-14T13:46:07.018Z,2026-01-14,DEFAULT,TRADING,BUY,STOCK,Gamma,"
    "JP0000000003,15.0000000000,31.2000000000,-468.00,-1.00,,EUR,,,,"
    "Buy trade,tr-0001\n"
    "2026-03-02T18:09:13.245Z,2026-03-02,DEFAULT,TRADING,SELL,FUND,Delta,"
    "IE0000000004,-64.3210980000,52.1100000000,3351.80,-1.00,,EUR,,,,"
    "Sell trade,tr-0002\n"
    "2026-03-03T08:00:00.000Z,2026-03-03,DEFAULT,TRADING,BUY,FUND,Delta,"
    "IE0000000004,3.1415920000,33.4200000000,-105.00,,,EUR,,,,"
    "Savings plan execution,tr-0003\n"
    "2026-04-01T00:00:00.000Z,2026-04-01,DEFAULT,CASH,INTEREST_PAYMENT,,,,,,"
    "9.52,,,EUR,,,,Interest payment,tr-0004\n"
)

TR_CONFIG = PlantillaConfig.model_validate(
    {
        "columnas": {
            "fecha": "datetime",
            "isin": "symbol",
            "tipo": "type",
            "cantidad": "shares",
            "precio": "price",
            "comision": ["fee", "tax"],
            "tipo_cambio": "fx_rate",
            "moneda_precio": "currency",
            "referencia": "transaction_id",
        },
        "valores_compra": ["BUY"],
        "valores_venta": ["SELL"],
    }
)

GENERICO_CSV = (
    "Fecha;Operación;ISIN;Títulos;Precio;Comisión;Gastos;Cambio\n"
    "04/02/2025;COMPRA;es0000000005;1.200;1.234,56;2,50;0,10;0,92\n"
    "05/02/2025;venta;ES0000000005;100;1.300,00;;;\n"
)

GENERICO_CONFIG = PlantillaConfig.model_validate(
    {
        "separador_decimal": ",",
        "formato_fecha": "%d/%m/%Y",
        "columnas": {
            "fecha": "Fecha",
            "isin": "ISIN",
            "tipo": "Operación",
            "cantidad": "Títulos",
            "precio": "Precio",
            "comision": ["Comisión", "Gastos"],
            "tipo_cambio": "Cambio",
        },
        "valores_compra": ["Compra"],
        "valores_venta": ["Venta"],
        "invertir_tipo_cambio": True,
    }
)


def normalizar_todo(contenido, config):
    tabla = leer_csv(contenido, config.delimitador)
    validar_columnas(tabla, config)
    return [normalizar_fila(fila, config) for fila in tabla.filas]


# ── leer_csv ─────────────────────────────────────────────────────────────────


class TestLeerCsv:
    def test_detecta_delimitador_y_cabeceras(self):
        tabla = leer_csv(GENERICO_CSV, None)
        assert tabla.delimitador == ";"
        assert tabla.columnas[:3] == ["Fecha", "Operación", "ISIN"]
        assert len(tabla.filas) == 2

    def test_numera_lineas_del_archivo(self):
        tabla = leer_csv(T212_CSV, ",")
        assert [f.linea for f in tabla.filas] == [2, 3, 4, 5]

    def test_quita_bom_y_salta_lineas_vacias(self):
        tabla = leer_csv("\ufeffa,b\n1,2\n\n , \n3,4\n", None)
        assert tabla.columnas == ["a", "b"]
        assert [f.valores for f in tabla.filas] == [
            {"a": "1", "b": "2"},
            {"a": "3", "b": "4"},
        ]

    def test_filas_cortas_se_completan_con_vacio(self):
        tabla = leer_csv("a,b,c\n1,2\n", ",")
        assert tabla.filas[0].valores == {"a": "1", "b": "2", "c": ""}

    def test_vacio_lanza_error(self):
        with pytest.raises(ImportacionError, match="^El CSV está vacío$"):
            leer_csv("  \n\n", None)

    def test_sin_filas_de_datos_lanza_error(self):
        with pytest.raises(ImportacionError, match="^El CSV no tiene filas de datos$"):
            leer_csv("a,b\n", None)

    def test_cabeceras_repetidas_lanza_error(self):
        with pytest.raises(
            ImportacionError, match="^El CSV tiene columnas repetidas: a$"
        ):
            leer_csv("a,b,a\n1,2,3\n", ",")

    def test_cabecera_vacia_lanza_error(self):
        with pytest.raises(
            ImportacionError, match="^El CSV tiene columnas sin nombre en la cabecera$"
        ):
            leer_csv("a,,c\n1,2,3\n", ",")

    def test_maximo_de_filas(self):
        justo = "a\n" + "1\n" * MAX_FILAS
        assert len(leer_csv(justo, ",").filas) == MAX_FILAS
        with pytest.raises(
            ImportacionError, match=f"^El CSV supera el máximo de {MAX_FILAS} filas$"
        ):
            leer_csv(justo + "1\n", ",")

    def test_columna_de_plantilla_ausente_lanza_error(self):
        tabla = leer_csv(TR_CSV.replace("transaction_id", "id"), None)
        with pytest.raises(
            ImportacionError,
            match="^La columna 'transaction_id' de la plantilla no existe en el CSV$",
        ):
            validar_columnas(tabla, TR_CONFIG)


# ── inspeccionar ─────────────────────────────────────────────────────────────


class TestInspeccionar:
    def test_devuelve_cabeceras_muestra_y_valores_distintos(self):
        resultado = inspeccionar(T212_CSV, None)
        assert resultado["delimitador"] == ","
        assert resultado["columnas"][0] == "Action"
        assert len(resultado["filas_muestra"]) == 4
        assert resultado["filas_muestra"][1][0] == "Market buy"
        assert resultado["valores_distintos"]["Action"] == [
            "Deposit",
            "Dividend (Ordinary)",
            "Market buy",
            "Market sell",
        ]

    def test_muestra_limitada_a_cinco_filas(self):
        contenido = "a\n" + "".join(f"{i}\n" for i in range(10))
        assert len(inspeccionar(contenido, None)["filas_muestra"]) == 5

    def test_omite_columnas_con_muchos_valores(self):
        contenido = "tipo,id\n" + "".join(f"BUY,{i}\n" for i in range(30))
        distintos = inspeccionar(contenido, None)["valores_distintos"]
        assert distintos == {"tipo": ["BUY"]}


# ── normalizar_fila ──────────────────────────────────────────────────────────


class TestNormalizarTrading212:
    def test_filas(self):
        deposito, compra, venta, dividendo = normalizar_todo(T212_CSV, T212_CONFIG)

        assert deposito == FilaOmitida(
            linea=2, motivo='Tipo "Deposit" no es compra ni venta'
        )
        assert dividendo == FilaOmitida(
            linea=5, motivo='Tipo "Dividend (Ordinary)" no es compra ni venta'
        )
        assert compra == FilaNormalizada(
            linea=3,
            tipo="compra",
            fecha=date(2025, 2, 4),
            isin="ES0000000001",
            cantidad=75.123456,
            precio=13.4,
            comision=0.0,
            tipo_cambio=1.0,
            referencia="EOF0001",
        )
        assert venta == FilaNormalizada(
            linea=4,
            tipo="venta",
            fecha=date(2025, 6, 11),
            isin="US0000000002",
            cantidad=21.654321,
            precio=15.22,
            comision=0.33,
            tipo_cambio=1.08765432,
            referencia="EOF0002",
        )


class TestNormalizarTradeRepublic:
    def test_filas(self):
        compra, venta, plan, interes = normalizar_todo(TR_CSV, TR_CONFIG)

        assert compra == FilaNormalizada(
            linea=2,
            tipo="compra",
            fecha=date(2026, 1, 14),
            isin="JP0000000003",
            cantidad=15.0,
            precio=31.2,
            comision=1.0,
            tipo_cambio=1.0,
            referencia="tr-0001",
        )
        # Negative shares and fee on sells are taken as absolute values
        assert venta.tipo == "venta"
        assert venta.cantidad == 64.321098
        assert venta.comision == 1.0
        # Empty fee and tax columns mean no commission
        assert plan.comision == 0.0
        assert plan.fecha == date(2026, 3, 3)
        assert interes == FilaOmitida(
            linea=5, motivo='Tipo "INTEREST_PAYMENT" no es compra ni venta'
        )


class TestNormalizarGenerico:
    def test_formato_europeo_e_inversion_del_cambio(self):
        compra, venta = normalizar_todo(GENERICO_CSV, GENERICO_CONFIG)

        assert compra == FilaNormalizada(
            linea=2,
            tipo="compra",
            fecha=date(2025, 2, 4),
            isin="ES0000000005",
            cantidad=1200.0,
            precio=1234.56,
            comision=2.6,
            tipo_cambio=pytest.approx(1 / 0.92),
            referencia=None,
        )
        assert venta.tipo == "venta"
        assert venta.precio == 1300.0
        assert venta.comision == 0.0
        assert venta.tipo_cambio is None
        assert venta.referencia is None


class TestMotivosDeOmision:
    CABECERA = "Fecha;Operación;ISIN;Títulos;Precio;Comisión;Gastos;Cambio\n"

    @pytest.mark.parametrize(
        ("fila", "motivo"),
        [
            (";Compra;ES0000000005;1;1;;;", 'Fecha "" no válida'),
            ("31/02/2025;Compra;ES0000000005;1;1;;;", 'Fecha "31/02/2025" no válida'),
            ("2025-02-04;Compra;ES0000000005;1;1;;;", 'Fecha "2025-02-04" no válida'),
            ("04/02/2025;;ES0000000005;1;1;;;", "Tipo vacío, no es compra ni venta"),
            ("04/02/2025;Traspaso;;1;1;;;", 'Tipo "Traspaso" no es compra ni venta'),
            ("04/02/2025;Compra;;1;1;;;", "Sin ISIN"),
            ("04/02/2025;Compra;  ;1;1;;;", "Sin ISIN"),
            ("04/02/2025;Compra;ES123;1;1;;;", 'ISIN "ES123" no válido'),
            ("04/02/2025;Compra;ES0000000005;0;1;;;", 'Cantidad "0" no válida'),
            ("04/02/2025;Compra;ES0000000005;;1;;;", 'Cantidad "" no válida'),
            ("04/02/2025;Compra;ES0000000005;1;abc;;;", 'Precio "abc" no válido'),
            ("04/02/2025;Compra;ES0000000005;1;0,00;;;", 'Precio "0,00" no válido'),
            # A dot-decimal value in a comma-decimal template is ambiguous: rejected
            ("04/02/2025;Compra;ES0000000005;1;12.5;;;", 'Precio "12.5" no válido'),
            ("04/02/2025;Compra;ES0000000005;1;1;x;;", 'Comisión "x" no válida'),
            ("04/02/2025;Compra;ES0000000005;1;1;;;0", 'Tipo de cambio "0" no válido'),
            (
                "04/02/2025;Compra;ES0000000005;1;1;;;nan",
                'Tipo de cambio "nan" no válido',
            ),
        ],
    )
    def test_motivo(self, fila, motivo):
        (resultado,) = normalizar_todo(self.CABECERA + fila + "\n", GENERICO_CONFIG)
        assert resultado == FilaOmitida(linea=2, motivo=motivo)

    def test_separador_decimal_punto_rechaza_formato_europeo(self):
        config = T212_CONFIG.model_copy(update={"delimitador": ";"})
        contenido = (
            "Action;Time (UTC);ISIN;ID;No. of shares;Price / share;"
            "Currency (Price / share);Exchange rate;Currency conversion fee\n"
            "Market buy;2025-02-04;ES0000000001;X1;1;1.234,56;EUR;1;\n"
        )
        (resultado,) = normalizar_todo(contenido, config)
        assert resultado == FilaOmitida(linea=2, motivo='Precio "1.234,56" no válido')

    def test_isin_se_normaliza_a_mayusculas(self):
        compra, _ = normalizar_todo(GENERICO_CSV, GENERICO_CONFIG)
        assert compra.isin == "ES0000000005"

    def test_moneda_no_eur_conserva_el_tipo_de_cambio_de_la_columna(self):
        _, _, venta, _ = normalizar_todo(T212_CSV, T212_CONFIG)
        assert venta.tipo_cambio == 1.08765432

    def test_moneda_no_eur_sin_tipo_de_cambio_queda_vacio(self):
        contenido = T212_CSV.replace("USD,1.08765432,", "USD,,")
        _, _, venta, _ = normalizar_todo(contenido, T212_CONFIG)
        assert venta.tipo_cambio is None
