"""
CSV movement import (issue #36): read a broker export and normalize each row
with a user template (`schemas.PlantillaConfig`).

Pure functions, no database or network: the router decides what is imported.
Messages are user-facing, in Spanish, like the rest of the API.
"""

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime

from app.schemas import PlantillaConfig

MAX_FILAS = 5000
FILAS_MUESTRA = 5
# Columns with more distinct values are not offered for value mapping
MAX_VALORES_DISTINTOS = 20
DELIMITADORES = (",", ";", "\t", "|")

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
# Date part of an ISO date or datetime: 2025-02-04, 2025-02-04 08:00:01, ...T13:46Z
FECHA_ISO_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[T ].*)?$")
# Thousands groups are optional but, when present, must be complete. This
# rejects "1.234,56" in a dot-decimal template instead of reading 1.23456.
NUMERO_RE = {
    ".": re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$"),
    ",": re.compile(r"^[+-]?(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d+)?$"),
}


class ImportacionError(Exception):
    """The file as a whole cannot be read with this template."""


@dataclass(frozen=True)
class FilaCsv:
    # Physical line in the file (header = 1)
    linea: int
    valores: dict[str, str]


@dataclass(frozen=True)
class TablaCsv:
    delimitador: str
    columnas: list[str]
    filas: list[FilaCsv]


@dataclass(frozen=True)
class FilaNormalizada:
    linea: int
    tipo: str  # compra | venta
    fecha: date
    isin: str
    cantidad: float
    precio: float  # price column as is (currency of the price column)
    comision: float  # EUR, like manual movements
    tipo_cambio: float | None  # currency units per 1 EUR (calculos.py)
    referencia: str | None


@dataclass(frozen=True)
class FilaOmitida:
    linea: int
    motivo: str


class _OmitirError(Exception):
    """Internal: the row is skipped with this reason."""


# ── Reading ──────────────────────────────────────────────────────────────────


def _detectar_delimitador(texto: str) -> str:
    """Most frequent candidate in the header line; ',' when none appears."""
    cabecera = next(linea for linea in texto.splitlines() if linea.strip())
    conteos = {d: cabecera.count(d) for d in DELIMITADORES}
    mejor = max(DELIMITADORES, key=lambda d: conteos[d])
    return mejor if conteos[mejor] > 0 else ","


def _validar_cabecera(registro: list[str]) -> list[str]:
    columnas = [c.strip() for c in registro]
    # Exports often end every line with a delimiter: drop trailing empty names
    while columnas and not columnas[-1]:
        columnas.pop()
    if "" in columnas:
        raise ImportacionError("El CSV tiene columnas sin nombre en la cabecera")
    repetidas = sorted({c for c in columnas if columnas.count(c) > 1})
    if repetidas:
        raise ImportacionError(
            f"El CSV tiene columnas repetidas: {', '.join(repetidas)}"
        )
    return columnas


def leer_csv(contenido: str, delimitador: str | None) -> TablaCsv:
    """Parse the file: first non-empty line is the header, blank lines skipped."""
    texto = contenido.lstrip("\ufeff")
    if not texto.strip():
        raise ImportacionError("El CSV está vacío")
    delim = delimitador or _detectar_delimitador(texto)

    reader = csv.reader(io.StringIO(texto, newline=""), delimiter=delim)
    columnas: list[str] | None = None
    filas: list[FilaCsv] = []
    for registro in reader:
        if not any(celda.strip() for celda in registro):
            continue
        if columnas is None:
            columnas = _validar_cabecera(registro)
            continue
        if len(filas) == MAX_FILAS:
            raise ImportacionError(f"El CSV supera el máximo de {MAX_FILAS} filas")
        valores = {
            col: registro[i].strip() if i < len(registro) else ""
            for i, col in enumerate(columnas)
        }
        filas.append(FilaCsv(linea=reader.line_num, valores=valores))

    if columnas is None or not filas:
        raise ImportacionError("El CSV no tiene filas de datos")
    return TablaCsv(delimitador=delim, columnas=columnas, filas=filas)


def inspeccionar(contenido: str, delimitador: str | None) -> dict:
    """Headers, sample rows and small value sets, to build a template."""
    tabla = leer_csv(contenido, delimitador)
    distintos: dict[str, list[str]] = {}
    for col in tabla.columnas:
        valores = {f.valores[col] for f in tabla.filas if f.valores[col]}
        if 0 < len(valores) <= MAX_VALORES_DISTINTOS:
            distintos[col] = sorted(valores)
    return {
        "delimitador": tabla.delimitador,
        "columnas": tabla.columnas,
        "filas_muestra": [
            [f.valores[c] for c in tabla.columnas] for f in tabla.filas[:FILAS_MUESTRA]
        ],
        "valores_distintos": distintos,
    }


def validar_columnas(tabla: TablaCsv, config: PlantillaConfig) -> None:
    c = config.columnas
    usadas = [
        c.fecha,
        c.isin,
        c.tipo,
        c.cantidad,
        c.precio,
        *c.comision,
        c.tipo_cambio,
        c.moneda_precio,
        c.referencia,
    ]
    for col in usadas:
        if col is not None and col not in tabla.columnas:
            raise ImportacionError(
                f"La columna '{col}' de la plantilla no existe en el CSV"
            )


# ── Normalization ────────────────────────────────────────────────────────────


def _tipo(valor: str, config: PlantillaConfig) -> str:
    clave = valor.casefold()
    if clave in {v.casefold() for v in config.valores_compra}:
        return "compra"
    if clave in {v.casefold() for v in config.valores_venta}:
        return "venta"
    if not valor:
        raise _OmitirError("Tipo vacío, no es compra ni venta")
    raise _OmitirError(f'Tipo "{valor}" no es compra ni venta')


def _isin(valor: str) -> str:
    if not valor:
        raise _OmitirError("Sin ISIN")
    isin = valor.upper()
    if not ISIN_RE.match(isin):
        raise _OmitirError(f'ISIN "{valor}" no válido')
    return isin


def _fecha(valor: str, formato: str | None) -> date:
    try:
        if formato is None:
            m = FECHA_ISO_RE.match(valor)
            if m:
                return date.fromisoformat(m.group(1))
        else:
            return datetime.strptime(valor, formato).date()
    except ValueError:
        pass
    raise _OmitirError(f'Fecha "{valor}" no válida')


def _numero(valor: str, separador_decimal: str) -> float | None:
    """Absolute value of a number cell; None when empty. ValueError if malformed."""
    texto = valor.replace(" ", "").replace("\u00a0", "")
    if not texto:
        return None
    if not NUMERO_RE[separador_decimal].match(texto):
        raise ValueError(valor)
    if separador_decimal == ".":
        texto = texto.replace(",", "")
    else:
        texto = texto.replace(".", "").replace(",", ".")
    return abs(float(texto))


def _positivo(valor: str, separador_decimal: str, motivo: str) -> float:
    try:
        numero = _numero(valor, separador_decimal)
    except ValueError:
        numero = None
    if not numero:
        raise _OmitirError(motivo)
    return numero


def _comision(fila: FilaCsv, config: PlantillaConfig) -> float:
    total = 0.0
    for col in config.columnas.comision:
        valor = fila.valores[col]
        try:
            total += _numero(valor, config.separador_decimal) or 0.0
        except ValueError:
            raise _OmitirError(f'Comisión "{valor}" no válida') from None
    # Sums like 2.5 + 0.1 must not carry float noise into stored amounts
    return round(total, 10)


def _tipo_cambio(fila: FilaCsv, config: PlantillaConfig) -> float | None:
    c = config.columnas
    if c.moneda_precio and fila.valores[c.moneda_precio].upper() == "EUR":
        return 1.0
    if not c.tipo_cambio:
        return None
    valor = fila.valores[c.tipo_cambio]
    try:
        tipo_cambio = _numero(valor, config.separador_decimal)
    except ValueError:
        tipo_cambio = 0.0
    if tipo_cambio is None:
        return None
    if tipo_cambio == 0:
        raise _OmitirError(f'Tipo de cambio "{valor}" no válido')
    return 1 / tipo_cambio if config.invertir_tipo_cambio else tipo_cambio


def normalizar_fila(
    fila: FilaCsv, config: PlantillaConfig
) -> FilaNormalizada | FilaOmitida:
    """Map one row to a movement, or explain why it is skipped.

    The type is checked first, so dividends or deposits without an ISIN are
    reported as "not a buy or sell" rather than as missing data.
    """
    c = config.columnas
    v = fila.valores
    sep = config.separador_decimal
    try:
        return FilaNormalizada(
            linea=fila.linea,
            tipo=_tipo(v[c.tipo], config),
            isin=_isin(v[c.isin]),
            fecha=_fecha(v[c.fecha], config.formato_fecha),
            cantidad=_positivo(
                v[c.cantidad], sep, f'Cantidad "{v[c.cantidad]}" no válida'
            ),
            precio=_positivo(v[c.precio], sep, f'Precio "{v[c.precio]}" no válido'),
            comision=_comision(fila, config),
            tipo_cambio=_tipo_cambio(fila, config),
            referencia=(v[c.referencia] or None) if c.referencia else None,
        )
    except _OmitirError as omitir:
        return FilaOmitida(linea=fila.linea, motivo=str(omitir))
