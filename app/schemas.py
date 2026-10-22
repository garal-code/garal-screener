import re
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

# -- User / Auth ----------------------------------------------------------------


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    nombre: str | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    nombre: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=72)


# -- Cartera ------------------------------------------------------------------


class CarteraCreate(BaseModel):
    nombre: str
    descripcion: str | None = None


class CarteraOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    descripcion: str | None
    created_at: datetime


# -- Instrumento --------------------------------------------------------------


class InstrumentoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    isin: str
    ticker: str | None = None
    nombre: str | None = None
    tipo: str | None = None
    sector: str | None = None
    pais: str | None = None
    moneda: str | None = None
    exchange: str | None = None


class InstrumentoUpdate(BaseModel):
    """Schema para PATCH /instrumentos/{id} — todos los campos opcionales."""

    ticker: str | None = None
    nombre: str | None = None
    tipo: str | None = None
    sector: str | None = None
    pais: str | None = None
    moneda: str | None = None
    exchange: str | None = None


# -- Movimiento ---------------------------------------------------------------


class MovimientoCreate(BaseModel):
    cartera_id: int
    isin: str
    tipo: str = Field(pattern="^(compra|venta)$")  # validacion en schema
    fecha: date
    cantidad: float = Field(gt=0)
    precio: float = Field(gt=0)
    comision: float | None = Field(default=0.0, ge=0)
    tipo_cambio: float | None = Field(default=None, gt=0)
    notas: str | None = None


class MovimientoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cartera_id: int
    instrumento: InstrumentoOut
    tipo: str
    fecha: date
    cantidad: float
    precio: float
    comision: float
    tipo_cambio: float | None
    notas: str | None
    created_at: datetime


# -- Importacion CSV -----------------------------------------------------------

# A CSV header name or a cell value: surrounding spaces are ignored, empty is invalid
TextoNoVacio = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ColumnasImportacion(BaseModel):
    """CSV header that feeds each movement field."""

    model_config = ConfigDict(extra="forbid")

    fecha: TextoNoVacio
    isin: TextoNoVacio
    tipo: TextoNoVacio
    cantidad: TextoNoVacio
    precio: TextoNoVacio
    # Commission = sum of these columns (brokers split fees across several)
    comision: list[TextoNoVacio] = Field(default_factory=list)
    tipo_cambio: TextoNoVacio | None = None
    # Currency of the price column; EUR rows get tipo_cambio 1.0
    moneda_precio: TextoNoVacio | None = None
    # Broker transaction ID used to skip rows already imported
    referencia: TextoNoVacio | None = None


class PlantillaConfig(BaseModel):
    """How to read one broker's CSV export."""

    model_config = ConfigDict(extra="forbid")

    # None = detect it from the file
    delimitador: Literal[",", ";", "\t", "|"] | None = None
    separador_decimal: Literal[".", ","] = "."
    # None = ISO date or datetime (2025-02-04, 2025-02-04 08:00:01, ...T13:46:07Z)
    formato_fecha: TextoNoVacio | None = None
    columnas: ColumnasImportacion
    # Case-insensitive exact values of the type column; any other value is skipped
    valores_compra: list[TextoNoVacio] = Field(min_length=1)
    valores_venta: list[TextoNoVacio] = Field(min_length=1)
    # True when the broker quotes EUR per currency unit instead of units per EUR
    invertir_tipo_cambio: bool = False

    @field_validator("formato_fecha")
    @classmethod
    def _formato_con_directivas(cls, v: str | None) -> str | None:
        if v is not None and not re.search(r"%[a-zA-Z]", v):
            raise ValueError(
                "formato_fecha debe usar directivas strptime, por ejemplo %d/%m/%Y"
            )
        return v

    @model_validator(mode="after")
    def _coherente(self) -> "PlantillaConfig":
        compra = {v.casefold() for v in self.valores_compra}
        comunes = sorted(v for v in self.valores_venta if v.casefold() in compra)
        if comunes:
            raise ValueError(
                f"Un valor no puede ser de compra y venta a la vez: {', '.join(comunes)}"
            )
        if self.delimitador == self.separador_decimal:
            raise ValueError("El delimitador no puede ser igual al separador decimal")
        return self


class PlantillaImportacionIn(BaseModel):
    nombre: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    config: PlantillaConfig


class PlantillaImportacionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    config: PlantillaConfig
    created_at: datetime
    updated_at: datetime


# Largest CSV accepted by the import endpoints (characters)
MAX_CONTENIDO_CSV = 2_000_000

ContenidoCsv = Annotated[str, Field(max_length=MAX_CONTENIDO_CSV)]


class InspeccionarIn(BaseModel):
    contenido: ContenidoCsv
    delimitador: Literal[",", ";", "\t", "|"] | None = None


class InspeccionarOut(BaseModel):
    delimitador: str
    columnas: list[str]
    filas_muestra: list[list[str]]
    valores_distintos: dict[str, list[str]]


class PrevisualizarImportacionIn(BaseModel):
    contenido: ContenidoCsv
    plantilla_id: int


class ConfirmarImportacionIn(PrevisualizarImportacionIn):
    # `huella` returned by the preview of this same file and template
    huella: str


class FilaImportacionOut(BaseModel):
    """One data row of the CSV. Rows skipped while reading have no data."""

    linea: int
    estado: Literal["importar", "omitir"]
    motivo: str | None
    tipo: str | None
    fecha: date | None
    isin: str | None
    nombre: str | None
    cantidad: float | None
    precio: float | None
    comision: float | None
    tipo_cambio: float | None
    referencia: str | None


class ResumenImportacionOut(BaseModel):
    total: int
    importar: int
    omitir: int


class PrevisualizacionImportacionOut(BaseModel):
    huella: str
    resumen: ResumenImportacionOut
    filas: list[FilaImportacionOut]


class ResultadoImportacionOut(BaseModel):
    importados: int
    omitidos: int


# -- Posicion calculada -------------------------------------------------------


class PosicionOut(BaseModel):
    instrumento: InstrumentoOut
    cantidad_actual: float
    coste_total: float
    precio_medio: float
    plusvalia_realizada: float
    precio_actual: float | None = None
    # Campos existentes (backwards-compat — valor_actual == valor_actual_eur, precio_actual == precio_actual_nativo)
    valor_actual: float | None = None
    plusvalia_latente: float | None = None
    rentabilidad_pct: float | None = None
    plusvalia_total: float | None = None
    # Dual-currency
    precio_actual_eur: float | None = None
    precio_actual_nativo: float | None = None
    valor_actual_eur: float | None = None
    valor_actual_nativo: float | None = None
    moneda_nativa: str | None = None
    fx_actual: float | None = None


# -- Resumen cartera ----------------------------------------------------------


class ResumenCartera(BaseModel):
    cartera: CarteraOut
    valor_total: float
    coste_total: float
    plusvalia_latente: float
    plusvalia_realizada: float
    plusvalia_total: float
    rentabilidad_pct: float
    num_posiciones: int
    posiciones: list[PosicionOut]


# -- Rentabilidad por periodo --------------------------------------------------


class PosicionRentabilidadOut(BaseModel):
    instrumento: InstrumentoOut
    cantidad_actual: float
    coste_total: float
    valor_actual: float | None = None
    plusvalia_latente: float | None = None
    plusvalia_realizada: float
    plusvalia_total: float
    rentabilidad_pct: float
    moneda_nativa: str | None = None


class RentabilidadCartera(BaseModel):
    periodo: str
    fecha_inicio: date
    fecha_fin: date
    valor_total: float
    coste_total: float
    plusvalia_latente: float
    plusvalia_realizada: float
    plusvalia_total: float
    rentabilidad_pct: float
    posiciones: list[PosicionRentabilidadOut]
    # Tickers de posiciones que ya se tenían al inicio del periodo pero de
    # las que no se pudo obtener precio histórico (excluidas del cálculo)
    tickers_sin_dato: list[str]


# -- Analisis / agrupaciones --------------------------------------------------


class GrupoAnalisis(BaseModel):
    nombre: str
    valor: float
    peso_pct: float


class AnalisisCartera(BaseModel):
    por_sector: list[GrupoAnalisis]
    por_pais: list[GrupoAnalisis]
    por_tipo: list[GrupoAnalisis]
    por_moneda: list[GrupoAnalisis]


# -- Frontera eficiente (Markowitz) -------------------------------------------


class PuntoCarteraOut(BaseModel):
    # Cifras mensuales (sin anualizar)
    rentabilidad: float = Field(
        description=(
            "Rentabilidad mensual esperada (w·mu) con los pesos resueltos. En los "
            "puntos de la frontera coincide, salvo tolerancia numérica, con la "
            "rentabilidad objetivo para la que se minimizó la varianza."
        )
    )
    volatilidad: float = Field(description="Desviación típica mensual: sqrt(varianza)")
    varianza: float = Field(description="Varianza mensual de la cartera: wᵀΣw")
    pesos: dict[str, float] = Field(
        description="Peso de cada ticker: en [0, 1] (sin ventas en corto) y suman 1."
    )


# Por qué una posición abierta queda fuera del análisis:
# - sin_ticker: el instrumento no tiene ticker con el que pedir precios
# - sin_precio_actual: sin valor de mercado EUR actual conocido, finito y > 0
# - sin_tipo_cambio: precio nativo válido pero sin tipo de cambio EUR actual para
#   su moneda; su valor EUR es desconocido (se evalúa tras sin_precio_actual)
# - sin_historico: el proveedor no devolvió ningún precio mensual
# - historico_insuficiente: menos rentabilidades mensuales propias que el mínimo
MotivoExclusion = Literal[
    "sin_ticker",
    "sin_precio_actual",
    "sin_tipo_cambio",
    "sin_historico",
    "historico_insuficiente",
]


class ActivoExcluido(BaseModel):
    # Ticker (o ISIN si el instrumento no tiene ticker)
    ticker: str
    motivo: MotivoExclusion


class DatosEntradaFrontera(BaseModel):
    """Series mensuales usadas en el cálculo; columnas en el orden de `tickers`."""

    fechas_precios: list[date] = Field(
        description=(
            "Meses (día 1) de los cierres usados: cada mes de `fechas` y su mes "
            "anterior. Con meses consecutivos tiene un elemento más que `fechas`."
        )
    )
    precios: list[list[float]] = Field(
        description=(
            "Cierre ajustado mensual en moneda nativa: filas = `fechas_precios`, "
            "columnas = `tickers`."
        )
    )
    fechas: list[date] = Field(
        description="Meses (día 1) de cada rentabilidad; n_observaciones elementos"
    )
    rentabilidades: list[list[float]] = Field(
        description=(
            "Rentabilidad simple mensual: filas = `fechas`, columnas = `tickers`. "
            "La de un mes es cierre del mes / cierre del mes anterior - 1."
        )
    )


class EstadisticasActivosOut(BaseModel):
    """Estadísticas por activo en el orden de `tickers` (media y covarianza en la raíz)."""

    varianzas: list[float] = Field(
        description="Varianza muestral (n-1) mensual: diagonal de la covarianza"
    )
    volatilidades: list[float] = Field(
        description="Desviación típica muestral (n-1) mensual: sqrt(varianza)"
    )
    matriz_correlaciones: list[list[float]] = Field(
        description="Correlaciones derivadas de la covarianza: Σij / (σi·σj)"
    )


class DetalleCarteraActual(BaseModel):
    valores_eur: dict[str, float] = Field(
        description=(
            "Valor de mercado EUR de cada posición incluida; de él salen los pesos "
            "de `cartera_actual`"
        )
    )
    rentabilidades: list[float] = Field(
        description=(
            "Rentabilidad mensual de la cartera actual en cada mes de `fechas`: "
            "pesos · rentabilidades del mes"
        )
    )


class DetalleCalculoFrontera(BaseModel):
    """Cómo se ha calculado el resultado, paso a paso (como la hoja de referencia)."""

    datos: DatosEntradaFrontera
    estadisticas: EstadisticasActivosOut
    cartera_actual: DetalleCarteraActual


class FronteraEficienteCartera(BaseModel):
    fecha_inicio: date
    # Último día del último mes cerrado: el mes en curso nunca se usa
    fecha_fin: date
    frecuencia: Literal["mensual"] = "mensual"
    n_observaciones: int
    # Activos analizados; ordenan rentabilidades_esperadas y matriz_covarianzas
    tickers: list[str]
    rentabilidades_esperadas: list[float]
    matriz_covarianzas: list[list[float]]
    # Ordenada por rentabilidad creciente; el primero es la de mínima varianza
    frontera: list[PuntoCarteraOut]
    cartera_minima_varianza: PuntoCarteraOut
    # Pesos = valor de mercado EUR actual, renormalizado sobre los incluidos
    cartera_actual: PuntoCarteraOut
    excluidos: list[ActivoExcluido]
    # Fracción [0, 1] del valor EUR de la cartera que queda fuera del análisis.
    # Solo cuentan posiciones con valor EUR conocido: las excluidas por
    # sin_ticker, sin_precio_actual o sin_tipo_cambio no tienen valor y no cuentan
    peso_excluido: float
    detalle: DetalleCalculoFrontera
