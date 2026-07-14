# API Specification

| Versión | Fecha | Estado | Autor |
|---|---|---|---|
| 0.9.0 | 2026-07-14 | En progreso (Contexto: Motor de Inteligencia Energética — `POST /api/v1/motor/lotes/{codigo_lote}/procesar`, Etapa 2 / US-007 — campo `duplicidades` documentado) | Rodrigo Zanin |
| 0.8.0 | 2026-07-14 | En progreso (Contexto: Motor de Inteligencia Energética — `POST /api/v1/motor/lotes/{codigo_lote}/procesar`, Etapa 1 / US-006 + US-010 — documentado) | Rodrigo Zanin |
| 0.7.0 | 2026-07-13 | En progreso (DECISIÓN #9 — resurrección al reimportar una clave natural soft-deleted, los cinco endpoints de importación ganan `restored`— y DECISIÓN #13 — `DELETE /api/v1/consumos/{id}`, corrección de períodos — documentadas) | Rodrigo Zanin |
| 0.6.0 | 2026-07-13 | En progreso (Gestión de Clientes, Gestión de Suministros y Gestión de Consumos —`Lectura`, `Lote de Facturación` y `Consumo`, las tres entidades de §4.3 completas— documentados) | Rodrigo Zanin |
| 0.5.0 | 2026-07-13 | En progreso (Gestión de Clientes, Gestión de Suministros y Gestión de Consumos —`Lectura` y `Lote de Facturación`— documentados) | Rodrigo Zanin |
| 0.4.0 | 2026-07-13 | En progreso (Gestión de Clientes, Gestión de Suministros y Gestión de Consumos documentados) | Rodrigo Zanin |

## Propósito

Este documento especifica la API REST del backend de EnergIA, construido con FastAPI, que expone las operaciones de los distintos contextos delimitados (bounded contexts) del dominio a los clientes frontend e integraciones externas. Documenta el contrato formal de cada endpoint (método, path, request/response, códigos de error), de modo que sirva como referencia única entre el equipo de backend y el de frontend. Se mantiene alineado con el modelo de dominio definido en `DOMAIN_MODEL.md` y con las reglas de negocio de `BUSINESS_ANALYSIS.md`.

Se documenta contexto por contexto, a medida que cada uno aterriza una primera feature real (mismo criterio que `contexts/README.md`). El resto de las secciones queda como esquema pendiente hasta que ese contexto tenga endpoints implementados.

## Contexto: Gestión de Clientes

Implementado en `backend/src/energia/contexts/clientes/` (US-001). Cubre la importación de clientes y su consulta paginada. Ambos endpoints están montados bajo el prefijo `/api/v1/clientes`.

### POST /api/v1/clientes/import

Importa clientes desde un array JSON. Es el adaptador de hoy para el puerto `ClienteSource` (ver `domain/ports.py`): mañana un adaptador de archivo (CSV/Excel) o el ETL de Oracle (ADR-004) pueden implementar el mismo puerto sin tocar dominio ni aplicación.

**Idempotencia**: cada registro se busca por su clave natural (`numero_cliente`) antes de decidir si crear, actualizar o no hacer nada. Reimportar el mismo payload no duplica filas: reporta `updated`/`unchanged` en lugar de `created`.

**Rechazo individual**: un registro que viola un invariante de `Cliente` (por ejemplo, `numero_cliente` vacío) se rechaza de forma individual — el resto del lote se sigue procesando.

**Request body** — array JSON, cada elemento:

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `numero_cliente` | string | Sí (validado a nivel de dominio, no de schema) | Máx. 30 caracteres (`clientes.numero_cliente`, varchar(30)) |
| `nombre` | string | Sí (ídem) | Máx. 150 caracteres |
| `estado` | string | No (default `"Activo"`) | `"Activo"` \| `"Inactivo"` |
| `documento` | string \| null | No | Máx. 20 caracteres |
| `localidad` | string \| null | No | Máx. 100 caracteres |
| `barrio` | string \| null | No | Máx. 100 caracteres |
| `direccion` | object \| null | No | JSON libre (columna `jsonb`); ver ambigüedad documentada en `contexts/README.md` |

Todos los campos son opcionales *a nivel de schema* (permiten `null`) a propósito: un `numero_cliente` o `nombre` faltante es un rechazo de **dominio** (HTTP 200, reportado en `rejected`), no un error estructural de request — salvo una clave desconocida (un campo mal tipeado, por ejemplo `numero_clientee`, o cualquier otra no declarada arriba), que sí es un rechazo estructural individual: `ClienteImportItem` usa `extra="forbid"` (el estándar de `Lote`, ver la sección `POST /api/v1/lotes/import` más abajo), también HTTP 200 en `rejected`, nombrando la clave ofensora.

**Response 200** — `ImportSummary`:

```json
{
  "created": 3,
  "updated": 0,
  "unchanged": 0,
  "restored": 0,
  "rejected": [
    { "record": { "numero_cliente": "", "nombre": "Sin numero", "...": null }, "reasons": ["numero_cliente es obligatorio"] }
  ]
}
```

**`restored` (DECISIÓN #9, resurrección — confirmada por negocio, 2026-07-13, ver `PROJECT_MASTER_SPEC.md`, ítems resueltos)**: un registro cuyo `numero_cliente` no matchea ninguna fila activa, pero sí matchea una fila soft-deleted (`deleted_at IS NOT NULL`), **revive esa misma fila** (mismo `id`, `deleted_at` limpiado, campos fusionados con el registro reimportado) en vez de crear una identidad nueva. Se cuenta en `restored`, separado de `created`/`updated`/`unchanged`. Si hay más de una fila soft-deleted con el mismo `numero_cliente`, solo la más recientemente eliminada (mayor `deleted_at`) es candidata a resurrección — las anteriores quedan eliminadas. Ver `contexts/README.md` ("Comportamiento ante soft-delete") para el detalle completo del mecanismo, compartido por los cinco endpoints de importación de esta API.

**Errores**:

| Código | Causa |
|---|---|
| 422 | Body estructuralmente inválido (no es un array JSON, o un campo tiene un tipo incompatible, ej. `numero_cliente` como número). Distinto del rechazo de dominio de arriba, que responde 200. |

### GET /api/v1/clientes

Lista paginada de clientes vigentes (excluye soft-deleted, `deleted_at IS NULL`), ordenada por `numero_cliente`.

**Query params**:

| Parámetro | Tipo | Default | Notas |
|---|---|---|---|
| `limit` | integer | 50 | Rango 1-200 |
| `offset` | integer | 0 | ≥ 0 |

**Response 200** — `ClientesPage`:

```json
{
  "items": [
    { "id": "...", "numero_cliente": "9001", "nombre": "Ana Gomez", "estado": "Activo",
      "documento": null, "localidad": "Formosa", "barrio": "Centro", "direccion": null }
  ],
  "total": 3,
  "limit": 50,
  "offset": 0
}
```

## Contexto: Gestión de Suministros

Implementado en `backend/src/energia/contexts/suministros/` (US-002). Cubre la importación de suministros y su consulta paginada. Ambos endpoints están montados bajo el prefijo `/api/v1/suministros`.

Un suministro pertenece a exactamente un cliente y a exactamente una categoría tarifaria (RD-005/RD-006/RD-008, `DOMAIN_MODEL.md` §7.2/§7.3). El payload de importación referencia a ambos por su **clave natural**, no por UUID: `numero_cliente` (la clave natural de `Cliente`) y `categoria_tarifaria` (la columna `nombre` de la tabla de catálogo `categorias_tarifarias`, ej. `"Residencial"`). El endpoint resuelve ambas claves naturales a UUID antes de intentar guardar el registro; si alguna no existe, el registro se rechaza individualmente (ver más abajo), no la request completa.

### POST /api/v1/suministros/import

Importa suministros desde un array JSON. Mismo patrón que `POST /api/v1/clientes/import`: es el adaptador de hoy para el puerto `SuministroSource` (ver `domain/ports.py`); un adaptador de archivo o el ETL de Oracle (ADR-004) pueden implementar el mismo puerto más adelante sin tocar dominio ni aplicación.

**Idempotencia**: cada registro se busca por su clave natural (`numero_suministro`) antes de decidir si crear, actualizar o no hacer nada. Reimportar el mismo payload no duplica filas: reporta `updated`/`unchanged` en lugar de `created`.

**Rechazo individual**: un registro se rechaza de forma individual, sin abortar el resto del lote, en cualquiera de estos casos:

- `numero_cliente` faltante o el cliente referenciado no existe (`"cliente inexistente: numero_cliente=..."`).
- `categoria_tarifaria` faltante o la categoría referenciada no existe (`"categoria tarifaria inexistente: ..."`).
- El registro viola un invariante de `Suministro` (por ejemplo, `numero_suministro` vacío o `fecha_alta` con formato inválido).
- Cualquier clave no declarada en el schema (un campo mal tipeado, por ejemplo `categoria_tarifariaa`) — `SuministroImportItem` usa `extra="forbid"` (el estándar de `Lote`, ver la sección `POST /api/v1/lotes/import` más abajo).

**Request body** — array JSON, cada elemento:

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `numero_suministro` | string | Sí (validado a nivel de dominio, no de schema) | Máx. 30 caracteres (`suministros.numero_suministro`, varchar(30)) |
| `numero_cliente` | string | Sí (ídem) | Clave natural de `Cliente`; resuelta a `cliente_id` (UUID) por el puerto `ClienteDirectory` |
| `categoria_tarifaria` | string | Sí (ídem) | `nombre` de una categoría tarifaria existente (`categorias_tarifarias`, ej. `"Residencial"`); resuelta a `categoria_tarifaria_id` (UUID) por el puerto `CategoriaTarifariaDirectory` |
| `fecha_alta` | string (fecha ISO `YYYY-MM-DD`) | Sí (ídem) | Sin default; `suministros.fecha_alta` es `NOT NULL` sin `DEFAULT` |
| `estado` | string | No (default `"Activo"`) | Sin enum cerrado (a diferencia de `Cliente.estado`): `DOMAIN_MODEL.md` §7.2 no enumera valores, así que `suministros.estado` es un varchar(15) abierto; solo se valida longitud |
| `localidad` | string \| null | No | Máx. 100 caracteres |
| `barrio` | string \| null | No | Máx. 100 caracteres |

Todos los campos son opcionales *a nivel de schema* (permiten `null`) a propósito, igual que en Gestión de Clientes: un valor faltante o inválido es un rechazo de **dominio** o de **resolución de referencia** (HTTP 200, reportado en `rejected`), no un error estructural de request — salvo una clave desconocida, que sí es un rechazo estructural individual (también HTTP 200, ver arriba).

**Response 200** — `ImportSummary`:

```json
{
  "created": 2,
  "updated": 0,
  "unchanged": 0,
  "restored": 0,
  "rejected": [
    { "record": { "numero_suministro": "SUM-300", "numero_cliente": "no-existe", "...": null },
      "reasons": ["cliente inexistente: numero_cliente='no-existe'"] },
    { "record": { "numero_suministro": "SUM-400", "categoria_tarifaria": "no-existe", "...": null },
      "reasons": ["categoria tarifaria inexistente: 'no-existe'"] }
  ]
}
```

**`restored`**: mismo mecanismo de resurrección que Gestión de Clientes (DECISIÓN #9), aplicado a `numero_suministro` — ver esa sección arriba para el detalle completo.

**Errores**:

| Código | Causa |
|---|---|
| 422 | Body estructuralmente inválido (no es un array JSON, o un campo tiene un tipo incompatible). Distinto del rechazo de dominio/referencia de arriba, que responde 200. |

### GET /api/v1/suministros

Lista paginada de suministros vigentes (excluye soft-deleted, `deleted_at IS NULL`), ordenada por `numero_suministro`.

**Query params**:

| Parámetro | Tipo | Default | Notas |
|---|---|---|---|
| `limit` | integer | 50 | Rango 1-200 |
| `offset` | integer | 0 | ≥ 0 |
| `numero_cliente` | string \| null | (ninguno) | Filtra a los suministros de ese cliente. Se resuelve a `cliente_id` con el mismo puerto `ClienteDirectory` del import; un `numero_cliente` que no resuelve a ningún cliente devuelve una página vacía (`total: 0`), no un error — es un filtro, no una búsqueda de la que el cliente dependa. |

**Response 200** — `SuministrosPage`:

```json
{
  "items": [
    { "id": "...", "numero_suministro": "SUM-100", "cliente_id": "...", "categoria_tarifaria_id": "...",
      "localidad": null, "barrio": null, "estado": "Activo", "fecha_alta": "2024-01-15" }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

`cliente_id` y `categoria_tarifaria_id` se exponen como UUID, no como sus claves naturales (`numero_cliente`/`nombre`): resolver el UUID de vuelta a la clave natural del cliente/categoría (por ejemplo, para mostrarlos en el frontend) queda pendiente para cuando ese caso de uso exista.

## Contexto: Gestión de Consumos

Implementado en `backend/src/energia/contexts/consumos/`, para las tres entidades que `DOMAIN_MODEL.md` §4.3 asigna a este contexto: `Lectura` (US-003), `Lote de Facturación` (US-005) y `Consumo` (US-004) — con `Consumo` aterrizado, Épica 1 queda completa (ver `contexts/README.md`, "One package, staged entities"). Cubre la importación de lecturas históricas, la importación de lotes de facturación, la importación de consumos históricos, la consulta paginada de las tres, y (DECISIÓN #13) la corrección de un consumo mal ingresado por baja lógica. Los endpoints de `Lectura` están montados bajo `/api/v1/lecturas`; los de `Lote` bajo `/api/v1/lotes`; los de `Consumo` bajo `/api/v1/consumos`.

Una lectura pertenece a exactamente un suministro (RD-015, `DOMAIN_MODEL.md` §7.5). El payload de importación referencia al suministro por su **clave natural**, `numero_suministro`, no por UUID: el endpoint la resuelve a UUID antes de intentar guardar el registro; si no existe, el registro se rechaza individualmente (ver más abajo), no la request completa.

**Identidad compuesta**: a diferencia de `Cliente`/`Suministro` (una sola clave natural), la identidad de una lectura es el par `(numero_suministro, fecha_lectura)` — un suministro acumula muchas lecturas en el tiempo, una por fecha. `docker/postgres/init/01_schema.sql` no tenía ningún índice único sobre esta clave antes de US-003; se agregó `uq_lecturas_suministro_fecha` (`suministro_id, fecha_lectura`, parcial `WHERE deleted_at IS NULL`) específicamente para que la importación sea idempotente — sin él, reimportar el mismo histórico duplicaba filas en lugar de actualizar/no-hacer-nada.

### POST /api/v1/lecturas/import

Importa lecturas desde un array JSON. Mismo patrón que `POST /api/v1/suministros/import`: es el adaptador de hoy para el puerto `LecturaSource` (ver `domain/ports.py`); un adaptador de archivo o el ETL de Oracle (ADR-004) pueden implementar el mismo puerto más adelante sin tocar dominio ni aplicación.

**Idempotencia**: cada registro se busca por su clave natural compuesta (`numero_suministro` resuelto a `suministro_id`, `fecha_lectura`) antes de decidir si crear, actualizar o no hacer nada. Reimportar el mismo payload no duplica filas: reporta `updated`/`unchanged` en lugar de `created`. Un `(numero_suministro, fecha_lectura)` repetido *dentro* del mismo payload se procesa de forma secuencial: la segunda ocurrencia actualiza sobre la primera, en lugar de entrar en conflicto.

**Rechazo individual**: un registro se rechaza de forma individual, sin abortar el resto del lote, en cualquiera de estos casos:

- `numero_suministro` faltante o el suministro referenciado no existe (`"suministro inexistente: numero_suministro=..."`).
- El registro viola un invariante de `Lectura`: `fecha_lectura` con formato inválido, `lectura_anterior`/`lectura_actual` faltante, no numérico, negativo (un medidor físico no retrocede — invariante solo de dominio, no una CHECK de la base) o con más de 3 decimales o más de 9 dígitos enteros (no entra en `numeric(12,3)`), `lectura_actual` menor que `lectura_anterior` (RD-013), o `dias_facturados` faltante, no entero o no mayor que cero (RD-014).
- Cualquier clave no declarada en el schema (un campo mal tipeado, por ejemplo `dias_facturadoo`) — `LecturaImportItem` usa `extra="forbid"` (el estándar de `Lote`, ver la sección `POST /api/v1/lotes/import` más abajo).

**Request body** — array JSON, cada elemento:

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `numero_suministro` | string | Sí (validado a nivel de dominio, no de schema) | Clave natural de `Suministro`; resuelta a `suministro_id` (UUID) por el puerto `SuministroDirectory` |
| `fecha_lectura` | string (fecha ISO 8601) | Sí (ídem) | Junto a `numero_suministro`/`suministro_id`, forma la clave natural compuesta. Se parsea con `date.fromisoformat` de Python: `YYYY-MM-DD` es la forma recomendada; la forma compacta `YYYYMMDD` también es válida |
| `lectura_anterior` | number | Sí (ídem) | `numeric(12,3)`: máx. 9 dígitos enteros, 3 decimales; no negativo |
| `lectura_actual` | number | Sí (ídem) | Ídem `lectura_anterior`; además debe ser ≥ `lectura_anterior` (RD-013) |
| `dias_facturados` | integer | Sí (ídem) | Debe ser mayor que cero (RD-014) |

Todos los campos son opcionales *a nivel de schema* (permiten `null`) a propósito, igual que en Gestión de Clientes/Suministros: un valor faltante o inválido es un rechazo de **dominio** o de **resolución de referencia** (HTTP 200, reportado en `rejected`), no un error estructural de request — salvo una clave desconocida, que sí es un rechazo estructural individual (también HTTP 200, ver arriba).

**Response 200** — `ImportSummary`:

```json
{
  "created": 2,
  "updated": 0,
  "unchanged": 0,
  "restored": 0,
  "rejected": [
    { "record": { "numero_suministro": "no-existe", "fecha_lectura": "2024-01-15", "...": null },
      "reasons": ["suministro inexistente: numero_suministro='no-existe'"] },
    { "record": { "numero_suministro": "SUM-100", "fecha_lectura": "2024-03-15", "...": null },
      "reasons": ["lectura_actual no puede ser menor que lectura_anterior (100.000 < 200.000)"] }
  ]
}
```

**`restored`**: mismo mecanismo de resurrección (DECISIÓN #9), aplicado a la clave natural compuesta `(numero_suministro, fecha_lectura)` — ver "Contexto: Gestión de Clientes" arriba para el detalle completo.

**Errores**:

| Código | Causa |
|---|---|
| 422 | Body estructuralmente inválido (no es un array JSON, o un campo tiene un tipo incompatible). Distinto del rechazo de dominio/referencia de arriba, que responde 200. |

### GET /api/v1/lecturas

Lista paginada de lecturas vigentes (excluye soft-deleted, `deleted_at IS NULL`), ordenada por `(fecha_lectura, id)`.

**Por qué ese orden y no la clave natural compuesta**: a diferencia de `numero_cliente`/`numero_suministro` (identificadores de negocio con orden propio), `suministro_id` es un UUID interno sin significado de orden — ordenar primero por `fecha_lectura` es lo que realmente sirve para "disponer del histórico completo" (US-003), tanto en una consulta global como filtrada a un suministro. `id` es solo el desempate: dos suministros distintos pueden compartir la misma `fecha_lectura` en un listado sin filtrar (el índice único solo garantiza unicidad por suministro), así que `fecha_lectura` sola no alcanza para un orden de paginación determinístico.

**Query params**:

| Parámetro | Tipo | Default | Notas |
|---|---|---|---|
| `limit` | integer | 50 | Rango 1-200 |
| `offset` | integer | 0 | ≥ 0 |
| `numero_suministro` | string \| null | (ninguno) | Filtra a las lecturas de ese suministro. Se resuelve a `suministro_id` con el mismo puerto `SuministroDirectory` del import; un `numero_suministro` que no resuelve a ningún suministro devuelve una página vacía (`total: 0`), no un error — es un filtro, no una búsqueda de la que el cliente dependa. |

**Response 200** — `LecturasPage`:

```json
{
  "items": [
    { "id": "...", "suministro_id": "...", "fecha_lectura": "2024-01-15",
      "lectura_anterior": "100.000", "lectura_actual": "150.500", "dias_facturados": 30 }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

`suministro_id` se expone como UUID, no como `numero_suministro` — misma decisión documentada para `cliente_id`/`categoria_tarifaria_id` en Gestión de Suministros arriba, y pendiente por la misma razón.

### POST /api/v1/lotes/import

Importa lotes de facturación desde un array JSON. Mismo patrón que los demás endpoints de importación: es el adaptador de hoy para el puerto `LoteSource` (ver `domain/ports.py`); un adaptador de archivo o el ETL de Oracle (ADR-004) pueden implementar el mismo puerto más adelante sin tocar dominio ni aplicación. A diferencia de `Lectura`/`Suministro`, `Lote` no referencia ninguna otra tabla — no hay clave natural que resolver antes de validar el registro.

**Idempotencia**: cada registro se busca por su clave natural (`codigo_lote`) antes de decidir si crear, actualizar o no hacer nada (RD-010, `DOMAIN_MODEL.md` §7.4: "un lote no puede ejecutarse dos veces"). Reimportar el mismo payload no duplica filas: reporta `updated`/`unchanged` en lugar de `created`.

**El campo `estado` no existe en este payload, deliberadamente.** Un lote importado nace siempre en `Pendiente`: las transiciones de estado (`Pendiente` → `Procesando` → `Procesado`/`Error`) son responsabilidad del futuro motor de procesamiento, un caso de uso que todavía no existe. Si el payload pudiera fijar `estado` libremente, una request maliciosa podría fabricar un lote ya `Procesado` sin que jamás haya pasado por el pipeline que ese estado representa — exactamente lo que RD-010 busca evitar. Si un caller envía `estado` de todos modos, el registro se **rechaza individualmente** (HTTP 200, reportado en `rejected`, nombrando `estado` como la clave ofensora) — no se ignora en silencio, y no es un error 422 del batch completo: cualquier clave no reconocida en el payload (`estado`, o un campo mal tipeado como `canditad_registros`) se rechaza así, exactamente igual. Reimportar un lote que ya transicionó a `Procesando`/`Procesado`/`Error` (por ejemplo, vía SQL directo hoy, o vía el motor de procesamiento el día que exista) **nunca** resetea su `estado` a nivel de aplicación (`ImportLotes`) ni a nivel de repositorio (`SqlAlchemyLoteRepository.save()` nunca escribe la columna `estado` en una actualización, por diseño) — solo actualiza `nombre`/`cantidad_registros` si cambiaron.

**Rechazo individual**: un registro se rechaza de forma individual, sin abortar el resto del lote, si viola un invariante de `Lote` o el contrato del payload:

- `codigo_lote` faltante o vacío.
- `cantidad_registros` no entero, negativo, mayor a `2147483647` (el máximo de `integer` en Postgres — ver la nota de la tabla más abajo), o enviado explícitamente como `null` (ver "Campos omitidos vs. `null` explícito").
- Cualquier clave no declarada en el schema (`estado`, o un campo mal tipeado).

**Campos omitidos vs. `null` explícito (reimportación parcial)**: `nombre` y `cantidad_registros` distinguen tres estados en cada reimportación, no dos:

| Estado del campo en el payload | Efecto sobre un `codigo_lote` nuevo | Efecto sobre un `codigo_lote` existente |
|---|---|---|
| Omitido (la clave no aparece) | Aplica el default documentado (`nombre` → `null`, `cantidad_registros` → `0`) | **Preserva** el valor ya almacenado — no lo pisa |
| `null` explícito | `nombre`: queda `null`. `cantidad_registros`: **rechazo individual** (la columna es `NOT NULL DEFAULT 0`; un `null` explícito no es un valor válido, a diferencia de omitir el campo) | `nombre`: se pisa a `null`. `cantidad_registros`: **rechazo individual**, el valor almacenado no se toca |
| Valor concreto | Se usa ese valor | Actualiza ese campo si difiere del almacenado |

Antes de esta distinción, un campo simplemente ausente del payload se trataba igual que un `null` explícito, así que reimportar un `codigo_lote` existente enviando solo los campos que cambiaron borraba (`nombre` → `null`, `cantidad_registros` → `0`) los campos no repetidos en cada request. Esa reimportación parcial ahora preserva lo ya almacenado.

**Request body** — array JSON, cada elemento:

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `codigo_lote` | string | Sí (validado a nivel de dominio, no de schema) | Máx. 50 caracteres (`lotes.codigo_lote`, varchar(50)); clave natural (`uq_lotes_codigo_lote`, índice único parcial) |
| `nombre` | string \| null | No | Máx. 150 caracteres |
| `cantidad_registros` | integer | No (default `0`) | Debe ser ≥ 0 y ≤ `2147483647` (`ck_lotes_cantidad_registros_no_negativa`; el límite superior es el máximo de `integer` en Postgres — `lotes.cantidad_registros` no es `bigint`) |

`codigo_lote` es obligatorio; `nombre`/`cantidad_registros` son opcionales *a nivel de schema* (permiten `null` u omitirse) a propósito, igual que en el resto de los contextos — pero, a diferencia del resto, distinguen explícitamente "omitido" de "`null` explícito" (ver arriba). Un valor inválido (o un `cantidad_registros` explícitamente `null`) es un rechazo de **dominio** (HTTP 200, reportado en `rejected`), no un error estructural de request. Ninguna clave fuera de `codigo_lote`/`nombre`/`cantidad_registros` está permitida — una clave extra (incluido `estado`) es un rechazo estructural individual, también HTTP 200 en `rejected`.

**Response 200** — `ImportSummary`:

```json
{
  "created": 2,
  "updated": 0,
  "unchanged": 0,
  "restored": 0,
  "rejected": [
    { "record": { "codigo_lote": "", "nombre": "Sin codigo" },
      "reasons": ["codigo_lote es obligatorio"] },
    { "record": { "codigo_lote": "LOTE-2024-03", "cantidad_registros": -5 },
      "reasons": ["cantidad_registros no puede ser negativo: -5"] }
  ]
}
```

**`restored`**: mismo mecanismo de resurrección (DECISIÓN #9), aplicado a `codigo_lote` — ver "Contexto: Gestión de Clientes" arriba para el detalle completo. Particularidad de `Lote`: un lote resurrecto **conserva** el `estado` (y `fecha_importacion`) que tenía al momento de eliminarse — la resurrección nunca lo resetea a `Pendiente`, la misma protección de RD-010 que ya aplica a una actualización ordinaria (ver el párrafo sobre `estado` más arriba).

El `record` de cada rechazo solo incluye las claves realmente presentes en el payload original — un campo omitido no aparece (ni como `null` ni con ningún otro valor centinela).

**Errores**:

| Código | Causa |
|---|---|
| 422 | Body estructuralmente inválido a nivel del array completo (no es un array JSON). Un elemento individualmente inválido (tipo incompatible, clave desconocida) no produce 422: se rechaza solo ese registro, HTTP 200, ver arriba. |

### GET /api/v1/lotes

Lista paginada de lotes vigentes (excluye soft-deleted, `deleted_at IS NULL`), ordenada por `(fecha_importacion desc, id)` — el lote importado más recientemente primero.

**Por qué ese orden**: a diferencia de `codigo_lote` (un identificador de negocio sin orden cronológico inherente), `fecha_importacion` es exactamente lo que "procesar automáticamente cada período" (US-005) necesita: saber qué lotes llegaron y en qué orden, sin que quien consulta tenga que ordenar del lado del cliente. `id` es solo el desempate cuando dos lotes comparten la misma `fecha_importacion`.

**Query params**:

| Parámetro | Tipo | Default | Notas |
|---|---|---|---|
| `limit` | integer | 50 | Rango 1-200 |
| `offset` | integer | 0 | ≥ 0 |
| `estado` | string \| null | (ninguno) | Filtra por `EstadoLote` (`"Pendiente"` \| `"Procesando"` \| `"Procesado"` \| `"Error"`). Un valor que no pertenece al enum responde **422** (no una página vacía): es un error de contrato, no un filtro que simplemente no matchea nada. |

**Response 200** — `LotesPage`:

```json
{
  "items": [
    { "id": "...", "codigo_lote": "LOTE-2024-01", "nombre": "Enero 2024",
      "fecha_importacion": "2026-07-13T13:29:21.924547Z", "cantidad_registros": 175,
      "estado": "Procesado" }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

### POST /api/v1/consumos/import

Importa consumos históricos desde un array JSON (US-004: "quiero importar los consumos históricos, para entrenar el modelo de IA"). Mismo patrón que los demás endpoints de importación: es el adaptador de hoy para el puerto `ConsumoSource` (ver `domain/ports.py`); un adaptador de archivo o el ETL de Oracle (ADR-004) pueden implementar el mismo puerto más adelante sin tocar dominio ni aplicación.

**Dos resoluciones de clave natural, no una.** A diferencia de `Lectura` (solo `numero_suministro`) o `Lote` (ninguna), un `Consumo` referencia tanto un suministro (`numero_suministro` → `suministro_id`, vía `SuministroDirectory`) como un lote de facturación (`codigo_lote` → `lote_id`, vía el nuevo puerto `LoteDirectory` — resolución *dentro del mismo contexto*, implementada con una consulta ORM ordinaria contra `LoteModel`, no SQL directo, ya que `Lote` vive en este mismo `consumos`; ver `contexts/README.md`, "cross-context directory-port pattern" → "when a directory port is *not* needed"). Ambas deben resolver para que el registro se intente validar.

**Tercera resolución, opcional: `fecha_lectura` → `lectura_id`.** Si el payload incluye `fecha_lectura`, se busca la lectura de ese suministro para esa fecha (vía `LecturaRepository.get_by_suministro_and_fecha`, reutilizado directamente — no hace falta un puerto nuevo); si no existe, el registro se rechaza (`"lectura inexistente: ..."`). Si `fecha_lectura` se omite, `lectura_id` queda `null` — el comentario de `docker/postgres/init/01_schema.sql` sobre `consumos.lectura_id` es explícito: los archivos históricos a recibir pueden no traer el detalle de lectura por período, y esto no es un error (RD-018 se satisface cuando la lectura *es* resoluble, no exige que siempre lo sea).

**`consumo_promedio_diario`: computado vs. dado (DOMAIN_MODEL.md §7.6).** El dominio lista `calcularPromedioDiario()` como método del agregado — por eso, si el campo se omite, `Consumo.create()` lo **calcula** (`kwh / dias_facturados`, cuantizado a `numeric(12,3)`) en vez de dejarlo vacío; si se envía un valor concreto, se valida igual que `kwh` (mismos guardas de NaN/Infinito/negativo/precisión) y se guarda tal cual, sin cruzarlo contra el valor derivado.

**Idempotencia**: cada registro se busca por su clave natural compuesta `(numero_suministro, fecha_inicio, fecha_fin)` (resuelto a `suministro_id`) antes de decidir si crear, actualizar o no hacer nada. Reimportar el mismo payload no duplica filas: reporta `updated`/`unchanged` en lugar de `created`. Un mismo período repetido *dentro* del mismo payload se procesa de forma secuencial: la segunda ocurrencia actualiza sobre la primera, en lugar de entrar en conflicto.

**Rechazo individual**: un registro se rechaza de forma individual, sin abortar el resto del lote, en cualquiera de estos casos:

- `numero_suministro` faltante o el suministro referenciado no existe (`"suministro inexistente: ..."`).
- `codigo_lote` faltante o el lote referenciado no existe (`"lote inexistente: ..."`).
- `fecha_lectura` con formato inválido, o dada pero sin ninguna lectura que resuelva para ese suministro y esa fecha (`"lectura inexistente: ..."`).
- El registro viola un invariante de `Consumo`: `fecha_inicio`/`fecha_fin` faltante o con formato inválido, `fecha_fin` anterior a `fecha_inicio`; `dias_facturados` faltante, no entero, no mayor que cero, booleano, o mayor a `2147483647` (el máximo de `integer` en Postgres); `kwh` faltante, no numérico, NaN, infinito, negativo (RD-016), booleano, con más de 3 decimales o más de 9 dígitos enteros (no entra en `numeric(12,3)`); `consumo_promedio_diario` dado con cualquiera de esos mismos problemas.
- Cualquier clave no declarada en el schema (`estado`, o un campo mal tipeado) — `ConsumoImportItem` usa `extra="forbid"` (el estándar de `Lote`, ver esa sección arriba).

**Campos omitidos vs. `null` explícito**: `consumo_promedio_diario` y `fecha_lectura` distinguen tres estados en cada importación, pero con efectos distintos entre sí sobre un período existente — `consumo_promedio_diario` es un campo *derivado* (sus insumos, `kwh`/`dias_facturados`, son siempre obligatorios y ya vienen validados en cada importación), mientras que `fecha_lectura`/`lectura_id` sigue el mismo patrón de `Lote.nombre`/`cantidad_registros` (un campo *opaco*, sin insumos propios de los que recalcular):

| Estado del campo en el payload | Efecto sobre un período nuevo | Efecto sobre un período existente |
|---|---|---|
| Omitido (la clave no aparece) | `consumo_promedio_diario`: se **calcula** (`kwh / dias_facturados`). `fecha_lectura`: `lectura_id` queda `null` | `consumo_promedio_diario`: se **recalcula siempre** a partir del `kwh`/`dias_facturados` de *este* registro — nunca preserva el valor ya almacenado (ver nota debajo). `fecha_lectura`: **preserva** el `lectura_id` ya almacenado — no lo pisa |
| `null` explícito | `consumo_promedio_diario`: se guarda `null` (no se calcula). `fecha_lectura`: `lectura_id` queda `null` | `consumo_promedio_diario`: se pisa a `null`. `fecha_lectura`: se pisa a `null` (limpia la asociación ya almacenada) |
| Valor concreto | `consumo_promedio_diario`: se valida y guarda tal cual. `fecha_lectura`: se resuelve a `lectura_id` (o se rechaza el registro si no resuelve) | Actualiza ese campo si difiere del almacenado |

**Por qué `consumo_promedio_diario` recalcula en lugar de preservar al omitirse**: a diferencia de `Lote.nombre`/`cantidad_registros` (campos opacos, sin más respaldo que un default de dominio si se omiten — por eso *deben* preservar lo ya almacenado, o lo pisarían con ese default), `consumo_promedio_diario` siempre tiene sus dos insumos (`kwh`, `dias_facturados`) presentes y validados en cada importación, así que recalcular es siempre seguro y siempre coherente con el resto de la fila. Antes de esta corrección, omitir `consumo_promedio_diario` en una reimportación preservaba el promedio ya almacenado incluso cuando `kwh`/`dias_facturados` habían cambiado, dejando un promedio obsoleto que contradecía al resto de la fila — caso reproducido: `kwh` 100 → 295 (mismo `dias_facturados`, 31), `consumo_promedio_diario` omitido en la reimportación devolvía 3.226 (el promedio de la importación anterior) en lugar de recalcular a 9.516.

**Request body** — array JSON, cada elemento:

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `numero_suministro` | string | Sí (validado a nivel de dominio, no de schema) | Clave natural de `Suministro`; resuelta a `suministro_id` (UUID) por `SuministroDirectory` |
| `codigo_lote` | string | Sí (ídem) | Clave natural de `Lote`; resuelta a `lote_id` (UUID) por `LoteDirectory` |
| `fecha_inicio` | string (fecha ISO 8601) | Sí (ídem) | Junto a `numero_suministro`/`suministro_id` y `fecha_fin`, forma la clave natural compuesta |
| `fecha_fin` | string (fecha ISO 8601) | Sí (ídem) | Debe ser ≥ `fecha_inicio` |
| `dias_facturados` | integer | Sí (ídem) | Debe ser mayor que cero |
| `kwh` | number | Sí (ídem) | `numeric(12,3)`: máx. 9 dígitos enteros, 3 decimales; no negativo (RD-016) |
| `consumo_promedio_diario` | number \| null | No (`UNSET` por defecto) | Ver "Campos omitidos vs. `null` explícito" arriba |
| `fecha_lectura` | string (fecha ISO 8601) \| null | No (`UNSET` por defecto) | Ver "Campos omitidos vs. `null` explícito" arriba |

Todos los campos son opcionales *a nivel de schema* (permiten `null`) a propósito: un valor faltante o inválido es un rechazo de **dominio** o de **resolución de referencia** (HTTP 200, reportado en `rejected`), no un error estructural de request — salvo una clave desconocida, que sí es un rechazo estructural individual (también HTTP 200, ver `Lote` arriba).

**Response 200** — `ImportSummary`:

```json
{
  "created": 2,
  "updated": 0,
  "unchanged": 0,
  "restored": 0,
  "rejected": [
    { "record": { "numero_suministro": "no-existe", "codigo_lote": "LOTE-2024-01", "...": null },
      "reasons": ["suministro inexistente: numero_suministro='no-existe'"] },
    { "record": { "numero_suministro": "SUM-100", "codigo_lote": "LOTE-2024-01",
        "fecha_inicio": "2024-05-31", "fecha_fin": "2024-05-01", "...": null },
      "reasons": ["fecha_fin no puede ser anterior a fecha_inicio (2024-05-01 < 2024-05-31)"] }
  ]
}
```

**`restored`**: mismo mecanismo de resurrección (DECISIÓN #9), aplicado a la clave natural compuesta `(numero_suministro, fecha_inicio, fecha_fin)` — ver "Contexto: Gestión de Clientes" arriba para el detalle completo. `consumo_promedio_diario` se recalcula igual en una resurrección que en una actualización ordinaria (nunca preserva el valor de la fila eliminada); ver DECISIÓN #13 más abajo para cómo esto se combina con `DELETE /api/v1/consumos/{id}` como vía de corrección.

**Errores**:

| Código | Causa |
|---|---|
| 422 | Body estructuralmente inválido (no es un array JSON, o un campo tiene un tipo incompatible). Distinto del rechazo de dominio/referencia de arriba, que responde 200. |

**Limitación conocida (RD-017, superposición parcial de períodos)**: `uq_consumos_suministro_periodo` (índice único parcial, `WHERE deleted_at IS NULL` — deuda #10 de `PROJECT_MASTER_SPEC.md`, resuelta antes de esta historia) evita importar dos veces **exactamente** el mismo período para un suministro. No impide, en cambio, que se carguen dos períodos que se solapan parcialmente (por ejemplo, 01/03-31/03 y 15/03-15/04 para el mismo suministro): eso requeriría un `EXCLUDE` constraint con rangos de fecha (extensión `btree_gist`, `docs/03-architecture/DATABASE_DESIGN.md` §6.4), que no se agrega en esta historia. Este endpoint no implementa ninguna detección de solapamiento propia.

**Limitación conocida: redondeo a cero.** `consumo_promedio_diario`/`kwh` son `numeric(12,3)`: un promedio genuinamente distinto de cero puede redondear a `0.000` y volverse indistinguible de un cero real — por ejemplo, `kwh=0.001` con `dias_facturados=365` computa `0.0000027...`, que redondea a `0.000`. Relevante para la detección de anomalías corriente abajo, donde un consumo cero es en sí mismo una señal.

### DELETE /api/v1/consumos/{id}

**DECISIÓN #13 (confirmada por negocio, 2026-07-13 — ver `PROJECT_MASTER_SPEC.md`, ítems resueltos): la vía de corrección de un período mal ingresado.** Da de baja lógica (`deleted_at`) el consumo **activo** con ese `id`, para poder reimportar el período corregido a continuación — combinado con DECISIÓN #9 (resurrección), la reimportación revive esa misma fila (`restored: 1`), no crea una tercera.

**Path param**:

| Parámetro | Tipo | Notas |
|---|---|---|
| `id` | UUID | El `id` de un `Consumo` (campo `id` de `ConsumoSchema`, ver `GET /api/v1/consumos` arriba) |

**Localización solo por `id`, no por la clave primaria compuesta**: `consumos.id` no es la primary key completa — la tabla está particionada por `fecha_inicio` (`docker/postgres/init/01_schema.sql`), así que la primary key real es `(id, fecha_inicio)`. Este endpoint solo recibe `id` (el cliente HTTP no conoce `fecha_inicio` de antemano), así que localiza la fila por `id` solo, asumiendo que es único en la práctica — una suposición razonable (`id` se genera vía `uuid4()`, nunca se reutiliza) pero no una garantía a nivel de base de datos, a diferencia de una primary key simple por `id`. Postgres no puede podar particiones con esta búsqueda (no conoce `fecha_inicio`), así que recorre todas — aceptable para un endpoint que borra un registro a la vez, no una operación masiva.

**Response 204** — sin cuerpo. El consumo queda excluido de `GET /api/v1/consumos` inmediatamente después.

**Errores**:

| Código | Causa |
|---|---|
| 404 | No existe un consumo **activo** con ese `id` — deliberadamente la misma respuesta tanto si `id` es desconocido como si ya estaba soft-deleted (el llamador no puede, ni necesita, distinguir cuál de los dos casos es). No commitea nada en este camino. |

No existe (todavía) un endpoint equivalente para `Cliente`, `Suministro`, `Lectura` ni `Lote` — evaluar agregarlos queda pendiente para cuando surja la necesidad de negocio (ver `PROJECT_MASTER_SPEC.md`).

### GET /api/v1/consumos

Lista paginada de consumos vigentes (excluye soft-deleted, `deleted_at IS NULL`), ordenada por `(fecha_inicio desc, id)` — el período de facturación más reciente primero, lo que "disponer del histórico completo para entrenar el modelo de IA" (US-004) necesita al navegar sin filtro. `id` es solo el desempate cuando dos consumos comparten la misma `fecha_inicio`.

**Query params**:

| Parámetro | Tipo | Default | Notas |
|---|---|---|---|
| `limit` | integer | 50 | Rango 1-200 |
| `offset` | integer | 0 | ≥ 0 |
| `numero_suministro` | string \| null | (ninguno) | Filtra a los consumos de ese suministro. Se resuelve a `suministro_id` con `SuministroDirectory`; si no resuelve, devuelve una página vacía (`total: 0`), no un error. |
| `codigo_lote` | string \| null | (ninguno) | Filtra a los consumos de ese lote. Se resuelve a `lote_id` con `LoteDirectory`; misma semántica de página vacía si no resuelve. |

**Response 200** — `ConsumosPage`:

```json
{
  "items": [
    { "id": "...", "suministro_id": "...", "lote_id": "...", "lectura_id": null,
      "fecha_inicio": "2024-01-01", "fecha_fin": "2024-01-31", "dias_facturados": 31,
      "kwh": "310.500", "consumo_promedio_diario": "10.016" }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

`suministro_id`/`lote_id`/`lectura_id` se exponen como UUID, no como sus claves naturales — misma decisión documentada para `cliente_id`/`categoria_tarifaria_id` en Gestión de Suministros, y pendiente por la misma razón.

## Contexto: Motor de Inteligencia Energética

Épica 2, slices 1-2 (US-006 "validar la integridad de los datos importados" + US-010, el disparo del motor; US-007 "detección de duplicados"). Documenta Etapa 1 (validación de integridad, `docs/04-ai/AI_ENGINE_SPEC.md` §4) y Etapa 2 (detección de duplicidades, §5); las Etapas 3-8 (features, estadística, reglas, Isolation Forest, IRE, IEE) no están implementadas todavía.

### POST /api/v1/motor/lotes/{codigo_lote}/procesar

Ejecuta la Etapa 1 sobre el lote `codigo_lote`: valida que su carga esté completa (AI_ENGINE_SPEC.md §2.1), corre los 7 chequeos de integridad (V1-V7, §4.1) sobre su cadena importada (`consumos` + `lecturas` + `suministros` + `categorias_tarifarias`) y decide su `estado` final. A continuación corre la Etapa 2 (§5): detecta solapamientos de períodos, near-duplicates de lecturas y drift de conteo entre lotes, y los agrega en el campo `duplicidades` de la respuesta — **anotación únicamente** (DEC-005): nunca cambia el `estado_final`, que sigue decidiéndose solo por la Etapa 1 (DEC-004).

**Disparador corregido (STEP 0, AI_ENGINE_SPEC.md §2.1-§2.2, 2026-07-14):** este endpoint actúa sobre un lote `Pendiente` (o `Error`, reintento) — es el propio motor quien lo transiciona a `Procesando` y luego a `Procesado`/`Error`, nunca al revés. La versión previa de esta especificación (todavía sin implementar) describía el disparo sobre un lote ya `Procesado`, una inconsistencia interna corregida junto con esta implementación (ver AI_ENGINE_SPEC.md §2 para el detalle completo).

**Path param**:

| Parámetro | Tipo | Notas |
|---|---|---|
| `codigo_lote` | string | Clave natural del `Lote` (`lotes.codigo_lote`) |

**Precondiciones y sus códigos de error** (evaluadas en este orden):

| Código | Causa |
|---|---|
| 404 | No existe un lote (no soft-deleted) con ese `codigo_lote`. |
| 409 | El lote ya está `Procesado` — terminal (RD-010: "un lote no puede ejecutarse dos veces"); no se reprocesa. Detectado al inicio, o al releer el estado tras perder la carrera optimista hacia `Procesando` (fila siguiente) si una ejecución concurrente ya lo completó. |
| 409 | El lote está `Procesando` — ya en ejecución. Detectado al inicio, o revelado por esa misma relectura si la ejecución concurrente que ganó la carrera todavía no terminó. |
| 409 | El lote perdió la carrera optimista hacia `Procesando` y, al releerlo, ya finalizó en `Error` — puede reintentarse. |
| 409 | El lote fue modificado durante el análisis: un consumo activo se insertó (o se eliminó) para ese mismo `lote_id` entre el gate de completitud y la relectura posterior a la lectura de la cadena importada — reintente (AI_ENGINE_SPEC.md §2.5, `LoteModificadoError`). |
| 422 | El lote no está completo (AI_ENGINE_SPEC.md §2.1: `cantidad_registros == 0`, o la cantidad de `consumos` activos no coincide con `cantidad_registros`). El `estado` del lote **no cambia** en este caso. |

**Respuesta 422** — cuerpo con ambos números comparados, para que el llamador entienda la brecha exacta:

```json
{
  "detail": {
    "detail": "el lote no está completo",
    "cantidad_registros": 5,
    "consumos_activos": 2,
    "motivo": "cantidad de consumos activos (2) no coincide con cantidad_registros declarada (5)"
  }
}
```

**Response 200** — `ProcesarLoteResponse`, en AMBOS desenlaces del umbral de completitud (DEC-004): que el lote termine `Procesado` o `Error` es un resultado legítimo de una ejecución exitosa del motor — la request en sí tuvo éxito. Solo las precondiciones de la tabla anterior son errores HTTP.

```json
{
  "estado_final": "Procesado",
  "informe": {
    "lote_id": "5b1b6e0e-...-...",
    "total_suministros": 3,
    "suministros_excluidos": 0,
    "fraccion_valida": "1",
    "umbral_cumplido": true,
    "hallazgos": [],
    "exclusiones": []
  },
  "duplicidades": {
    "lote_id": "5b1b6e0e-...-...",
    "periodos_conflictivos": [
      {
        "suministro_id": "8f2c...-...",
        "periodos": [
          {
            "consumo_id": "aaa1...-...",
            "fecha_inicio": "2024-01-15",
            "fecha_fin": "2024-02-15",
            "lote_id": "5b1b6e0e-...-...",
            "conflicto_con_consumo_id": "bbb2...-...",
            "conflicto_con_fecha_inicio": "2024-01-01",
            "conflicto_con_fecha_fin": "2024-01-31",
            "conflicto_con_lote_id": "cccc...-..."
          }
        ]
      }
    ],
    "lecturas_near_duplicate": [],
    "drift_lotes": []
  }
}
```

| Campo | Tipo | Notas |
|---|---|---|
| `estado_final` | string | `"Procesado"` (`fraccion_valida >= 0.95`, DEC-004) o `"Error"` (por debajo del umbral) — decidido únicamente por `informe` (Etapa 1); `duplicidades` (Etapa 2) nunca lo influye (DEC-005) |
| `informe.lote_id` | UUID | — |
| `informe.total_suministros` | integer | Suministros distintos con al menos un consumo activo en el lote |
| `informe.suministros_excluidos` | integer | Suministros con al menos un hallazgo V1-V7 (DEC-003: excluir + anotar, no abortar el lote) |
| `informe.fraccion_valida` | string (decimal) | `(total_suministros - suministros_excluidos) / total_suministros` — serializado como string, igual que `kwh`/`consumo_promedio_diario` en Gestión de Consumos (precisión de `Decimal`, no de `float`) |
| `informe.umbral_cumplido` | boolean | `fraccion_valida >= 0.95` (DEC-004) |
| `informe.hallazgos` | array | Uno por chequeo V1-V7 disparado: `{ "check": "V5", "suministro_id": "...", "consumo_id": "...", "motivo": "..." }` |
| `informe.exclusiones` | array | Uno por suministro excluido: `{ "suministro_id": "...", "motivos": ["V1: ...", "V7: ..."] }` — todos los motivos de ese suministro, no solo el primero |
| `duplicidades.lote_id` | UUID | — |
| `duplicidades.periodos_conflictivos` | array | Uno por suministro con al menos un solapamiento (AI_ENGINE_SPEC.md §5) — `periodos`: cada entrada marca UN período de ese suministro (`consumo_id`/`fecha_inicio`/`fecha_fin`/`lote_id`) contra el otro período con el que se solapa (`conflicto_con_*`). Calculado sobre TODOS los suministros del lote, sin filtrar por las exclusiones de la Etapa 1 (ver AI_ENGINE_SPEC.md §5, "Implementación v1"). Es la forma estable que la Etapa 3 (§6, ventanas de features) debe leer para no contar dos veces un período conflictivo |
| `duplicidades.lecturas_near_duplicate` | array | Uno por par de lecturas próximas (≤ `VENTANA_DIAS_LECTURA_NEAR_DUPLICATE` días, hoy 3) con `lectura_actual` idéntico: `{ "suministro_id": "...", "lectura_id": "...", "fecha_lectura": "...", "lectura_actual": "...", "conflicto_con_lectura_id": "...", "conflicto_con_fecha_lectura": "..." }` — informativo, no afecta el scoring |
| `duplicidades.drift_lotes` | array | Uno por OTRO lote (no `duplicidades.lote_id`) cuya cantidad de `consumos` activos ya no coincide con su `cantidad_registros` declarada: `{ "lote_id": "...", "codigo_lote": "...", "cantidad_registros": N, "consumos_activos": M, "diferencia": M-N }` — residuo detectable de una migración de `lote_id` vía upsert por clave natural (AI_ENGINE_SPEC.md §5); best-effort, no exhaustivo (ver esa sección) |

**Persistencia (implementación v1, ver AI_ENGINE_SPEC.md §4.2/§5 para el detalle completo):** ni `informe` ni `duplicidades` se persisten en base de datos — `resultados_ia` exige `modelo_ia_id`/`clasificacion` (`NOT NULL`), que no existen hasta la etapa de scoring (§9). El único efecto persistente de este endpoint es la transición de `lotes.estado`.

**Idempotencia y concurrencia**: una segunda solicitud sobre un lote ya `Procesado` responde 409 (RD-010). Una solicitud concurrente contra el MISMO lote `Pendiente`/`Error` se resuelve por una transición optimista (`UPDATE ... WHERE estado IN (...)`, AI_ENGINE_SPEC.md §2.3): solo una gana la carrera hacia `Procesando`, la otra recibe 409. Un lote que aterrizó en `Error` admite reintento (`Error → Procesando`, decisión de negocio 2026-07-13): una nueva solicitud vuelve a correr los chequeos desde cero.

## Contenido pendiente (otros contextos)

- Convenciones generales de la API (formato de URLs, versionado, paginación, filtros y ordenamiento).
- Formato estándar de request/response (JSON, envoltorios de éxito y error).
- Autenticación y autorización (JWT, roles, scopes) y su relación con SECURITY_SPEC.md.
- Endpoints por contexto delimitado restante: Inspecciones, Integración con RRHH. Gestión de Consumos ya no tiene pendientes propios: `Lectura`, `Lote de Facturación` y `Consumo` (§4.3, completo) tienen endpoints. El Motor de Inteligencia Energética tiene sus Etapas 1 y 2 documentadas (ver sección propia arriba); las Etapas 3-8 quedan pendientes. (Gestión de Clientes, Gestión de Suministros, Gestión de Consumos y Motor de Inteligencia Energética: ver secciones propias arriba.)
- Modelos de datos (schemas Pydantic) de entrada y salida por endpoint.
- Catálogo de códigos de error y formato estándar de mensajes de error.
- Estrategia de versionado de la API y política de compatibilidad hacia atrás.
- Especificación OpenAPI/Swagger generada y su ubicación de publicación.
- Límites de uso (rate limiting) aplicables por endpoint o por rol.
