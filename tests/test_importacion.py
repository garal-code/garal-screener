"""
Integration tests for CSV movement import (issue #36): import templates,
CSV inspection, preview and confirmation.

All CSV fixtures are synthetic; they only mimic the shape of real broker
exports (Trading212, Trade Republic) and contain no personal data.
"""

import copy
import hashlib
import json
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models
from app.services import importacion
from tests.test_api import mock_precios

BASE = "/api/v1"
PLANTILLAS = f"{BASE}/plantillas-importacion"

CONFIG_T212 = {
    "delimitador": ",",
    "separador_decimal": ".",
    "formato_fecha": None,
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
    "invertir_tipo_cambio": False,
}


def config_t212(**overrides):
    config = copy.deepcopy(CONFIG_T212)
    config.update(overrides)
    return config


def crear_plantilla(client, nombre="Trading212", config=None):
    return client.post(
        PLANTILLAS,
        json={"nombre": nombre, "config": config or config_t212()},
    )


# ── Import templates ─────────────────────────────────────────────────────────


class TestPlantillasImportacion:
    def test_crear_plantilla(self, auth_client):
        r = crear_plantilla(auth_client)
        assert r.status_code == 200
        data = r.json()
        assert data["nombre"] == "Trading212"
        assert data["config"]["columnas"]["isin"] == "ISIN"
        assert data["config"]["columnas"]["comision"] == ["Currency conversion fee"]
        assert data["config"]["valores_venta"] == ["Market sell", "Limit sell"]
        assert isinstance(data["id"], int)

    def test_config_minima_aplica_valores_por_defecto(self, auth_client):
        config = {
            "columnas": {
                "fecha": "date",
                "isin": "symbol",
                "tipo": "type",
                "cantidad": "shares",
                "precio": "price",
            },
            "valores_compra": ["BUY"],
            "valores_venta": ["SELL"],
        }
        r = crear_plantilla(auth_client, nombre="Trade Republic", config=config)
        assert r.status_code == 200
        guardada = r.json()["config"]
        assert guardada["delimitador"] is None
        assert guardada["separador_decimal"] == "."
        assert guardada["formato_fecha"] is None
        assert guardada["invertir_tipo_cambio"] is False
        assert guardada["columnas"]["comision"] == []
        assert guardada["columnas"]["tipo_cambio"] is None
        assert guardada["columnas"]["moneda_precio"] is None
        assert guardada["columnas"]["referencia"] is None

    def test_listar_plantillas_propias(self, auth_client):
        crear_plantilla(auth_client, nombre="B")
        crear_plantilla(auth_client, nombre="A")
        r = auth_client.get(PLANTILLAS)
        assert r.status_code == 200
        assert [p["nombre"] for p in r.json()] == ["A", "B"]

    def test_nombre_duplicado_retorna_409_y_no_guarda(self, auth_client):
        crear_plantilla(auth_client)
        r = crear_plantilla(auth_client)
        assert r.status_code == 409
        assert r.json()["detail"] == "Ya existe una plantilla con ese nombre"
        assert len(auth_client.get(PLANTILLAS).json()) == 1

    def test_nombre_vacio_retorna_422(self, auth_client):
        r = crear_plantilla(auth_client, nombre="   ")
        assert r.status_code == 422
        assert auth_client.get(PLANTILLAS).json() == []

    def test_columna_obligatoria_ausente_retorna_422(self, auth_client):
        config = config_t212()
        del config["columnas"]["isin"]
        r = crear_plantilla(auth_client, config=config)
        assert r.status_code == 422
        assert auth_client.get(PLANTILLAS).json() == []

    def test_columna_obligatoria_vacia_retorna_422(self, auth_client):
        config = config_t212()
        config["columnas"]["precio"] = "  "
        r = crear_plantilla(auth_client, config=config)
        assert r.status_code == 422
        assert auth_client.get(PLANTILLAS).json() == []

    def test_solo_valores_de_venta_se_guarda(self, auth_client):
        r = crear_plantilla(auth_client, config=config_t212(valores_compra=[]))
        assert r.status_code == 200
        assert r.json()["config"]["valores_compra"] == []
        assert r.json()["config"]["valores_venta"] == ["Market sell", "Limit sell"]

    def test_solo_valores_de_compra_se_guarda(self, auth_client):
        config = config_t212()
        del config["valores_venta"]
        r = crear_plantilla(auth_client, config=config)
        assert r.status_code == 200
        assert r.json()["config"]["valores_compra"] == ["Market buy", "Limit buy"]
        assert r.json()["config"]["valores_venta"] == []

    @pytest.mark.parametrize(
        "overrides",
        [{"valores_compra": [], "valores_venta": []}, {}],
        ids=["listas_vacias", "sin_listas"],
    )
    def test_sin_valores_de_compra_ni_de_venta_retorna_422(
        self, auth_client, overrides
    ):
        config = config_t212()
        del config["valores_compra"], config["valores_venta"]
        config.update(overrides)
        r = crear_plantilla(auth_client, config=config)
        assert r.status_code == 422
        assert "al menos un valor de compra o de venta" in r.text
        assert auth_client.get(PLANTILLAS).json() == []

    def test_valor_de_compra_vacio_retorna_422(self, auth_client):
        r = crear_plantilla(auth_client, config=config_t212(valores_compra=["  "]))
        assert r.status_code == 422
        assert auth_client.get(PLANTILLAS).json() == []

    def test_valor_en_compra_y_venta_retorna_422(self, auth_client):
        config = config_t212(valores_compra=["BUY"], valores_venta=["buy"])
        r = crear_plantilla(auth_client, config=config)
        assert r.status_code == 422
        assert "compra y venta" in r.text
        assert auth_client.get(PLANTILLAS).json() == []

    def test_separador_decimal_invalido_retorna_422(self, auth_client):
        r = crear_plantilla(auth_client, config=config_t212(separador_decimal=";"))
        assert r.status_code == 422
        assert auth_client.get(PLANTILLAS).json() == []

    def test_delimitador_invalido_retorna_422(self, auth_client):
        r = crear_plantilla(auth_client, config=config_t212(delimitador="."))
        assert r.status_code == 422
        assert auth_client.get(PLANTILLAS).json() == []

    def test_delimitador_igual_a_separador_decimal_retorna_422(self, auth_client):
        config = config_t212(delimitador=",", separador_decimal=",")
        r = crear_plantilla(auth_client, config=config)
        assert r.status_code == 422
        assert auth_client.get(PLANTILLAS).json() == []

    def test_formato_fecha_sin_directivas_retorna_422(self, auth_client):
        r = crear_plantilla(auth_client, config=config_t212(formato_fecha="dd/mm/yyyy"))
        assert r.status_code == 422
        assert auth_client.get(PLANTILLAS).json() == []

    def test_formato_fecha_explicito_se_guarda(self, auth_client):
        r = crear_plantilla(auth_client, config=config_t212(formato_fecha="%d/%m/%Y"))
        assert r.status_code == 200
        assert r.json()["config"]["formato_fecha"] == "%d/%m/%Y"

    def test_actualizar_plantilla(self, auth_client):
        plantilla_id = crear_plantilla(auth_client).json()["id"]
        r = auth_client.put(
            f"{PLANTILLAS}/{plantilla_id}",
            json={
                "nombre": "T212 nueva",
                "config": config_t212(separador_decimal=",", delimitador=";"),
            },
        )
        assert r.status_code == 200
        assert r.json()["nombre"] == "T212 nueva"
        listado = auth_client.get(PLANTILLAS).json()
        assert len(listado) == 1
        assert listado[0]["nombre"] == "T212 nueva"
        assert listado[0]["config"]["separador_decimal"] == ","

    def test_actualizar_a_nombre_existente_retorna_409_sin_cambios(self, auth_client):
        crear_plantilla(auth_client, nombre="A")
        b_id = crear_plantilla(auth_client, nombre="B").json()["id"]
        r = auth_client.put(
            f"{PLANTILLAS}/{b_id}", json={"nombre": "A", "config": config_t212()}
        )
        assert r.status_code == 409
        assert r.json()["detail"] == "Ya existe una plantilla con ese nombre"
        assert [p["nombre"] for p in auth_client.get(PLANTILLAS).json()] == ["A", "B"]

    def test_actualizar_inexistente_retorna_404(self, auth_client):
        r = auth_client.put(
            f"{PLANTILLAS}/9999", json={"nombre": "X", "config": config_t212()}
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "Plantilla no encontrada"

    def test_eliminar_plantilla(self, auth_client):
        plantilla_id = crear_plantilla(auth_client).json()["id"]
        r = auth_client.delete(f"{PLANTILLAS}/{plantilla_id}")
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        assert auth_client.get(PLANTILLAS).json() == []

    def test_eliminar_inexistente_retorna_404(self, auth_client):
        r = auth_client.delete(f"{PLANTILLAS}/9999")
        assert r.status_code == 404
        assert r.json()["detail"] == "Plantilla no encontrada"

    def test_sin_sesion_retorna_401(self, client):
        assert client.get(PLANTILLAS).status_code == 401
        assert crear_plantilla(client).status_code == 401

    def test_plantilla_ajena_no_es_visible_ni_modificable(
        self, auth_client, second_auth_client
    ):
        ajena_id = crear_plantilla(second_auth_client).json()["id"]

        assert auth_client.get(PLANTILLAS).json() == []
        r_put = auth_client.put(
            f"{PLANTILLAS}/{ajena_id}", json={"nombre": "X", "config": config_t212()}
        )
        assert r_put.status_code == 404
        assert auth_client.delete(f"{PLANTILLAS}/{ajena_id}").status_code == 404

        propias = second_auth_client.get(PLANTILLAS).json()
        assert [p["nombre"] for p in propias] == ["Trading212"]

    def test_mismo_nombre_en_usuarios_distintos(self, auth_client, second_auth_client):
        assert crear_plantilla(auth_client).status_code == 200
        assert crear_plantilla(second_auth_client).status_code == 200


# ── Import: inspect, preview, confirm ────────────────────────────────────────

ES = "ES0000000001"
US = "US0000000002"
LIMITE_CONTENIDO = 2_000_000
CABECERA = (
    "Action,Time (UTC),ISIN,ID,No. of shares,Price / share,"
    "Currency (Price / share),Exchange rate,Currency conversion fee"
)

DATOS_ISIN = {
    ES: {
        "ticker": "AAA",
        "nombre": "Alpha",
        "tipo": "accion",
        "sector": "Tecnología",
        "pais": "España",
        "moneda": "EUR",
        "exchange": "BME",
    },
    US: {
        "ticker": "BBB",
        "nombre": "Beta",
        "tipo": "etf",
        "sector": "Salud",
        "pais": "Estados Unidos",
        "moneda": "USD",
        "exchange": "NASDAQ",
    },
}


def csv_t212(*filas):
    return "\n".join([CABECERA, *filas]) + "\n"


def fila_csv(
    accion, fecha, isin, ref, cantidad, precio, moneda="EUR", tc="1.0", fee=""
):
    return f"{accion},{fecha},{isin},{ref},{cantidad},{precio},{moneda},{tc},{fee}"


# Lines 2..7 of the file: 3 rows are imported, 3 are skipped by normalization
CSV_MIXTO = csv_t212(
    fila_csv("Market buy", "2025-02-04 08:00:01", ES, "B1", "10", "11.5"),
    fila_csv("Market sell", "2025-03-10 09:00:00", ES, "S1", "4", "12", fee="0.5"),
    "Deposit,2025-01-02 10:00:00,,D1,,,,,",
    fila_csv("Market buy", "2025-04-01 10:00:00", "", "B2", "5", "10"),
    fila_csv("Market buy", "2025-04-02 10:00:00", US, "B3", "abc", "10", "USD", "1.1"),
    fila_csv(
        "Limit buy", "2025-05-02 10:00:00", US, "B4", "2.5", "100", "USD", "1.25", "0.1"
    ),
)


@pytest.fixture
def enriquecer():
    """Mocks the ISIN enrichment used by the import router."""
    mock = AsyncMock(side_effect=lambda isin: DATOS_ISIN.get(isin))
    with patch.multiple("app.routers.importaciones.precios", enriquecer_por_isin=mock):
        yield mock


def cartera_id(client):
    return client.get(f"{BASE}/carteras").json()[0]["id"]


def inspeccionar(client, contenido, delimitador=None):
    return client.post(
        f"{BASE}/importaciones/inspeccionar",
        json={"contenido": contenido, "delimitador": delimitador},
    )


def previsualizar(client, cid, contenido, plantilla_id):
    return client.post(
        f"{BASE}/carteras/{cid}/importaciones/previsualizar",
        json={"contenido": contenido, "plantilla_id": plantilla_id},
    )


def confirmar(client, cid, contenido, plantilla_id, huella):
    return client.post(
        f"{BASE}/carteras/{cid}/importaciones",
        json={"contenido": contenido, "plantilla_id": plantilla_id, "huella": huella},
    )


def escenario(client, config=None):
    """Template + cartera of the logged user: (cartera_id, plantilla_id)."""
    plantilla_id = crear_plantilla(client, config=config).json()["id"]
    return cartera_id(client), plantilla_id


def movimientos(client, cid):
    return client.get(f"{BASE}/carteras/{cid}/movimientos").json()


def instrumentos(client):
    return client.get(f"{BASE}/instrumentos").json()


def assert_sin_escrituras(client, cid):
    assert movimientos(client, cid) == []
    assert instrumentos(client) == []


def fila(linea, estado, motivo, tipo, fecha, isin, nombre, cant, precio, com, tc, ref):
    return {
        "linea": linea,
        "estado": estado,
        "motivo": motivo,
        "tipo": tipo,
        "fecha": fecha,
        "isin": isin,
        "nombre": nombre,
        "cantidad": cant,
        "precio": precio,
        "comision": com,
        "tipo_cambio": tc,
        "referencia": ref,
    }


def fila_omitida_en_lectura(linea, motivo):
    return fila(linea, "omitir", motivo, *[None] * 9)


def motivos(respuesta):
    return {f["linea"]: f["motivo"] for f in respuesta.json()["filas"]}


def estados(respuesta):
    return {f["linea"]: f["estado"] for f in respuesta.json()["filas"]}


class TestInspeccionar:
    def test_devuelve_cabeceras_muestra_y_valores_distintos(self, auth_client):
        r = inspeccionar(auth_client, CSV_MIXTO)
        assert r.status_code == 200
        data = r.json()
        assert data["delimitador"] == ","
        assert data["columnas"] == CABECERA.split(",")
        assert len(data["filas_muestra"]) == 5
        assert data["filas_muestra"][0] == [
            "Market buy",
            "2025-02-04 08:00:01",
            ES,
            "B1",
            "10",
            "11.5",
            "EUR",
            "1.0",
            "",
        ]
        assert data["valores_distintos"]["Action"] == [
            "Deposit",
            "Limit buy",
            "Market buy",
            "Market sell",
        ]

    def test_respeta_el_delimitador_indicado(self, auth_client):
        r = inspeccionar(auth_client, "a;b\n1;2\n", ";")
        assert r.status_code == 200
        assert r.json()["delimitador"] == ";"
        assert r.json()["columnas"] == ["a", "b"]

    def test_csv_vacio_retorna_400(self, auth_client):
        r = inspeccionar(auth_client, "  \n")
        assert r.status_code == 400
        assert r.json() == {"detail": "El CSV está vacío"}

    def test_contenido_demasiado_grande_retorna_422(self, auth_client):
        assert (
            inspeccionar(auth_client, "a" * (LIMITE_CONTENIDO + 1)).status_code == 422
        )

    def test_delimitador_no_permitido_retorna_422(self, auth_client):
        assert inspeccionar(auth_client, "a,b\n1,2\n", "x").status_code == 422

    def test_sin_sesion_retorna_401(self, client):
        assert inspeccionar(client, CSV_MIXTO).status_code == 401


class TestPrevisualizar:
    def test_archivo_mixto(self, auth_client, enriquecer):
        cid, pid = escenario(auth_client)
        r = previsualizar(auth_client, cid, CSV_MIXTO, pid)

        assert r.status_code == 200
        data = r.json()
        assert data["resumen"] == {"total": 6, "importar": 3, "omitir": 3}
        assert data["filas"] == [
            fila(
                2,
                "importar",
                None,
                "compra",
                "2025-02-04",
                ES,
                "Alpha",
                10.0,
                11.5,
                0.0,
                1.0,
                "B1",
            ),
            fila(
                3,
                "importar",
                None,
                "venta",
                "2025-03-10",
                ES,
                "Alpha",
                4.0,
                12.0,
                0.5,
                1.0,
                "S1",
            ),
            fila_omitida_en_lectura(4, 'Tipo "Deposit" no es compra ni venta'),
            fila_omitida_en_lectura(5, "Sin ISIN"),
            fila_omitida_en_lectura(6, 'Cantidad "abc" no válida'),
            fila(
                7,
                "importar",
                None,
                "compra",
                "2025-05-02",
                US,
                "Beta",
                2.5,
                100.0,
                0.1,
                1.25,
                "B4",
            ),
        ]
        assert_sin_escrituras(auth_client, cid)

    def test_huella_es_sha256_del_json_canonico_de_las_filas(
        self, auth_client, enriquecer
    ):
        cid, pid = escenario(auth_client)
        data = previsualizar(auth_client, cid, CSV_MIXTO, pid).json()
        canonico = json.dumps(data["filas"], sort_keys=True, separators=(",", ":"))
        assert data["huella"] == hashlib.sha256(canonico.encode()).hexdigest()

    def test_huella_estable_y_sensible_a_los_datos(self, auth_client, enriquecer):
        cid, pid = escenario(auth_client)
        h1 = previsualizar(auth_client, cid, CSV_MIXTO, pid).json()["huella"]
        h2 = previsualizar(auth_client, cid, CSV_MIXTO, pid).json()["huella"]
        otro = CSV_MIXTO.replace(",10,11.5,", ",11,11.5,")
        h3 = previsualizar(auth_client, cid, otro, pid).json()["huella"]
        assert h1 == h2
        assert h3 != h1

    def test_enriquece_una_vez_por_isin_desconocido(self, auth_client, enriquecer):
        cid, pid = escenario(auth_client)
        previsualizar(auth_client, cid, CSV_MIXTO, pid)
        assert sorted(c.args[0] for c in enriquecer.call_args_list) == [ES, US]
        assert_sin_escrituras(auth_client, cid)

    def test_isin_sin_datos_o_con_error_omite_la_fila(self, auth_client, enriquecer):
        async def falla(isin):
            if isin == ES:
                return None
            raise RuntimeError("proveedor caído")

        enriquecer.side_effect = falla
        cid, pid = escenario(auth_client)
        contenido = csv_t212(
            fila_csv("Market buy", "2025-02-01", ES, "B1", "1", "10"),
            fila_csv("Market buy", "2025-02-02", ES, "B2", "2", "10"),
            fila_csv("Market buy", "2025-02-03", US, "B3", "3", "10", "USD"),
        )
        r = previsualizar(auth_client, cid, contenido, pid)

        assert r.status_code == 200
        assert r.json()["resumen"] == {"total": 3, "importar": 0, "omitir": 3}
        assert motivos(r) == {
            2: f"No se encontraron datos para el ISIN {ES}",
            3: f"No se encontraron datos para el ISIN {ES}",
            4: f"No se encontraron datos para el ISIN {US}",
        }
        # skipped later: normalized fields are kept, the name is unknown
        assert r.json()["filas"][0] == fila(
            2, "omitir", f"No se encontraron datos para el ISIN {ES}", "compra",
            "2025-02-01", ES, None, 1.0, 10.0, 0.0, 1.0, "B1",
        )  # fmt: skip
        assert sorted(c.args[0] for c in enriquecer.call_args_list) == [ES, US]
        assert_sin_escrituras(auth_client, cid)

    def test_venta_cubierta_por_compra_anterior_del_mismo_archivo(
        self, auth_client, enriquecer
    ):
        cid, pid = escenario(auth_client)
        contenido = csv_t212(
            # file order differs from date order: the sale is the first line
            fila_csv("Market sell", "2025-03-01", US, "S1", "3", "12", "USD"),
            fila_csv("Market buy", "2025-02-01", US, "B1", "3", "10", "USD"),
        )
        r = previsualizar(auth_client, cid, contenido, pid)
        assert estados(r) == {2: "importar", 3: "importar"}
        assert r.json()["resumen"] == {"total": 2, "importar": 2, "omitir": 0}

    def test_venta_anterior_a_su_compra_supera_la_posicion(
        self, auth_client, enriquecer
    ):
        cid, pid = escenario(auth_client)
        contenido = csv_t212(
            fila_csv("Market buy", "2025-03-01", US, "B1", "3", "10", "USD"),
            fila_csv("Market sell", "2025-02-01", US, "S1", "3", "12", "USD"),
        )
        r = previsualizar(auth_client, cid, contenido, pid)
        assert estados(r) == {2: "importar", 3: "omitir"}
        assert motivos(r)[3] == "Venta de 3 supera la posición disponible (0)"

    def test_venta_mayor_que_la_posicion_no_reduce_las_siguientes(
        self, auth_client, enriquecer
    ):
        cid, pid = escenario(auth_client)
        contenido = csv_t212(
            fila_csv("Market buy", "2025-02-01", US, "B1", "3", "10", "USD"),
            fila_csv("Market sell", "2025-03-01", US, "S1", "5", "12", "USD"),
            fila_csv("Market sell", "2025-03-02", US, "S2", "3", "12", "USD"),
            fila_csv("Market sell", "2025-03-03", US, "S3", "1", "12", "USD"),
        )
        r = previsualizar(auth_client, cid, contenido, pid)
        assert estados(r) == {2: "importar", 3: "omitir", 4: "importar", 5: "omitir"}
        assert motivos(r)[3] == "Venta de 5 supera la posición disponible (3)"
        assert motivos(r)[5] == "Venta de 1 supera la posición disponible (0)"

    def test_cantidades_del_motivo_sin_ceros_finales(self, auth_client, enriquecer):
        cid, pid = escenario(auth_client)
        contenido = csv_t212(
            fila_csv("Market buy", "2025-02-01", US, "B1", "19.5", "10", "USD"),
            fila_csv("Market sell", "2025-03-01", US, "S1", "21.654321", "12", "USD"),
        )
        r = previsualizar(auth_client, cid, contenido, pid)
        assert (
            motivos(r)[3] == "Venta de 21.654321 supera la posición disponible (19.5)"
        )

    def test_tolerancia_de_1e9_en_la_venta(self, auth_client, enriquecer):
        cid, pid = escenario(auth_client)
        contenido = csv_t212(
            fila_csv("Market buy", "2025-02-01", US, "B1", "0.3", "10", "USD"),
            fila_csv(
                "Market sell", "2025-03-01", US, "S1", "0.3000000001", "12", "USD"
            ),
        )
        assert estados(previsualizar(auth_client, cid, contenido, pid))[3] == "importar"

    def test_id_duplicado_en_el_archivo(self, auth_client, enriquecer):
        cid, pid = escenario(auth_client)
        contenido = csv_t212(
            fila_csv("Market buy", "2025-02-01", ES, "B1", "1", "10"),
            fila_csv("Market buy", "2025-02-02", ES, "B1", "2", "10"),
            # D7: the later line is the duplicate even when its date is earlier
            fila_csv("Market buy", "2025-01-03", ES, "B2", "3", "10"),
            fila_csv("Market buy", "2025-01-02", ES, "B2", "4", "10"),
        )
        r = previsualizar(auth_client, cid, contenido, pid)
        assert motivos(r) == {
            2: None,
            3: "Duplicada en el archivo (ID B1)",
            4: None,
            5: "Duplicada en el archivo (ID B2)",
        }
        assert r.json()["resumen"] == {"total": 4, "importar": 2, "omitir": 2}

    def test_plantilla_sin_columna_referencia_no_deduplica(
        self, auth_client, enriquecer
    ):
        config = config_t212()
        config["columnas"]["referencia"] = None
        cid, pid = escenario(auth_client, config)
        contenido = csv_t212(
            fila_csv("Market buy", "2025-02-01", ES, "B1", "1", "10"),
            fila_csv("Market buy", "2025-02-01", ES, "B1", "1", "10"),
        )
        r = previsualizar(auth_client, cid, contenido, pid)
        assert estados(r) == {2: "importar", 3: "importar"}

    def test_columna_de_la_plantilla_ausente_retorna_400(self, auth_client, enriquecer):
        config = config_t212()
        config["columnas"]["precio"] = "Precio"
        cid, pid = escenario(auth_client, config)
        r = previsualizar(auth_client, cid, CSV_MIXTO, pid)
        assert r.status_code == 400
        assert r.json() == {
            "detail": "La columna 'Precio' de la plantilla no existe en el CSV"
        }
        assert_sin_escrituras(auth_client, cid)

    def test_csv_vacio_retorna_400(self, auth_client, enriquecer):
        cid, pid = escenario(auth_client)
        r = previsualizar(auth_client, cid, "", pid)
        assert r.status_code == 400
        assert r.json() == {"detail": "El CSV está vacío"}

    def test_cartera_ajena_o_inexistente_retorna_404(
        self, auth_client, second_auth_client, enriquecer
    ):
        _, pid = escenario(auth_client)
        ajena = cartera_id(second_auth_client)
        for cid in (ajena, 9999):
            r = previsualizar(auth_client, cid, CSV_MIXTO, pid)
            assert r.status_code == 404
            assert r.json() == {"detail": "Cartera no encontrada"}
        assert enriquecer.call_count == 0
        assert_sin_escrituras(second_auth_client, ajena)

    def test_plantilla_ajena_o_inexistente_retorna_404(
        self, auth_client, second_auth_client, enriquecer
    ):
        cid = cartera_id(auth_client)
        ajena = crear_plantilla(second_auth_client).json()["id"]
        for pid in (ajena, 9999):
            r = previsualizar(auth_client, cid, CSV_MIXTO, pid)
            assert r.status_code == 404
            assert r.json() == {"detail": "Plantilla no encontrada"}
        assert enriquecer.call_count == 0

    def test_contenido_demasiado_grande_retorna_422(self, auth_client, enriquecer):
        cid, pid = escenario(auth_client)
        r = previsualizar(auth_client, cid, "a" * (LIMITE_CONTENIDO + 1), pid)
        assert r.status_code == 422

    def test_plantilla_id_invalido_retorna_422(self, auth_client, enriquecer):
        cid, _ = escenario(auth_client)
        assert previsualizar(auth_client, cid, CSV_MIXTO, "x").status_code == 422

    def test_sin_sesion_retorna_401(self, client):
        assert previsualizar(client, 1, CSV_MIXTO, 1).status_code == 401


class TestConfirmar:
    def _importar_mixto(self, client):
        cid, pid = escenario(client)
        huella = previsualizar(client, cid, CSV_MIXTO, pid).json()["huella"]
        return cid, pid, huella

    def test_importa_solo_las_filas_importar(self, auth_client, enriquecer, db_session):
        cid, pid, huella = self._importar_mixto(auth_client)
        r = confirmar(auth_client, cid, CSV_MIXTO, pid, huella)

        assert r.status_code == 200
        assert r.json() == {"importados": 3, "omitidos": 3}
        actuales = movimientos(auth_client, cid)
        assert [
            (
                m["tipo"], m["fecha"], m["instrumento"]["isin"], m["cantidad"],
                m["precio"], m["comision"], m["tipo_cambio"], m["notas"],
            )
            for m in actuales
        ] == [
            ("compra", "2025-05-02", US, 2.5, 100.0, 0.1, 1.25, None),
            ("venta", "2025-03-10", ES, 4.0, 12.0, 0.5, 1.0, None),
            ("compra", "2025-02-04", ES, 10.0, 11.5, 0.0, 1.0, None),
        ]  # fmt: skip
        assert all("referencia_externa" not in m for m in actuales)
        referencias = {
            m.referencia_externa
            for m in db_session.query(models.Movimiento).filter_by(cartera_id=cid)
        }
        assert referencias == {"B1", "S1", "B4"}

    def test_crea_los_instrumentos_nuevos(self, auth_client, enriquecer):
        cid, pid, huella = self._importar_mixto(auth_client)
        confirmar(auth_client, cid, CSV_MIXTO, pid, huella)
        creados = {i["isin"]: i for i in instrumentos(auth_client)}
        assert set(creados) == {ES, US}
        for isin, datos in DATOS_ISIN.items():
            assert {k: creados[isin][k] for k in datos} == datos

    def test_no_crea_instrumentos_sin_filas_importadas(self, auth_client, enriquecer):
        cid, pid = escenario(auth_client)
        contenido = csv_t212(
            fila_csv("Market sell", "2025-02-01", US, "S1", "1", "10", "USD")
        )
        huella = previsualizar(auth_client, cid, contenido, pid).json()["huella"]
        r = confirmar(auth_client, cid, contenido, pid, huella)
        assert r.status_code == 200
        assert r.json() == {"importados": 0, "omitidos": 1}
        assert_sin_escrituras(auth_client, cid)

    def test_reutiliza_instrumento_existente_sin_enriquecer(
        self, auth_client, enriquecer
    ):
        cid, pid = escenario(auth_client)
        uno = csv_t212(fila_csv("Market buy", "2025-02-01", ES, "B1", "1", "10"))
        confirmar(
            auth_client,
            cid,
            uno,
            pid,
            previsualizar(auth_client, cid, uno, pid).json()["huella"],
        )
        enriquecer.reset_mock()

        dos = csv_t212(fila_csv("Market buy", "2025-02-02", ES, "B2", "2", "10"))
        p = previsualizar(auth_client, cid, dos, pid)
        assert p.json()["filas"][0]["nombre"] == "Alpha"
        r = confirmar(auth_client, cid, dos, pid, p.json()["huella"])

        assert r.json() == {"importados": 1, "omitidos": 0}
        assert enriquecer.call_count == 0
        assert len(instrumentos(auth_client)) == 1
        assert len(movimientos(auth_client, cid)) == 2

    def test_segunda_importacion_marca_ya_importada_e_importa_cero(
        self, auth_client, enriquecer
    ):
        cid, pid, huella = self._importar_mixto(auth_client)
        confirmar(auth_client, cid, CSV_MIXTO, pid, huella)
        antes = movimientos(auth_client, cid)

        p = previsualizar(auth_client, cid, CSV_MIXTO, pid)
        assert motivos(p) == {
            2: "Ya importada (ID B1)",
            3: "Ya importada (ID S1)",
            4: 'Tipo "Deposit" no es compra ni venta',
            5: "Sin ISIN",
            6: 'Cantidad "abc" no válida',
            7: "Ya importada (ID B4)",
        }
        assert p.json()["resumen"] == {"total": 6, "importar": 0, "omitir": 6}

        r = confirmar(auth_client, cid, CSV_MIXTO, pid, p.json()["huella"])
        assert r.status_code == 200
        assert r.json() == {"importados": 0, "omitidos": 6}
        assert movimientos(auth_client, cid) == antes

    def test_ids_de_otra_cartera_no_cuentan_como_importados(
        self, auth_client, enriquecer
    ):
        cid, pid, huella = self._importar_mixto(auth_client)
        confirmar(auth_client, cid, CSV_MIXTO, pid, huella)
        otra = auth_client.post(f"{BASE}/carteras", json={"nombre": "Otra"}).json()[
            "id"
        ]

        p = previsualizar(auth_client, otra, CSV_MIXTO, pid)
        assert p.json()["resumen"] == {"total": 6, "importar": 3, "omitir": 3}

    def test_venta_contra_posicion_existente(self, auth_client, enriquecer):
        cid, pid = escenario(auth_client)
        compra = csv_t212(
            fila_csv("Market buy", "2025-02-01", US, "B1", "3", "10", "USD")
        )
        confirmar(
            auth_client,
            cid,
            compra,
            pid,
            previsualizar(auth_client, cid, compra, pid).json()["huella"],
        )

        ventas = csv_t212(
            fila_csv("Market sell", "2025-03-01", US, "S1", "5", "12", "USD"),
            fila_csv("Market sell", "2025-03-02", US, "S2", "2", "12", "USD"),
        )
        p = previsualizar(auth_client, cid, ventas, pid)
        assert estados(p) == {2: "omitir", 3: "importar"}
        assert motivos(p)[2] == "Venta de 5 supera la posición disponible (3)"
        r = confirmar(auth_client, cid, ventas, pid, p.json()["huella"])
        assert r.json() == {"importados": 1, "omitidos": 1}
        assert len(movimientos(auth_client, cid)) == 2

    def test_plantilla_solo_de_ventas(self, auth_client, enriquecer):
        cid, pid = escenario(auth_client)
        compra = csv_t212(
            fila_csv("Market buy", "2025-02-01", US, "B1", "3", "10", "USD")
        )
        confirmar(
            auth_client,
            cid,
            compra,
            pid,
            previsualizar(auth_client, cid, compra, pid).json()["huella"],
        )
        solo_ventas = crear_plantilla(
            auth_client, nombre="Solo ventas", config=config_t212(valores_compra=[])
        ).json()["id"]

        csv = csv_t212(
            fila_csv("Market buy", "2025-03-01", US, "B2", "1", "11", "USD"),
            fila_csv("Market sell", "2025-03-02", US, "S1", "2", "12", "USD"),
        )
        p = previsualizar(auth_client, cid, csv, solo_ventas)
        assert estados(p) == {2: "omitir", 3: "importar"}
        assert motivos(p)[2] == 'Tipo "Market buy" no es compra ni venta'
        r = confirmar(auth_client, cid, csv, solo_ventas, p.json()["huella"])
        assert r.json() == {"importados": 1, "omitidos": 1}
        assert sorted(m["tipo"] for m in movimientos(auth_client, cid)) == [
            "compra",
            "venta",
        ]

    def test_huella_distinta_retorna_409_y_no_escribe(self, auth_client, enriquecer):
        cid, pid, huella = self._importar_mixto(auth_client)
        detalle = "La previsualización ya no coincide con los datos actuales. Vuelve a previsualizar."

        modificado = CSV_MIXTO.replace(",10,11.5,", ",11,11.5,")
        for contenido, h in ((modificado, huella), (CSV_MIXTO, "no-es-la-huella")):
            r = confirmar(auth_client, cid, contenido, pid, h)
            assert r.status_code == 409
            assert r.json() == {"detail": detalle}
            assert_sin_escrituras(auth_client, cid)

    def test_huella_obsoleta_tras_otra_importacion_retorna_409(
        self, auth_client, enriquecer
    ):
        cid, pid, huella = self._importar_mixto(auth_client)
        assert confirmar(auth_client, cid, CSV_MIXTO, pid, huella).status_code == 200
        antes = movimientos(auth_client, cid)

        r = confirmar(auth_client, cid, CSV_MIXTO, pid, huella)
        assert r.status_code == 409
        assert movimientos(auth_client, cid) == antes

    def test_una_sola_transaccion_y_sin_escrituras_parciales(
        self, auth_client, enriquecer
    ):
        cid, pid, huella = self._importar_mixto(auth_client)

        with patch.object(
            Session, "commit", autospec=True, side_effect=RuntimeError("boom")
        ):
            with pytest.raises(RuntimeError, match="boom"):
                confirmar(auth_client, cid, CSV_MIXTO, pid, huella)
        assert_sin_escrituras(auth_client, cid)

        with patch.object(
            Session, "commit", autospec=True, side_effect=Session.commit
        ) as commit:
            assert (
                confirmar(auth_client, cid, CSV_MIXTO, pid, huella).status_code == 200
            )
        assert commit.call_count == 1

    def test_errores_de_lectura_retornan_400(self, auth_client, enriquecer):
        config = config_t212()
        config["columnas"]["isin"] = "Isin"
        cid, pid = escenario(auth_client, config)
        r = confirmar(auth_client, cid, CSV_MIXTO, pid, "x")
        assert r.status_code == 400
        assert r.json() == {
            "detail": "La columna 'Isin' de la plantilla no existe en el CSV"
        }
        assert_sin_escrituras(auth_client, cid)

    def test_cartera_ajena_o_inexistente_retorna_404(
        self, auth_client, second_auth_client, enriquecer
    ):
        _, pid = escenario(auth_client)
        ajena = cartera_id(second_auth_client)
        for cid in (ajena, 9999):
            r = confirmar(auth_client, cid, CSV_MIXTO, pid, "x")
            assert r.status_code == 404
            assert r.json() == {"detail": "Cartera no encontrada"}
        assert_sin_escrituras(second_auth_client, ajena)

    def test_plantilla_ajena_o_inexistente_retorna_404(
        self, auth_client, second_auth_client, enriquecer
    ):
        cid = cartera_id(auth_client)
        ajena = crear_plantilla(second_auth_client).json()["id"]
        for pid in (ajena, 9999):
            r = confirmar(auth_client, cid, CSV_MIXTO, pid, "x")
            assert r.status_code == 404
            assert r.json() == {"detail": "Plantilla no encontrada"}
        assert_sin_escrituras(auth_client, cid)

    def test_validacion_de_cuerpo_retorna_422(self, auth_client, enriquecer):
        cid, pid = escenario(auth_client)
        grande = "a" * (LIMITE_CONTENIDO + 1)
        assert confirmar(auth_client, cid, grande, pid, "x").status_code == 422
        sin_huella = auth_client.post(
            f"{BASE}/carteras/{cid}/importaciones",
            json={"contenido": CSV_MIXTO, "plantilla_id": pid},
        )
        assert sin_huella.status_code == 422

    def test_sin_sesion_retorna_401(self, client):
        assert confirmar(client, 1, CSV_MIXTO, 1, "x").status_code == 401


class TestMovimientoManualConImportaciones:
    def test_creacion_manual_mantiene_su_forma(self, auth_client):
        cid = cartera_id(auth_client)
        mock = AsyncMock(return_value=DATOS_ISIN[ES])
        body = {
            "cartera_id": cid,
            "isin": ES,
            "tipo": "compra",
            "fecha": "2025-02-01",
            "cantidad": 1,
            "precio": 10,
        }
        with patch.multiple(
            "app.routers.movimientos.precios", enriquecer_por_isin=mock
        ):
            primero = auth_client.post(f"{BASE}/movimientos", json=body)
            segundo = auth_client.post(f"{BASE}/movimientos", json=body)

        # No broker ID: several manual movements never collide on referencia_externa
        assert primero.status_code == 200
        assert segundo.status_code == 200
        assert set(primero.json()) == {
            "id", "cartera_id", "instrumento", "tipo", "fecha", "cantidad",
            "precio", "comision", "tipo_cambio", "notas", "created_at",
        }  # fmt: skip
        assert len(movimientos(auth_client, cid)) == 2


class TestVentaConRuidoDeComaFlotante:
    def test_venta_dentro_de_la_tolerancia_no_rompe_el_resumen(
        self, auth_client, enriquecer
    ):
        # 0.3 - 0.1 leaves 0.19999999999999998 in the FIFO lot: the 0.2 sale is
        # accepted by the tolerance and must be stored so FIFO can cover it
        contenido = csv_t212(
            fila_csv("Market buy", "2025-02-04 08:00:00", ES, "B1", "0.3", "10"),
            fila_csv("Market sell", "2025-02-05 08:00:00", ES, "S1", "0.1", "10"),
            fila_csv("Market sell", "2025-02-06 08:00:00", ES, "S2", "0.2", "10"),
        )
        cid, pid = escenario(auth_client)
        preview = previsualizar(auth_client, cid, contenido, pid)
        assert estados(preview) == {2: "importar", 3: "importar", 4: "importar"}
        r = confirmar(auth_client, cid, contenido, pid, preview.json()["huella"])
        assert r.json() == {"importados": 3, "omitidos": 0}

        with mock_precios():
            resumen = auth_client.get(f"{BASE}/carteras/{cid}/resumen")
        assert resumen.status_code == 200


class TestArchivosIlegibles:
    """S4, D7: a file the CSV reader rejects is a 400 with a reason, never a 500."""

    MENSAJE = (
        "El CSV no se puede leer: tiene un campo demasiado largo "
        "o una comilla sin cerrar"
    )

    @pytest.mark.parametrize(
        "contenido",
        [
            "a,b\n1," + "x" * 200_000 + "\n",
            # An unclosed quote swallows the rest of the file into one field
            csv_t212(
                'Market buy,2025-01-01,ES0000000001,"Q,1,1,EUR,1.0,',
                *[
                    fila_csv("Market buy", "2025-01-01", ES, f"Q{i}", "1", "1")
                    for i in range(3000)
                ],
            ),
        ],
        ids=["campo_enorme", "comilla_sin_cerrar"],
    )
    def test_campo_enorme_da_400(self, auth_client, enriquecer, contenido):
        cid, pid = escenario(auth_client)
        r_inspeccion = inspeccionar(auth_client, contenido)
        r_preview = previsualizar(auth_client, cid, contenido, pid)
        r_confirm = confirmar(auth_client, cid, contenido, pid, "0" * 64)
        for r in (r_inspeccion, r_preview, r_confirm):
            assert r.status_code == 400
            assert r.json()["detail"] == self.MENSAJE
        assert_sin_escrituras(auth_client, cid)


class TestConfirmarAtomico:
    """D6: a write conflict in the middle of the confirm rolls everything back."""

    def test_conflicto_de_referencia_revierte_todo(
        self, auth_client, enriquecer, db_session
    ):
        cid, pid = escenario(auth_client)
        inst = models.Instrumento(isin=ES, nombre="Alpha")
        db_session.add(inst)
        db_session.commit()
        db_session.add(
            models.Movimiento(
                cartera_id=cid,
                instrumento_id=inst.id,
                tipo="compra",
                fecha=date(2025, 1, 1),
                cantidad=1,
                precio=1,
                comision=0,
                referencia_externa="R1",
            )
        )
        db_session.commit()
        contenido = csv_t212(
            fila_csv("Market buy", "2025-01-05", US, "R0", "1", "1", "USD", "1.1"),
            fila_csv("Market buy", "2025-01-06", ES, "R1", "1", "1"),
        )
        original = importacion.planificar

        # Simulates a concurrent import: the plan misses the stored ID R1
        async def sin_referencias(resultados, referencias, *args):
            return await original(resultados, set(), *args)

        with patch("app.routers.importaciones.importacion.planificar", sin_referencias):
            huella = previsualizar(auth_client, cid, contenido, pid).json()["huella"]
            r = confirmar(auth_client, cid, contenido, pid, huella)

        assert r.status_code == 409
        assert r.json()["detail"] == (
            "La previsualización ya no coincide con los datos actuales. "
            "Vuelve a previsualizar."
        )
        assert len(movimientos(auth_client, cid)) == 1
        assert [i["isin"] for i in instrumentos(auth_client)] == [ES]
