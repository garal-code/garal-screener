# Importación de movimientos desde un CSV de broker

Permite cargar movimientos (compras y ventas) desde el CSV que exporta un broker, en lugar de introducirlos a mano. No usa IA: el usuario indica qué columna del CSV alimenta cada campo, guarda esa correspondencia como **plantilla** y la reutiliza en cada importación.

La función es genérica. Trading212 y Trade Republic son solo dos ejemplos de plantilla (ver [Ejemplos](#ejemplos-de-plantilla)); cualquier CSV con una fila por operación se puede mapear.

## Índice

1. [Flujo](#flujo)
2. [Plantillas](#plantillas)
3. [Normalización de filas](#normalización-de-filas)
4. [Endpoints](#endpoints)
5. [Motivos de omisión](#motivos-de-omisión)
6. [Seguridad y límites](#seguridad-y-límites)
7. [Ejemplos de plantilla](#ejemplos-de-plantilla)

---

## Flujo

1. **Inspeccionar** el CSV: devuelve el delimitador, las cabeceras, unas filas de muestra y los valores distintos de las columnas con pocos valores (por ejemplo, la columna del tipo de operación). Sirve para construir la plantilla.
2. **Guardar la plantilla** (`/plantillas-importacion`).
3. **Previsualizar**: informa de qué se importaría y qué no, y por qué. No escribe nada.
4. **Confirmar**: inserta las filas marcadas como `importar`, enviando la `huella` de la previsualización.

Todas las rutas cuelgan de `/api/v1`, usan la cookie de sesión (sin sesión: `401`) y validan el cuerpo con Pydantic (cuerpo inválido: `422`). El CSV viaja como texto en el campo `contenido` de un JSON, sin subida multipart.

## Plantillas

Cada usuario tiene sus plantillas (`nombre` único por usuario). CRUD:

| Método y ruta | Descripción |
|---|---|
| `GET /plantillas-importacion` | Lista las plantillas propias, ordenadas por nombre |
| `POST /plantillas-importacion` | Crea una. `409` si el nombre ya existe |
| `PUT /plantillas-importacion/{id}` | Reemplaza nombre y configuración |
| `DELETE /plantillas-importacion/{id}` | Elimina |

Una plantilla ajena o inexistente responde `404` con `"Plantilla no encontrada"`. Cuerpo de `POST` y `PUT`: `{"nombre": str, "config": {...}}`. La respuesta añade `id`, `created_at` y `updated_at`.

### Campos de `config`

| Campo | Valores | Por defecto | Significado |
|---|---|---|---|
| `delimitador` | `","` `";"` `"\t"` `"\|"` o `null` | `null` | `null` = detectar el más frecuente en la cabecera (`,` si no hay ninguno) |
| `separador_decimal` | `"."` o `","` | `"."` | El otro símbolo se trata como separador de miles |
| `formato_fecha` | formato `strptime` (p. ej. `%d/%m/%Y`) o `null` | `null` | `null` = fecha o fecha-hora ISO (`2025-02-04`, `2025-02-04 08:00:01`, `2026-01-14T13:46:07.018Z`) |
| `columnas` | ver abajo | — | Cabecera del CSV que alimenta cada campo |
| `valores_compra` | lista | `[]` | Valores de la columna `tipo` que son compras |
| `valores_venta` | lista | `[]` | Valores de la columna `tipo` que son ventas |
| `invertir_tipo_cambio` | `true` / `false` | `false` | `true` si el broker da EUR por unidad de moneda en lugar de unidades de moneda por EUR |

`valores_compra` y `valores_venta` se comparan sin distinguir mayúsculas, con coincidencia exacta, y no pueden compartir valores. Al menos una de las dos listas debe tener valores, así que una plantilla puede ser solo de compras o solo de ventas (por ejemplo, un CSV que solo exporta ventas); si las dos están vacías la respuesta es 422 "Debe haber al menos un valor de compra o de venta". El delimitador no puede ser igual al separador decimal.

`columnas`:

| Campo | Obligatorio | Contenido |
|---|---|---|
| `fecha` | sí | Fecha de la operación |
| `isin` | sí | ISIN del instrumento |
| `tipo` | sí | Tipo de operación (se compara con `valores_compra` / `valores_venta`) |
| `cantidad` | sí | Número de títulos |
| `precio` | sí | Precio por título, en la moneda de la columna de precio |
| `comision` | no (lista) | Columnas cuya suma es la comisión |
| `tipo_cambio` | no | Tipo de cambio de la operación |
| `moneda_precio` | no | Moneda del precio; si es `EUR`, el tipo de cambio es `1.0` |
| `referencia` | no | ID de la operación en el broker; permite detectar duplicados |

Si una columna de la plantilla no existe en el CSV, la lectura falla con `400` (ver [Motivos](#errores-del-archivo-400)).

## Normalización de filas

Cada fila de datos se convierte en un movimiento o se omite con un motivo. Se evalúa en este orden, y la primera comprobación que falla decide el motivo:

1. **Tipo.** Si no está en `valores_compra` ni `valores_venta` (depósitos, dividendos, intereses...), se omite. Se comprueba primero para que una fila sin ISIN que no es compra ni venta se explique como "no es compra ni venta".
2. **ISIN.** Obligatorio (el sistema funciona por ISIN). Se pasa a mayúsculas y debe tener formato ISIN (2 letras, 9 alfanuméricos, 1 dígito).
3. **Fecha.** Con `formato_fecha`, o ISO si es `null`. Solo se conserva la fecha, sin hora.
4. **Cantidad y precio.** Números positivos. Con `separador_decimal` `"."`, la coma es separador de miles; con `","`, el punto lo es. Los grupos de miles deben estar completos: `1.234,56` en una plantilla de punto decimal se rechaza en vez de leerse como `1.23456`. Se toma el **valor absoluto** (el tipo sale de la columna `tipo`, no del signo).
5. **Comisión.** Suma de las columnas de `comision` (vacía = 0), en valor absoluto. Se guarda en EUR, como en los movimientos manuales.
6. **Tipo de cambio** (unidades de moneda por 1 EUR, igual que en los movimientos manuales):
   - Si `moneda_precio` está mapeada y la fila dice `EUR`, vale `1.0`.
   - Si no, se usa la columna `tipo_cambio`; vacía = sin tipo de cambio. Cero o no numérico omite la fila.
   - Con `invertir_tipo_cambio`, el valor leído se invierte (`1 / valor`).
7. **Referencia.** Texto de la columna `referencia` (vacío = sin referencia).

## Endpoints

### `POST /importaciones/inspeccionar`

Cuerpo:

```json
{"contenido": "<texto del CSV, máx. 2.000.000 caracteres>", "delimitador": ","}
```

`delimitador` es `","`, `";"`, `"\t"`, `"|"` o `null` (detectar). Respuesta `200`:

```json
{
  "delimitador": ",",
  "columnas": ["Action", "Time (UTC)", "ISIN"],
  "filas_muestra": [["Market buy", "2025-02-04 08:00:01", "ES0000000001"]],
  "valores_distintos": {"Action": ["Deposit", "Market buy", "Market sell"]}
}
```

- `filas_muestra`: hasta 5 filas, con los valores en el orden de `columnas`.
- `valores_distintos`: solo columnas con entre 1 y 20 valores distintos no vacíos.

### `POST /carteras/{cartera_id}/importaciones/previsualizar`

Cuerpo: `{"contenido": str, "plantilla_id": int}`. No escribe nada: ni instrumentos ni movimientos. Respuesta `200`:

```json
{
  "huella": "<sha256 hexadecimal>",
  "resumen": {"total": 6, "importar": 3, "omitir": 3},
  "filas": [
    {
      "linea": 2,
      "estado": "importar",
      "motivo": null,
      "tipo": "compra",
      "fecha": "2025-02-04",
      "isin": "ES0000000001",
      "nombre": "Alpha",
      "cantidad": 10.0,
      "precio": 11.5,
      "comision": 0.0,
      "tipo_cambio": 1.0,
      "referencia": "B1"
    },
    {
      "linea": 4,
      "estado": "omitir",
      "motivo": "Tipo \"Deposit\" no es compra ni venta",
      "tipo": null, "fecha": null, "isin": null, "nombre": null,
      "cantidad": null, "precio": null, "comision": null,
      "tipo_cambio": null, "referencia": null
    }
  ]
}
```

- `filas` va ordenada por `linea` (línea física del archivo; la cabecera es la 1).
- `estado` es `importar` u `omitir`; `motivo` es `null` salvo en las omitidas.
- Una fila omitida **al leerla** (tipo, ISIN, fecha, cantidad, precio, comisión o tipo de cambio no válidos) tiene todos los campos de datos a `null`. Una fila omitida **después** (duplicada, ya importada, ISIN sin datos, venta excesiva) conserva sus campos normalizados.
- `nombre` es el del instrumento si ya existe en la base de datos; si no, el que devuelve el enriquecimiento por ISIN.
- Las filas se procesan ordenadas por (`fecha`, `linea`), no por su posición en el archivo. Esto importa para duplicados y ventas.
- Los ISIN que no están en la base de datos se enriquecen (`precios.enriquecer_por_isin`) **una sola vez por ISIN y petición**, y solo si alguna fila llega a esa comprobación. Una respuesta vacía o una excepción omite la fila.
- `huella` es el SHA-256 hexadecimal del JSON canónico de `filas`: la lista tal cual se devuelve, con claves ordenadas, separadores `,` y `:` sin espacios, fechas en ISO y escapes ASCII (`json.dumps(filas, sort_keys=True, separators=(",", ":"))`).

Errores: `404` `"Cartera no encontrada"` (inexistente o ajena), `404` `"Plantilla no encontrada"` (inexistente o ajena), `400` si el archivo no se puede leer con la plantilla.

### `POST /carteras/{cartera_id}/importaciones`

Cuerpo: `{"contenido": str, "plantilla_id": int, "huella": str}`, con la `huella` de la previsualización del mismo archivo y plantilla. Respuesta `200`:

```json
{"importados": 3, "omitidos": 3}
```

El servidor recalcula el plan. Si la `huella` recalculada difiere (porque cambió el archivo, la plantilla o los datos de la cartera), responde `409` con `"La previsualización ya no coincide con los datos actuales. Vuelve a previsualizar."` y no escribe nada. Si coincide, inserta en **una sola transacción** los instrumentos nuevos (solo los de filas que se importan) y un movimiento por cada fila `importar`, con `referencia_externa` = `referencia`, `notas` vacías, y la comisión y el tipo de cambio normalizados. Cualquier fallo revierte todo. Si otra petición ocupa mientras tanto un ISIN o un ID de broker (violación de unicidad), también responde `409` con el mismo mensaje.

Confirmar un archivo sin filas `importar` es válido: responde `{"importados": 0, "omitidos": n}`.

Mismos `404` y `400` que la previsualización.

## Motivos de omisión

Textos exactos de `motivo` (en español, como el resto de la API).

### Al leer la fila

| Motivo | Causa |
|---|---|
| `Tipo "X" no es compra ni venta` | El tipo no está en `valores_compra` ni `valores_venta` |
| `Tipo vacío, no es compra ni venta` | La columna de tipo está vacía |
| `Sin ISIN` | ISIN vacío |
| `ISIN "X" no válido` | El ISIN no tiene formato válido |
| `Fecha "X" no válida` | No cumple `formato_fecha` (o ISO) |
| `Cantidad "X" no válida` | Vacía, cero o con formato numérico inválido |
| `Precio "X" no válido` | Vacío, cero o con formato numérico inválido |
| `Comisión "X" no válida` | Alguna columna de comisión con formato inválido |
| `Tipo de cambio "X" no válido` | Cero o con formato inválido |

### Al planificar (filas ya normalizadas)

Los duplicados se deciden por línea del archivo: gana la primera línea con cada `referencia`. El resto se comprueba en este orden, procesando por (`fecha`, `linea`):

| Motivo | Causa |
|---|---|
| `Duplicada en el archivo (ID X)` | Una línea anterior del archivo tiene la misma `referencia` (aunque esa fila también se haya omitido por otro motivo) |
| `Ya importada (ID X)` | Esa `referencia` ya está guardada como `referencia_externa` en la cartera |
| `No se encontraron datos para el ISIN X` | El ISIN no está en `instrumentos` y el enriquecimiento no devuelve datos o falla |
| `Venta de N supera la posición disponible (M)` | La venta supera la posición FIFO disponible |

En la venta, la posición disponible `M` sale de `calculos.calcular_posicion_fifo` sobre los movimientos que la cartera ya tiene de ese instrumento más las filas aceptadas antes en el orden de proceso (una venta rechazada no resta posición). La venta se acepta si y solo si el FIFO de `calculos` puede cubrirla, con la misma tolerancia de `1e-9` que `calculos` aplica al ruido de coma flotante (vender toda una posición comprada en varios lotes). `N` y `M` se muestran sin ceros finales (`21.654321`, `0`).

Las referencias son por cartera: el mismo ID de broker puede importarse en otra cartera.

### Errores del archivo (`400`)

Cuando el archivo entero no se puede leer, la petición responde `400` con `{"detail": <mensaje>}`:

| Mensaje |
|---|
| `El CSV está vacío` |
| `El CSV no tiene filas de datos` |
| `El CSV tiene columnas sin nombre en la cabecera` |
| `El CSV tiene columnas repetidas: A, B` |
| `El CSV supera el máximo de 5000 filas` |
| `El CSV no se puede leer: tiene un campo demasiado largo o una comilla sin cerrar` |
| `El CSV no se puede leer: <error del lector CSV>` |
| `La columna 'X' de la plantilla no existe en el CSV` |

## Seguridad y límites

- `contenido`: máximo 2.000.000 caracteres (`422` si lo supera).
- Máximo 5.000 filas de datos por archivo.
- Las plantillas y las carteras son por usuario; las ajenas responden `404`, nunca `403`.
- La previsualización es de solo lectura; la confirmación es atómica y exige la `huella`.

## Ejemplos de plantilla

Solo nombres de columna; ningún dato personal.

### Trading212

```json
{
  "nombre": "Trading212",
  "config": {
    "delimitador": ",",
    "separador_decimal": ".",
    "formato_fecha": null,
    "columnas": {
      "fecha": "Time (UTC)",
      "isin": "ISIN",
      "tipo": "Action",
      "cantidad": "No. of shares",
      "precio": "Price / share",
      "comision": ["Currency conversion fee"],
      "tipo_cambio": "Exchange rate",
      "moneda_precio": "Currency (Price / share)",
      "referencia": "ID"
    },
    "valores_compra": ["Market buy", "Limit buy"],
    "valores_venta": ["Market sell", "Limit sell"],
    "invertir_tipo_cambio": false
  }
}
```

### Trade Republic

```json
{
  "nombre": "Trade Republic",
  "config": {
    "delimitador": null,
    "separador_decimal": ".",
    "formato_fecha": null,
    "columnas": {
      "fecha": "datetime",
      "isin": "symbol",
      "tipo": "type",
      "cantidad": "shares",
      "precio": "price",
      "comision": ["fee", "tax"],
      "tipo_cambio": "fx_rate",
      "moneda_precio": "currency",
      "referencia": "transaction_id"
    },
    "valores_compra": ["BUY"],
    "valores_venta": ["SELL"],
    "invertir_tipo_cambio": false
  }
}
```

Trade Republic exporta las ventas con cantidad negativa; no importa, porque se toma el valor absoluto y el tipo sale de la columna `tipo`.
