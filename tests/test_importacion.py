"""
Integration tests for CSV movement import (issue #36): import templates,
CSV inspection, preview and confirmation.

All CSV fixtures are synthetic; they only mimic the shape of real broker
exports (Trading212, Trade Republic) and contain no personal data.
"""

import copy

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

    def test_valores_compra_vacios_retorna_422(self, auth_client):
        r = crear_plantilla(auth_client, config=config_t212(valores_compra=[]))
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
