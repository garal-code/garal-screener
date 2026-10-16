# Análisis de Markowitz: cálculo de la frontera eficiente

Este documento describe, paso a paso, cómo calcula `garal-screener` la frontera eficiente de Markowitz de una cartera. El objetivo es que cualquier lector pueda reproducir cada número de la respuesta de la API a partir de los precios de entrada.

Todo lo que se afirma aquí procede del código. Cada paso cita su ubicación como `ruta:línea` (referencias tomadas sobre `origin/develop` en `c6fd927`).

## Índice

1. [Objetivo y alcance](#1-objetivo-y-alcance)
2. [Endpoint y parámetros](#2-endpoint-y-parámetros)
3. [Selección de activos](#3-selección-de-activos)
4. [Datos de precios](#4-datos-de-precios)
5. [Rentabilidades mensuales](#5-rentabilidades-mensuales)
6. [Estadísticas por activo](#6-estadísticas-por-activo)
7. [Métricas de una cartera](#7-métricas-de-una-cartera)
8. [Cartera actual](#8-cartera-actual)
9. [Cartera de mínima varianza](#9-cartera-de-mínima-varianza)
10. [Frontera eficiente](#10-frontera-eficiente)
11. [Referencia analítica con ventas en corto (solo tests)](#11-referencia-analítica-con-ventas-en-corto-solo-tests)
12. [Respuesta de la API](#12-respuesta-de-la-api)
13. [Errores](#13-errores)
14. [Movimientos de rebalanceo (frontend)](#14-movimientos-de-rebalanceo-frontend)
15. [Limitaciones y supuestos](#15-limitaciones-y-supuestos)
16. [Ejemplo numérico](#16-ejemplo-numérico)
17. [Notas de mantenimiento](#17-notas-de-mantenimiento)

### Notación

| Símbolo | Significado |
|---|---|
| $N$ | Número de activos analizados (longitud de `tickers`) |
| $n$ | Número de observaciones mensuales comunes (`n_observaciones`) |
| $P_{i,t}$ | Cierre mensual ajustado del activo $i$ en el mes $t$, en su moneda nativa |
| $r_{i,t}$ | Rentabilidad simple mensual del activo $i$ en el mes $t$ |
| $\mu$ | Vector $N \times 1$ de rentabilidades esperadas (medias muestrales) |
| $\Sigma$ | Matriz $N \times N$ de covarianzas muestrales |
| $\sigma_i$ | Volatilidad (desviación típica muestral) del activo $i$ |
| $w$ | Vector de pesos de una cartera, en el orden de `tickers` |
| $\mathbf{1}$ | Vector de unos de dimensión $N$ |

---

## 1. Objetivo y alcance

El endpoint responde a la pregunta: **dadas las posiciones abiertas de una cartera, ¿qué combinaciones de esos mismos activos minimizan el riesgo para cada nivel de rentabilidad esperada, y dónde queda la cartera actual respecto a ellas?**

Convenios del cálculo (docstring de `app/services/markowitz.py:1-15`):

- **Rentabilidades simples mensuales, sin anualizar.** Todas las cifras de la respuesta (rentabilidad, volatilidad, varianza, covarianzas) son mensuales.
- **Rentabilidad esperada = media muestral; covarianza muestral** con denominador $n-1$.
- **Solo posiciones largas (long-only):** $0 \le w_i \le 1$ y $\sum_i w_i = 1$. No se permiten ventas en corto ni apalancamiento.
- **Resolución numérica** con `scipy.optimize.minimize` (método SLSQP).

### Relación con la hoja de referencia

El modelo de partida es la hoja de cálculo *"Markowitz - Cartera Eficiente.xlsx"*. La app replica sus convenios (rentabilidades simples, media muestral, covarianza $n-1$, serie mensual de la cartera equivalente a la columna "PORTFOLIO", ver `app/services/markowitz.py:281-294`), pero se aparta en un punto clave:

| | Hoja de referencia | `garal-screener` |
|---|---|---|
| Restricción de pesos | Solo $\sum w = 1$ (ventas en corto permitidas) | $\sum w = 1$ y $0 \le w \le 1$ |
| Método | Solución cerrada con $\Sigma^{-1}$ | Optimización numérica SLSQP |
| Uso de la solución cerrada | Resultado final | Solo como oráculo en los tests (sección 11) |

Cuando la solución cerrada de la hoja ya tiene todos los pesos positivos, ambas coinciden (`tests/test_markowitz.py:162-177`).

---

## 2. Endpoint y parámetros

```text
GET /api/v1/carteras/{cartera_id}/markowitz?anios=5&puntos=20
```

- Ruta: `app/routers/posiciones.py:402-406` (router con prefijo `/carteras`, `app/routers/posiciones.py:18`, montado bajo `/api/v1` en `main.py:55`).
- Modelo de respuesta: `schemas.FronteraEficienteCartera` (`app/schemas.py:304-324`).

| Parámetro | Tipo | Defecto | Rango | Significado | Fuente |
|---|---|---|---|---|---|
| `anios` | entero | `5` | `1` a `20` | Años de histórico mensual | `app/routers/posiciones.py:407` |
| `puntos` | entero | `20` (`markowitz.N_PUNTOS_FRONTERA`) | `2` a `100` | Rentabilidades objetivo de la frontera | `app/routers/posiciones.py:408-410`, `app/services/markowitz.py:30` |

Validación y acceso:

- Un valor fuera de rango lo rechaza FastAPI con **422** (`tests/test_api.py:831-841`).
- La cartera se resuelve con la dependencia `get_owned_cartera` (`app/auth/security.py:114-135`): si no existe **o pertenece a otro usuario** se devuelve **404** `"Cartera no encontrada"`, nunca 403, para no confirmar la existencia de carteras ajenas (`tests/test_api.py:843-853`). En el caso de cartera ajena no se llega a consultar el proveedor de precios.
- Sin autenticación: **401** (`tests/test_api.py:1221`).

---

## 3. Selección de activos

### 3.1. Posiciones abiertas valoradas en EUR

El endpoint reutiliza `resumen_cartera` (`app/routers/posiciones.py:426`), que:

1. Reconstruye cada posición con FIFO (`calculos.calcular_posicion_fifo`) y **descarta las posiciones cerradas** (`cantidad_actual <= 0`, `app/routers/posiciones.py:64-67`).
2. Obtiene el precio actual de cada ticker y el tipo de cambio actual en paralelo (`app/routers/posiciones.py:39-46`).
3. Calcula el valor de mercado en EUR (`app/services/calculos.py:194-196`):

$$
V = \operatorname{round}\left(\frac{P^{\text{actual}}_{\text{nativo}}}{\text{fx}} \cdot \text{cantidad},\ 2\right)
$$

donde $V$ es `valor_actual_eur`, $P^{\text{actual}}_{\text{nativo}}$ el precio actual en moneda nativa y `fx` sigue el convenio de Yahoo Finance `EUR{MONEDA}=X` (unidades de la moneda por 1 EUR) y vale `1.0` para EUR y para instrumentos sin moneda, que se tratan como EUR (`app/routers/posiciones.py:75-82`). Si una moneda extranjera no tiene tipo de cambio actual, **no se asume `1.0`**: la posición se trata como si no tuviera precio en EUR. Conserva `precio_actual_nativo` y `valor_actual_nativo` (mismo redondeo a 2 decimales), pero `valor_actual_eur`, `precio_actual_eur`, la plusvalía latente, la rentabilidad y `fx_actual` quedan a `null`. Además se registra un `logger.warning` por moneda y petición (`app/routers/posiciones.py:91-104`). Esa posición se excluye con `sin_tipo_cambio` (sección 3.2).

### 3.2. Motivos de exclusión y orden de evaluación

Cada posición abierta se evalúa en este orden; la primera condición que se cumple determina el motivo (`app/routers/posiciones.py:428-444`, `_motivo_sin_valor_eur` en `528-540` y `481-490`). Los motivos están tipados en `MotivoExclusion` (`app/schemas.py:219-232`).

| Orden | Motivo | Condición | Momento |
|---|---|---|---|
| 1 | `sin_ticker` | El instrumento no tiene ticker. En `ActivoExcluido.ticker` se informa el **ISIN** (`app/schemas.py:236`). | Antes de pedir precios |
| 2 | `sin_precio_actual` | No hay precio actual nativo válido: `valor_actual_nativo` es `None`, no finito (NaN, ±inf) o $\le 0$. También cuando, con tipo de cambio, `valor_actual_eur` no es finito o es $\le 0$ (`_es_valor_positivo`, `app/routers/posiciones.py:523-525`). | Antes de pedir precios |
| 3 | `sin_tipo_cambio` | Hay precio nativo válido, pero no hay tipo de cambio actual para su moneda (`fx_actual` es `null`): el valor EUR es desconocido (sección 3.1). | Antes de pedir precios |
| 4 | `historico_insuficiente` | El proveedor devolvió precios, pero el activo tiene menos de 12 rentabilidades mensuales propias (`separar_historicos_cortos`, sección 5.3). | Tras descargar precios |
| 5 | `sin_historico` | El proveedor no devolvió ningún cierre mensual utilizable para ese ticker. | Tras descargar precios |

Si dos posiciones comparten ticker, sus valores EUR **se suman** en `valor_por_ticker` (`app/routers/posiciones.py:441-444`) y se tratan como un único activo.

### 3.3. Mínimo de activos

- Si tras los motivos 1 a 3 quedan **menos de 2 tickers valorados**, se devuelve **400** sin consultar el histórico (`app/routers/posiciones.py:446-455`, `tests/test_api.py:689-698`).
- Si tras los motivos 4 y 5 quedan menos de 2 activos con histórico suficiente, el cálculo lanza `DatosInsuficientesError` y se devuelve **400** (`app/services/markowitz.py:361-366`, `tests/test_api.py:700-709`).

### 3.4. `peso_excluido`

Fracción del valor EUR de la cartera que queda fuera del análisis (`app/routers/posiciones.py:504-505` y `518`):

$$
\text{peso excluido} = \frac{V_{\text{total}} - V_{\text{incluido}}}{V_{\text{total}}},
\qquad
V_{\text{total}} = \sum_{k \in \text{valorados}} V_k,
\qquad
V_{\text{incluido}} = \sum_{k \in \text{tickers}} V_k
$$

- $V_{\text{total}}$ solo suma los tickers que superaron los motivos 1 a 3. Una posición `sin_ticker`, `sin_precio_actual` o `sin_tipo_cambio` **no tiene valor EUR conocido y no cuenta** (comentario en `app/schemas.py:320-322`; tests `tests/test_api.py:750-769` y `771-799`).
- Los excluidos por `historico_insuficiente` o `sin_historico` sí cuentan en el numerador. Ejemplo del test `tests/test_api.py:660-687`: 2000 EUR excluidos sobre 6000 EUR $\Rightarrow$ `peso_excluido` $= 1/3$.

---

## 4. Datos de precios

### 4.1. Ventana temporal

```python
fecha_fin = precios.fin_ultimo_mes_completo()
fecha_inicio = date(fecha_fin.year - anios, fecha_fin.month, 1)
```

(`app/routers/posiciones.py:457-459`)

- `fin_ultimo_mes_completo` devuelve el **último día del último mes cerrado**: `hoy.replace(day=1) - 1 día` (`app/services/precios.py:299-304`). El mes en curso **nunca** cuenta, ni siquiera el día 1. Ejemplos de `tests/test_precios.py:545-557`:

| Hoy | `fecha_fin` |
|---|---|
| 2026-10-04 | 2026-09-30 |
| 2026-03-01 | 2026-02-28 |
| 2026-01-15 | 2025-12-31 |

- `fecha_inicio` es el día 1 del **mismo mes** de `fecha_fin`, `anios` años antes. Con meses completos esto da $12 \cdot \text{anios} + 1$ cierres y, por tanto, como máximo $12 \cdot \text{anios}$ rentabilidades. Ejemplo (`tests/test_api.py:552-561`): hoy = 2026-10-04, `anios=5` $\Rightarrow$ ventana `[2021-09-01, 2026-09-30]`, 61 cierres, 60 observaciones.

### 4.2. Descarga

`obtener_precios_mensuales_batch(tickers, fecha_inicio, fecha_fin)` (`app/services/precios.py:307-326`):

1. Elimina tickers vacíos y duplicados conservando el orden (`app/services/precios.py:320`). Si no queda ninguno, devuelve `{}` sin llamar al proveedor.
2. Ejecuta la descarga síncrona `_fetch_precios_mensuales` en un executor para no bloquear el event loop.

`_fetch_precios_mensuales` (`app/services/precios.py:329-390`) hace **una única llamada** a yfinance para todos los tickers:

```python
yf.download(
    tickers,
    start=str(fecha_inicio),
    end=str(fecha_fin + timedelta(days=1)),
    interval="1mo",
    auto_adjust=True,
    progress=False,
    threads=True,
)
```

- `interval="1mo"`: una fila por mes.
- `auto_adjust=True`: la columna `Close` es el **cierre ajustado por dividendos y splits** (docstring `app/services/precios.py:311-312`).
- `end = fecha_fin + 1 día` para que `fecha_fin` quede dentro del rango pedido.
- Los precios quedan en la **moneda nativa** de cada instrumento; no se convierten a EUR.

### 4.3. Normalización y filtrado

Para cada ticker presente en las columnas de `Close` (`app/services/precios.py:364-384`):

1. Si la descarga es de un solo ticker con columnas planas, `Close` es una `Series` y se convierte en un `DataFrame` con el ticker como columna (`app/services/precios.py:365-367`).
2. Se descartan los **NaN** (`dropna()`) y se ordena por fecha.
3. Cada fecha se **normaliza al día 1 de su mes**: `date(año, mes, 1)`. Así se alinean mercados con calendarios distintos.
4. Si hay dos filas del mismo mes, **prevalece la más reciente** (se recorre en orden y se sobrescribe; `tests/test_precios.py:457-473`).
5. Solo se conserva un mes si `mes < mes_en_curso` **y** `mes <= fecha_fin` (`app/services/precios.py:370` y `381`). El mes en curso nunca se devuelve aunque yfinance lo incluya (`tests/test_precios.py:509-527`).
6. Los tickers que se quedan sin ningún mes se omiten del resultado.

Salida: `{ticker: [(primer_día_del_mes, cierre), ...]}` ordenado por fecha.

### 4.4. Fallo del proveedor frente a "sin datos"

Se lanza `ProveedorPreciosError` (`app/services/precios.py:286-291`) en tres casos:

| Caso | Fuente |
|---|---|
| `yf.download` lanza una excepción (p. ej. red) | `app/services/precios.py:354-357` |
| El resultado es `None`, vacío o no tiene columna `Close` | `app/services/precios.py:359-362` |
| Ningún ticker trae **ni un solo cierre** no NaN (yfinance a menudo registra el fallo y devuelve columnas vacías o todo NaN en lugar de lanzar) | `app/services/precios.py:371`, `378`, `386-389` |

La bandera `hay_datos` se activa con cualquier cierre no NaN, **antes** de filtrar por mes. Por eso un valor recién listado que solo tiene el mes en curso **no** es fallo del proveedor: devuelve `{}` para ese ticker (`tests/test_precios.py:529-542`) y el endpoint lo clasifica como `sin_historico`.

En el endpoint, `ProveedorPreciosError` se traduce en **503** (sección 13).

---

## 5. Rentabilidades mensuales

### 5.1. Fórmula

`calcular_rentabilidades_mensuales` (`app/services/markowitz.py:118-152`). Para cada activo:

1. Se descartan los precios no finitos o no positivos: solo $P > 0$ y finito (`app/services/markowitz.py:132`).
2. Se ordenan por fecha y se toman pares de cierres **consecutivos en la serie**.
3. Solo se calcula la rentabilidad si los dos cierres son de **meses naturales consecutivos** (`app/services/markowitz.py:138`), usando el índice de mes $m(\text{fecha}) = 12 \cdot \text{año} + \text{mes}$ (`_indice_mes`, `app/services/markowitz.py:352-353`):

$$
r_{i,t} = \frac{P_{i,t}}{P_{i,t-1}} - 1
\qquad \text{solo si } m(t) - m(t-1) = 1
$$

La rentabilidad se fecha en el mes del cierre final $t$ (día 1). **Nunca se encadenan dos meses en uno**: si falta el precio de marzo, no existe rentabilidad de marzo (feb→mar) ni de abril (mar→abr); no se calcula una rentabilidad feb→abr (`tests/test_markowitz.py:87-101`).

### 5.2. Alineación entre activos

Tras calcular las rentabilidades de cada activo (`app/services/markowitz.py:143-152`):

- Los activos **sin ninguna rentabilidad** se omiten.
- Se conservan solo los meses con rentabilidad **para todos** los activos (intersección de fechas). Un mes ausente en cualquier activo se descarta para todos.
- Resultado `RentabilidadesMensuales` (`app/services/markowitz.py:44-50`): `tickers`, `fechas` (ordenadas) y `matriz` $R$ de dimensión $n \times N$ (filas = meses, columnas = activos), con $R_{t,i} = r_{i,t}$.

El orden de `tickers` es el orden de inserción del diccionario de series, que sigue el orden de las posiciones del resumen (no se ordena alfabéticamente).

### 5.3. Exclusión previa de históricos cortos

Antes de alinear, el endpoint llama a `separar_historicos_cortos` (`app/services/markowitz.py:224-246`, `app/routers/posiciones.py:479-481`). Para cada serie cuenta sus **rentabilidades propias** (calculadas con la regla de 5.1, es decir, solo meses consecutivos) y:

- con al menos `MIN_OBSERVACIONES = 12` (`app/services/markowitz.py:29`) $\Rightarrow$ la serie pasa al cálculo;
- con menos $\Rightarrow$ el ticker se marca `historico_insuficiente`.

Motivo: sin este paso, al recortar a los meses comunes, un único activo recién listado dejaría a toda la cartera sin observaciones. Los huecos no cuentan: 13 precios con un mes intermedio ausente dan solo 11 rentabilidades y el activo se excluye (`tests/test_markowitz.py:306-314`).

### 5.4. Mínimo de observaciones comunes

Aunque cada activo tenga 12 rentabilidades propias, tras la intersección puede haber menos meses comunes. `calcular_estadisticas_desde_rentabilidades` exige (`app/services/markowitz.py:170-181`):

- al menos 2 activos, y
- $n \ge 12$ meses comunes.

Si no, lanza `DatosInsuficientesError` $\Rightarrow$ **400** (`tests/test_api.py:711-722`). Con menos observaciones que activos la covarianza es directamente singular, y con menos de 12 se considera poco fiable (comentario en `app/services/markowitz.py:27-28`).

---

## 6. Estadísticas por activo

`calcular_estadisticas_desde_rentabilidades` (`app/services/markowitz.py:170-189`) produce `EstadisticasActivos` (`app/services/markowitz.py:53-91`).

### 6.1. Rentabilidad esperada (media muestral)

`rentabilidades.matriz.mean(axis=0)` (`app/services/markowitz.py:184`):

$$
\mu_i = \frac{1}{n} \sum_{t=1}^{n} r_{i,t}
$$

### 6.2. Matriz de covarianzas (muestral, $n-1$)

`np.cov(matriz, rowvar=False, ddof=1)`, envuelta en `np.atleast_2d` (`app/services/markowitz.py:185-187`):

$$
\Sigma_{ij} = \frac{1}{n-1} \sum_{t=1}^{n} \left(r_{i,t} - \mu_i\right)\left(r_{j,t} - \mu_j\right)
$$

El denominador es $n-1$ (muestral), no $n$ (poblacional) (`tests/test_markowitz.py:105-123`).

### 6.3. Varianzas, volatilidades y correlaciones

| Magnitud | Fórmula | Código |
|---|---|---|
| Varianza | $\sigma_i^2 = \Sigma_{ii}$ | `varianzas`, `app/services/markowitz.py:62-66` |
| Volatilidad | $\sigma_i = \sqrt{\max(\Sigma_{ii},\ 0)}$ | `volatilidades`, `app/services/markowitz.py:68-72` |
| Correlación | $\rho_{ij} = \dfrac{\Sigma_{ij}}{\sigma_i \, \sigma_j}$ | `matriz_correlaciones`, `app/services/markowitz.py:74-91` |

Tratamiento de varianza cero en la correlación: si $\sigma_i \sigma_j = 0$ (un activo sin variación), $\rho_{ij}$ se fija en $0$ en lugar de NaN; la diagonal se fuerza siempre a $1$ (`tests/test_markowitz.py:380-385`). Con varianzas positivas coincide con `np.corrcoef` (`tests/test_markowitz.py:363-378`).

### 6.4. Validación de la covarianza (criterio de singularidad)

Antes de optimizar, `_validar_estadisticas` (`app/services/markowitz.py:369-382`) exige:

1. Al menos 2 activos.
2. Todos los valores de $\Sigma$ y $\mu$ finitos; si no, `DatosInsuficientesError("Las estadísticas contienen valores no finitos.")`.
3. Covarianza **no singular**: con los autovalores ordenados $\lambda_{\min} \le \dots \le \lambda_{\max}$ (`np.linalg.eigvalsh`), es singular si

$$
\lambda_{\max} \le 0
\quad\text{o}\quad
\lambda_{\min} \le 10^{-10} \cdot \lambda_{\max}
$$

(`_TOLERANCIA_SINGULAR = 1e-10`, `app/services/markowitz.py:37`). Casos típicos: un activo con precio constante, o dos activos linealmente dependientes (p. ej. uno con precios exactamente el doble del otro) (`tests/test_markowitz.py:192-202`). El error se traduce en **400**.

---

## 7. Métricas de una cartera

`estadisticas_cartera` (`app/services/markowitz.py:252-258`) y `_punto` (`app/services/markowitz.py:449-457`). Para un vector de pesos $w$:

$$
R_p = w^{\top}\mu = \sum_i w_i \mu_i
$$

$$
\sigma_p^2 = w^{\top} \Sigma\, w = \sum_i \sum_j w_i w_j \Sigma_{ij}
$$

$$
\sigma_p = \sqrt{\max\left(w^{\top} \Sigma\, w,\ 0\right)}
$$

El `max(·, 0)` protege de varianzas ligeramente negativas por redondeo. La varianza que se expone (`PuntoCartera.varianza`, `app/services/markowitz.py:102-104`) es $\sigma_p^2$ recalculada como `volatilidad**2`.

### Serie mensual de una cartera

`rentabilidades_cartera` (`app/services/markowitz.py:281-294`) es la columna "PORTFOLIO" de la hoja: para cada mes común $t$,

$$
r_{p,t} = \sum_i w_i \, r_{i,t}
\qquad\Longleftrightarrow\qquad
r_p = R\, w
$$

Los tickers ausentes de los pesos valen 0 y los pesos se usan **tal cual**, sin renormalizar. Con pesos que suman 1 se cumple (`tests/test_markowitz.py:396-407`):

$$
\overline{r_p} = w^{\top}\mu,
\qquad
\operatorname{Var}_{n-1}(r_p) = w^{\top} \Sigma\, w
$$

---

## 8. Cartera actual

En `_calcular_frontera` (`app/routers/posiciones.py:565-567`) la cartera actual se evalúa con `evaluar_cartera` (`app/services/markowitz.py:261-278`), pasando como "pesos" los **valores EUR** de los tickers analizados:

$$
w_i^{\text{actual}} = \frac{V_i}{\sum_{j \in \text{tickers}} V_j}
$$

- La suma del denominador es solo sobre los activos **incluidos** en el análisis: los pesos se **renormalizan** a 1 sobre ellos (`tests/test_api.py:660-687`).
- $V_i$ siempre es un valor EUR real: una posición sin tipo de cambio actual queda fuera (`sin_tipo_cambio`, sección 3.2) en lugar de entrar con su valor nativo tomado como EUR. Con tipo de cambio, $V_i = P^{\text{actual}}_{\text{nativo}} / \text{fx} \cdot \text{cantidad}$ (`tests/test_api.py:801-815`).
- `evaluar_cartera` rechaza pesos negativos o una suma $\le 0$ con `ValueError` (no puede ocurrir en el endpoint, porque todos los valores incluidos son $> 0$).
- La serie mensual `detalle.cartera_actual.rentabilidades` es $R\,w^{\text{actual}}$ con los pesos ya normalizados (`app/routers/posiciones.py:578-580`).

**Monedas.** Los pesos se calculan con valores en **EUR**, pero las rentabilidades $r_{i,t}$ se calculan con precios en la **moneda nativa** de cada activo. Es decir, $\mu$ y $\Sigma$ no incluyen el efecto del tipo de cambio para un inversor en EUR (sección 15).

---

## 9. Cartera de mínima varianza

### 9.1. Problema

`calcular_cartera_minima_varianza` (`app/services/markowitz.py:297-302`) y `_pesos_minima_varianza` (`app/services/markowitz.py:385-394`):

$$
\begin{aligned}
\min_{w}\quad & w^{\top} \Sigma\, w \\
\text{s.a.}\quad & \mathbf{1}^{\top} w = 1 \\
& 0 \le w_i \le 1 \quad \forall i
\end{aligned}
$$

### 9.2. Resolución numérica (`_minimizar_varianza`)

Implementado en `app/services/markowitz.py:397-446`:

| Elemento | Valor | Línea |
|---|---|---|
| Método | `scipy.optimize.minimize(..., method="SLSQP")` | `422-430` |
| Escala del objetivo | $s = 1 / \overline{\operatorname{diag}(\Sigma)}$ (inversa de la varianza media de los activos) | `411` |
| Función objetivo | $f(w) = s \cdot w^{\top}\Sigma w$ | `423` |
| Jacobiano analítico del objetivo | $\nabla f(w) = 2 s \, \Sigma w$ | `425` |
| Restricción de igualdad | $\mathbf{1}^{\top} w - 1 = 0$, jacobiano $\mathbf{1}$ | `413-415` |
| Cotas | $(0,\ 1)$ para cada peso | `427` |
| Punto inicial | Pesos iguales $w_i = 1/N$ | `387-389` |
| Opciones | `ftol = 1e-12`, `maxiter = 500` | `429` |

El escalado lleva la varianza a orden $O(1)$ para que la tolerancia `ftol` de SLSQP sea significativa (las varianzas mensuales son del orden de $10^{-3}$). No cambia el óptimo.

### 9.3. Comprobación y limpieza del resultado

Tras la optimización (`app/services/markowitz.py:431-446`), la solución se **rechaza** (devuelve `None`) si:

1. `resultado.success` es falso;
2. $\left\lvert \mathbf{1}^{\top} w - 1 \right\rvert > 10^{-7}$ (`_TOLERANCIA_RESTRICCION`, `app/services/markowitz.py:35`);
3. (solo con rentabilidad objetivo) $\left\lvert w^{\top}\mu - \text{objetivo} \right\rvert > 10^{-7}$;
4. $\min_i w_i < -10^{-7}$.

Si se acepta, se limpia:

$$
\tilde w_i =
\begin{cases}
0 & \text{si } w_i < 10^{-9} \\
\min(w_i,\ 1) & \text{en otro caso}
\end{cases}
\qquad\qquad
w_i^{\text{final}} = \frac{\tilde w_i}{\sum_j \tilde w_j}
$$

(`_UMBRAL_PESO = 1e-9`, `app/services/markowitz.py:33`). Los pesos por debajo del umbral, incluidos los ligeramente negativos, se consideran ruido numérico. La rentabilidad y la volatilidad del punto se recalculan con $w^{\text{final}}$ (sección 7).

### 9.4. Si no converge

Para la cartera de mínima varianza, un resultado `None` lanza `DatosInsuficientesError("No se pudo calcular la cartera de mínima varianza.")` (`app/services/markowitz.py:390-393`) $\Rightarrow$ **400**. Antes de optimizar se ejecuta `_validar_estadisticas` (sección 6.4).

Comprobado en tests: sin ventas en corto, dos activos muy correlacionados donde la solución cerrada vendería en corto el más volátil dan $w = (1, 0)$ (`tests/test_markowitz.py:179-190`).

---

## 10. Frontera eficiente

`calcular_frontera_eficiente(estadisticas, n_puntos)` (`app/services/markowitz.py:305-346`).

### 10.1. Pasos

1. Si `n_puntos < 2` $\Rightarrow$ `ValueError` (`app/services/markowitz.py:317-318`). En la API no ocurre porque `puntos >= 2` se valida antes.
2. `_validar_estadisticas` (sección 6.4).
3. Se calcula la cartera de mínima varianza $w^{\text{mv}}$, con rentabilidad $R_{\text{mv}}$ (sección 9).
4. Se identifica el **mejor activo** $k = \arg\max_i \mu_i$ (`np.argmax`: en caso de empate, el primero en el orden de `tickers`) y $R_{\max} = \mu_k$ (`app/services/markowitz.py:325-326`). Sin ventas en corto ni apalancamiento, $R_{\max}$ es la máxima rentabilidad alcanzable.
5. **Caso degenerado:** si $R_{\max} - R_{\text{mv}} \le 10^{-7}$, la frontera es un único punto, la propia cartera de mínima varianza (`app/services/markowitz.py:327-329`, `tests/test_markowitz.py:253-264`).
6. Rentabilidades objetivo equiespaciadas (`np.linspace`, `app/services/markowitz.py:335`):

$$
R^{\ast}_k = R_{\text{mv}} + \frac{k}{n_{\text{puntos}} - 1}\left(R_{\max} - R_{\text{mv}}\right),
\qquad k = 0, \dots, n_{\text{puntos}} - 1
$$

7. El punto $k = 0$ es la cartera de mínima varianza (no se vuelve a optimizar). Para cada $k \ge 1$ se resuelve (`app/services/markowitz.py:336-345`):

$$
\begin{aligned}
\min_{w}\quad & w^{\top} \Sigma\, w \\
\text{s.a.}\quad & \mathbf{1}^{\top} w = 1 \\
& \mu^{\top} w = R^{\ast}_k \\
& 0 \le w_i \le 1 \quad \forall i
\end{aligned}
$$

con el mismo `_minimizar_varianza` de la sección 9.2, más la restricción de igualdad $\mu^{\top} w - R^{\ast}_k = 0$ con jacobiano $\mu$ (`app/services/markowitz.py:416-420`).

### 10.2. Arranque en caliente (punto inicial factible)

Para cada objetivo, el punto inicial es la mezcla lineal entre la cartera de mínima varianza y la cartera 100 % en el mejor activo $e_k$ (`app/services/markowitz.py:337-340`):

$$
\alpha = \frac{R^{\ast}_k - R_{\text{mv}}}{R_{\max} - R_{\text{mv}}},
\qquad
w^{(0)} = (1 - \alpha)\, w^{\text{mv}} + \alpha\, e_k
$$

Este punto cumple exactamente las dos igualdades ($\mathbf{1}^{\top}w^{(0)} = 1$ y $\mu^{\top}w^{(0)} = R^{\ast}_k$) y las cotas, porque $\alpha \in [0, 1]$.

### 10.3. Puntos no resueltos

Si `_minimizar_varianza` devuelve `None` para un objetivo (no converge o incumple una restricción, sección 9.3), **ese punto se omite** sin error (`app/services/markowitz.py:344-345`). Por eso la API puede devolver **menos** puntos que `puntos` (`tests/test_api.py:572`).

### 10.4. Propiedades comprobadas en tests

Con los datos de la hoja (`tests/test_markowitz.py:208-237`):

- Todos los pesos en $[0, 1]$ y sumando 1 (tolerancia $10^{-6}$).
- Rentabilidad estrictamente creciente y volatilidad no decreciente a lo largo de la frontera.
- El primer punto es el de mínima varianza y es el menos volátil.
- El último punto es 100 % el mejor activo (NVDA): rentabilidad $0{,}043$ y volatilidad $\sqrt{0{,}001427667}$.

El campo `rentabilidad` de cada punto es $w^{\top}\mu$ recalculado con los pesos finales, que coincide con el objetivo $R^{\ast}_k$ salvo la tolerancia de $10^{-7}$ y la limpieza de la sección 9.3.

---

## 11. Referencia analítica con ventas en corto (solo tests)

La hoja resuelve el problema **sin** cotas ($w$ puede ser negativo) con la solución cerrada. En el código **solo** se usa como oráculo en `tests/test_markowitz.py` (`TestCarteraMinimaVarianza`, líneas 152-177); **no se expone en la API**. Por decisión de producto, el análisis nunca contempla ventas en corto, y la referencia que existió en la respuesta se eliminó.

Definiciones:

$$
A = \mu^{\top} \Sigma^{-1} \mu,
\qquad
B = \mathbf{1}^{\top} \Sigma^{-1} \mu,
\qquad
D = \mathbf{1}^{\top} \Sigma^{-1} \mathbf{1}
$$

Cartera de mínima varianza sin restricciones de signo:

$$
w_{\min} = \frac{\Sigma^{-1}\mathbf{1}}{D},
\qquad
\sigma^2_{\min} = \frac{1}{D},
\qquad
R_{\min} = \frac{B}{D}
$$

Datos de la hoja usados en los tests (`tests/test_markowitz.py:29-41`), en el orden `AMZN, AAPL, NVDA, MSFT, TSLA`:

$$
\mu = \begin{pmatrix} 0{,}013 & 0{,}019 & 0{,}043 & 0{,}010 & 0{,}020 \end{pmatrix}^{\top}
$$

| $\Sigma$ | AMZN | AAPL | NVDA | MSFT | TSLA |
|---|---|---|---|---|---|
| **AMZN** | 0.002986616 | 0.000259343 | 0.000381061 | -0.000186111 | 0.000130808 |
| **AAPL** | 0.000259343 | 0.002357364 | 0.00000748485 | 0.000350333 | 0.0000986465 |
| **NVDA** | 0.000381061 | 0.00000748485 | 0.001427667 | 0.0000379495 | 0.000110242 |
| **MSFT** | -0.000186111 | 0.000350333 | 0.0000379495 | 0.002015061 | -0.000124424 |
| **TSLA** | 0.000130808 | 0.0000986465 | 0.000110242 | -0.000124424 | 0.003747111 |

Valores de la hoja comprobados por los tests:

| Magnitud | Valor esperado | Tolerancia | Test |
|---|---|---|---|
| $D$ | 1881.05 | abs 0.01 | `tests/test_markowitz.py:158` |
| $\sigma^2_{\min} = 1/D$ | 0.000531617 | rel $10^{-5}$ | `tests/test_markowitz.py:159` |
| $R_{\min} = B/D$ | 0.0237 | abs $10^{-4}$ | `tests/test_markowitz.py:160` |
| $w_{\min}$ | (0.1325, 0.1674, 0.3193, 0.2491, 0.1317) | abs $10^{-3}$ | `tests/test_markowitz.py:172-174` |

Como en esta hoja todos los pesos de $w_{\min}$ son positivos, la solución long-only coincide con la cerrada (`tests/test_markowitz.py:162-177`). $A$ no se usa en ningún test; con estos datos vale $A \approx 1{,}53509$ (calculado con numpy sobre la fixture, no asertado).

---

## 12. Respuesta de la API

Construida en `app/routers/posiciones.py:507-520` y `_detalle_calculo_out` (`app/routers/posiciones.py:593-613`). Todas las cifras son mensuales, como fracción (0.01 = 1 %), sin redondear. Notación: $N$ = `len(tickers)`, $n$ = `n_observaciones`.

### 12.1. `FronteraEficienteCartera` (`app/schemas.py:304-324`)

| Campo | Tipo / dimensión | Significado |
|---|---|---|
| `fecha_inicio` | fecha | Día 1 del mes inicial de la ventana pedida (sección 4.1) |
| `fecha_fin` | fecha | Último día del último mes cerrado |
| `frecuencia` | `"mensual"` | Constante |
| `n_observaciones` | entero $n$ | Meses comunes con rentabilidad para todos los activos |
| `tickers` | lista $N$ | Activos analizados; fija el orden de todos los vectores y matrices |
| `rentabilidades_esperadas` | lista $N$ | $\mu$ |
| `matriz_covarianzas` | $N \times N$ | $\Sigma$ (muestral $n-1$) |
| `frontera` | lista de `PuntoCarteraOut` (entre 1 y `puntos`) | Ordenada por rentabilidad creciente; el primero es el de mínima varianza |
| `cartera_minima_varianza` | `PuntoCarteraOut` | Sección 9 |
| `cartera_actual` | `PuntoCarteraOut` | Sección 8 |
| `excluidos` | lista de `ActivoExcluido` | `{ticker, motivo}`; sección 3.2 |
| `peso_excluido` | número en $[0, 1]$ | Sección 3.4 |
| `detalle` | `DetalleCalculoFrontera` | Datos intermedios para reproducir el cálculo |

### 12.2. `PuntoCarteraOut` (`app/schemas.py:203-216`)

| Campo | Significado |
|---|---|
| `rentabilidad` | $w^{\top}\mu$ |
| `volatilidad` | $\sqrt{\max(w^{\top}\Sigma w,\ 0)}$ |
| `varianza` | `volatilidad²` |
| `pesos` | `{ticker: peso}` para **todos** los `tickers` (incluidos los de peso 0); suman 1 y están en $[0, 1]$ |

### 12.3. `detalle` (`DetalleCalculoFrontera`, `app/schemas.py:296-301`)

**`detalle.datos`** (`DatosEntradaFrontera`, `app/schemas.py:241-264`), construido por `alinear_precios_mensuales` (`app/services/markowitz.py:192-221`):

| Campo | Dimensión | Significado |
|---|---|---|
| `fechas_precios` | $m$ | Meses (día 1) de los cierres usados: cada mes de `fechas` **y su mes anterior**, ordenados y sin repetir |
| `precios` | $m \times N$ | Cierre ajustado mensual en moneda nativa; filas = `fechas_precios`, columnas = `tickers` |
| `fechas` | $n$ | Mes (día 1) de cada rentabilidad |
| `rentabilidades` | $n \times N$ | $R$; filas = `fechas`, columnas = `tickers` |

Con meses consecutivos, $m = n + 1$, `fechas_precios[0] = fecha_inicio`, `fechas_precios[1:] == fechas` y `rentabilidades == precios[1:] / precios[:-1] - 1` (`tests/test_api.py:601-619`). Con huecos, $m > n + 1$ y esa igualdad fila a fila deja de cumplirse; cada rentabilidad sale de su mes y del mes anterior (`tests/test_markowitz.py:337-360`).

**`detalle.estadisticas`** (`EstadisticasActivosOut`, `app/schemas.py:267-278`):

| Campo | Dimensión | Significado |
|---|---|---|
| `varianzas` | $N$ | $\Sigma_{ii}$ |
| `volatilidades` | $N$ | $\sigma_i$ |
| `matriz_correlaciones` | $N \times N$ | $\rho_{ij}$ (sección 6.3) |

**`detalle.cartera_actual`** (`DetalleCarteraActual`, `app/schemas.py:281-293`):

| Campo | Dimensión | Significado |
|---|---|---|
| `valores_eur` | `{ticker: EUR}` para los $N$ tickers | Valor de mercado EUR de cada activo incluido; origen de los pesos actuales |
| `rentabilidades` | $n$ | $R\,w^{\text{actual}}$; su media es `cartera_actual.rentabilidad` |

---

## 13. Errores

| Código | Causa | Origen |
|---|---|---|
| **400** | Menos de 2 posiciones abiertas con ticker y valor EUR válido (no se consulta el histórico) | `app/routers/posiciones.py:446-455` |
| **400** | Menos de 2 activos con histórico suficiente | `app/services/markowitz.py:361-366` |
| **400** | Menos de 12 meses comunes a todos los activos | `app/services/markowitz.py:176-181` |
| **400** | Estadísticas no finitas | `app/services/markowitz.py:373-376` |
| **400** | Covarianza singular (activo sin variación o activos linealmente dependientes) | `app/services/markowitz.py:377-382` |
| **400** | SLSQP no resuelve la cartera de mínima varianza | `app/services/markowitz.py:390-393` |
| **401** | Petición sin autenticar | `tests/test_api.py:1221` |
| **404** | Cartera inexistente o de otro usuario | `app/auth/security.py:124-134` |
| **422** | `anios` o `puntos` fuera de rango (validación de FastAPI) | `app/routers/posiciones.py:407-410` |
| **503** | `ProveedorPreciosError` (sección 4.4) | `app/routers/posiciones.py:464-477` |

Formato del `detail` de los 400 (`_detalle_datos_insuficientes`, `app/routers/posiciones.py:616-625`):

```text
No hay datos suficientes para calcular la frontera eficiente. <motivo> Excluidos: <ticker> (<motivo>), <ticker> (<motivo>).
```

La parte `Excluidos: ...` solo aparece si hay excluidos en ese momento.

En el 503, el `detail` es un mensaje genérico (`"No se pudieron obtener los precios históricos del proveedor de precios. Inténtalo de nuevo más tarde."`) que no filtra el error interno; el error original se registra con `logger.warning` junto con el id de la cartera (`app/routers/posiciones.py:465-470`, `tests/test_api.py:732-748`).

El cálculo (SLSQP) se ejecuta con `run_in_threadpool` para no bloquear el event loop (`app/routers/posiciones.py:492-496`, `tests/test_api.py:817-829`).

---

## 14. Movimientos de rebalanceo (frontend)

Los movimientos **no** los calcula este backend: los calcula el frontend `investment-portfolio-ui` en `lib/rebalanceo.ts` a partir de la respuesta de la sección 12 (verificado en la rama `feat/analisis-05-metodologia` de ese repositorio).

### 14.1. Entradas

`computeRebalancingMoves(tickers, pesosActuales, pesosObjetivo, valoresEur)` (`lib/rebalanceo.ts:22-38`), invocado en `components/analisis/MovimientosTable.tsx:51` con:

- `tickers` de la respuesta;
- `pesosActuales` = `cartera_actual.pesos`;
- `pesosObjetivo` = `pesos` del punto de la frontera elegido;
- `valoresEur` = `detalle.cartera_actual.valores_eur`.

### 14.2. Fórmulas

$$
T = \sum_{i \in \text{tickers}} V_i
$$

Para cada ticker, en el orden de `tickers` (un peso ausente vale 0), con $I_i$ = `importeEur`:

$$
d_i = w_i^{\text{objetivo}} - w_i^{\text{actual}},
\qquad
I_i = d_i \cdot T
$$

$$
\text{acción}_i =
\begin{cases}
\text{mantener} & \text{si } \lvert d_i \rvert < 0{,}005 \\
\text{comprar} & \text{si } d_i \ge 0{,}005 \\
\text{vender} & \text{si } d_i \le -0{,}005
\end{cases}
$$

- `HOLD_THRESHOLD = 0.005` (0,5 puntos porcentuales, `lib/rebalanceo.ts:5`). En "mantener" el importe exacto se conserva, solo cambia la etiqueta.
- **Autofinanciado:** como ambos conjuntos de pesos suman 1, $\sum_i I_i = T \left(\sum_i w_i^{\text{objetivo}} - \sum_i w_i^{\text{actual}}\right) \approx 0$.
- **Las posiciones excluidas no se mueven:** $T$ solo suma los tickers analizados.
- **No se calcula número de acciones**, solo importes en EUR.

### 14.3. Punto objetivo por defecto

`defaultFrontierIndex(frontera, actual)` (`lib/rebalanceo.ts:42-57`), usado en `app/analisis/page.tsx:52-56`: el punto de la frontera cuya **volatilidad es más cercana** a la de la cartera actual,

$$
k^{\ast} = \arg\min_k \left\lvert \sigma_k - \sigma_{\text{actual}} \right\rvert
$$

En empate exacto de distancia gana el de **mayor rentabilidad**. Con frontera vacía devuelve `null`. El usuario puede elegir otro punto; al cambiar la respuesta (otro horizonte, recarga) se vuelve al punto por defecto.

---

## 15. Limitaciones y supuestos

- **Datos históricos como estimación del futuro.** $\mu$ y $\Sigma$ son medias y covarianzas muestrales del pasado. La media mensual tiene un error de estimación alto (del orden de $\sigma_i / \sqrt{n}$), y el optimizador tiende a concentrarse en los activos con mayor $\mu$ estimada; la frontera es sensible a ese ruido.
- **Sin anualizar.** Las cifras son mensuales. El código no aplica ningún factor ($\times 12$ o $\times \sqrt{12}$).
- **Rentabilidades simples**, no logarítmicas (`app/services/markowitz.py:134`).
- **Cierre ajustado** por dividendos y splits (`auto_adjust=True`): la rentabilidad incluye dividendos reinvertidos implícitamente.
- **Moneda nativa.** Las rentabilidades no incluyen la variación del tipo de cambio frente al EUR; los pesos sí están en EUR.
- **Sin costes de transacción, impuestos ni restricciones de lote**; los importes de rebalanceo son teóricos.
- **Solo largos**: $0 \le w_i \le 1$, sin ventas en corto, sin apalancamiento ni activo libre de riesgo (no hay línea del mercado de capitales ni cartera tangente).
- **Universo cerrado**: solo los activos que ya están en la cartera y superan los filtros.
- **Ventana común**: el activo con el histórico más corto (por encima de 12 meses) recorta la ventana de todos, y cualquier hueco elimina ese mes para todos.
- **Mes en curso excluido**: los datos llegan como mucho al último mes cerrado.
- **Fallo parcial del proveedor**: si yfinance falla solo para algunos tickers, esos tickers se clasifican como `sin_historico` y no como fallo del proveedor (sección 17).
- **Precisión numérica**: los pesos y restricciones se cumplen con tolerancia $10^{-7}$; los pesos $< 10^{-9}$ se redondean a 0.

---

## 16. Ejemplo numérico

Datos: $\mu$ y $\Sigma$ de la hoja (sección 11), $N = 5$, `n_observaciones = 60`. Los valores marcados con ✓ están asertados en `tests/test_markowitz.py`; el resto se obtuvo ejecutando `calcular_frontera_eficiente(..., n_puntos=5)` y `evaluar_cartera` del propio código sobre la fixture `estadisticas_hoja`.

### 16.1. Estadísticas de los activos

| Activo | $\mu_i$ | $\sigma_i^2$ | $\sigma_i$ |
|---|---|---|---|
| AMZN | 0.013 | 0.002986616 | 0.054650 |
| AAPL | 0.019 | 0.002357364 | 0.048553 |
| NVDA | 0.043 | 0.001427667 | 0.037784 |
| MSFT | 0.010 | 0.002015061 | 0.044889 |
| TSLA | 0.020 | 0.003747111 | 0.061214 |

Autovalores de $\Sigma$: entre $0{,}001314$ y $0{,}003807$; el cociente $\lambda_{\min} / \lambda_{\max} \approx 0{,}35$ está muy por encima de $10^{-10}$, así que la matriz no es singular.

### 16.2. Cartera de mínima varianza (long-only)

| | AMZN | AAPL | NVDA | MSFT | TSLA |
|---|---|---|---|---|---|
| $w^{\text{mv}}$ ✓ | 0.1325 | 0.1674 | 0.3193 | 0.2491 | 0.1317 |

- $\sigma^2_{\text{mv}} = 0{,}000531617$ ✓ ($= 1/D$, porque todos los pesos cerrados son positivos).
- $\sigma_{\text{mv}} = 0{,}02306$ ✓.
- $R_{\text{mv}} = 0{,}02376$ ✓ ($= B/D$).

### 16.3. Frontera con `n_puntos = 5`

$R_{\max} = \mu_{\text{NVDA}} = 0{,}043$. Objetivos: `np.linspace(0.0237594, 0.043, 5)` $= (0{,}023759,\ 0{,}028570,\ 0{,}033380,\ 0{,}038190,\ 0{,}043000)$.

| $k$ | Rentabilidad | Volatilidad | AMZN | AAPL | NVDA | MSFT | TSLA |
|---|---|---|---|---|---|---|---|
| 0 | 0.023759 | 0.023057 | 0.1325 | 0.1674 | 0.3193 | 0.2491 | 0.1317 |
| 1 | 0.028570 | 0.024094 | 0.0718 | 0.1654 | 0.4758 | 0.1705 | 0.1165 |
| 2 | 0.033380 | 0.026967 | 0.0112 | 0.1633 | 0.6322 | 0.0920 | 0.1012 |
| 3 | 0.038190 | 0.031338 | 0.0000 | 0.1337 | 0.7967 | 0.0000 | 0.0697 |
| 4 ✓ | 0.043000 | 0.037784 | 0.0000 | 0.0000 | 1.0000 | 0.0000 | 0.0000 |

En $k = 3$ la cota $w \ge 0$ ya está activa para AMZN y MSFT: en esta zona la frontera long-only se separa de la frontera con ventas en corto.

### 16.4. Cartera actual y rebalanceo (ilustrativo)

Supóngase una cartera con 2000 EUR en cada activo ($T = 10\,000$ EUR). Entonces $w^{\text{actual}}_i = 0{,}2$ para todos, con

$$
R_{\text{actual}} = 0{,}2 \cdot (0{,}013 + 0{,}019 + 0{,}043 + 0{,}010 + 0{,}020) = 0{,}021,
\qquad
\sigma_{\text{actual}} \approx 0{,}024219
$$

Distancias $\lvert \sigma_k - \sigma_{\text{actual}} \rvert$: $0{,}001163$; $0{,}000126$; $0{,}002747$; $0{,}007119$; $0{,}013565$. El punto por defecto es $k = 1$: casi el mismo riesgo y $+0{,}76$ puntos de rentabilidad mensual esperada.

| Ticker | $w^{\text{actual}}$ | $w^{\text{objetivo}}$ | $d_i$ | Importe EUR | Acción |
|---|---|---|---|---|---|
| AMZN | 0.2000 | 0.071841 | -0.128159 | -1281.59 | vender |
| AAPL | 0.2000 | 0.165360 | -0.034640 | -346.40 | vender |
| NVDA | 0.2000 | 0.475788 | +0.275788 | +2757.88 | comprar |
| MSFT | 0.2000 | 0.170532 | -0.029468 | -294.68 | vender |
| TSLA | 0.2000 | 0.116479 | -0.083521 | -835.21 | vender |
| **Suma** | 1 | 1 | 0 | ≈ 0 | |

---

## 17. Notas de mantenimiento

Discrepancias e incidencias detectadas al redactar este documento. Las resueltas indican qué cambió.

1. **Descripción obsoleta de ventas en corto. Resuelta.** `PuntoCarteraOut.pesos` mencionaba una referencia con ventas en corto que ya no existe en la API. Ahora dice que los pesos están en $[0, 1]$ y suman 1 (`app/schemas.py:214-216`).
2. **`rentabilidad` de los puntos de la frontera. Resuelta.** La descripción decía que era la rentabilidad objetivo. Ahora indica que es $w^{\top}\mu$ con los pesos resueltos, que coincide con el objetivo salvo tolerancia numérica (`app/schemas.py:205-211`; cálculo en `app/services/markowitz.py:444-446` y `449-457`).
3. **Tipo de cambio por defecto 1.0. Resuelta.** `resumen_cartera` ya no usa `fx = 1.0` cuando falta el tipo de cambio de una moneda extranjera. Los campos EUR de la posición quedan a `null` y los nativos se conservan; los totales y `/analisis` usan el coste. Se registra un aviso por moneda (`app/routers/posiciones.py:75-104`). En la frontera, la posición se excluye con `sin_tipo_cambio` y no cuenta en `peso_excluido` (secciones 3.1, 3.2 y 3.4).
4. **Fallo parcial del proveedor.** Si yfinance devuelve datos de algunos tickers pero falla en otros, los que fallan se omiten del resultado (`app/services/precios.py:373-375` y `383-384`). El endpoint los clasifica como `sin_historico` (`app/routers/posiciones.py:487-490`), no como 503: solo un fallo total se considera error del proveedor.
5. **Docstring de los tests.** `tests/test_markowitz.py:4-6` menciona la solución cerrada "con ventas en corto permitidas". Es correcto, porque se usa solo como oráculo; no indica que exista en la API.
6. **Comentario de `peso_excluido`. Resuelta.** El comentario ahora indica que solo cuentan las posiciones con valor EUR conocido: las excluidas por `sin_ticker`, `sin_precio_actual` o `sin_tipo_cambio` no cuentan (`app/schemas.py:320-322`).
