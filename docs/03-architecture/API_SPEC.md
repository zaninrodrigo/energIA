# API Specification

| Versión | Fecha | Estado | Autor |
|---|---|---|---|
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

Todos los campos son opcionales *a nivel de schema* (permiten `null`) a propósito: un `numero_cliente` o `nombre` faltante es un rechazo de **dominio** (HTTP 200, reportado en `rejected`), no un error estructural de request.

**Response 200** — `ImportSummary`:

```json
{
  "created": 3,
  "updated": 0,
  "unchanged": 0,
  "rejected": [
    { "record": { "numero_cliente": "", "nombre": "Sin numero", "...": null }, "reasons": ["numero_cliente es obligatorio"] }
  ]
}
```

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

Todos los campos son opcionales *a nivel de schema* (permiten `null`) a propósito, igual que en Gestión de Clientes: un valor faltante o inválido es un rechazo de **dominio** o de **resolución de referencia** (HTTP 200, reportado en `rejected`), no un error estructural de request.

**Response 200** — `ImportSummary`:

```json
{
  "created": 2,
  "updated": 0,
  "unchanged": 0,
  "rejected": [
    { "record": { "numero_suministro": "SUM-300", "numero_cliente": "no-existe", "...": null },
      "reasons": ["cliente inexistente: numero_cliente='no-existe'"] },
    { "record": { "numero_suministro": "SUM-400", "categoria_tarifaria": "no-existe", "...": null },
      "reasons": ["categoria tarifaria inexistente: 'no-existe'"] }
  ]
}
```

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

Implementado en `backend/src/energia/contexts/consumos/`, hoy para dos entidades: `Lectura` (US-003) y `Lote de Facturación` (US-005) — `Consumo` (`DOMAIN_MODEL.md` §4.3) todavía no tiene endpoints propios; se agrega a este mismo contexto cuando su historia de usuario aterrice (ver `contexts/README.md`, "One package, staged entities"). Cubre la importación de lecturas históricas, la importación de lotes de facturación, y la consulta paginada de ambas. Los endpoints de `Lectura` están montados bajo `/api/v1/lecturas`; los de `Lote` bajo `/api/v1/lotes`.

Una lectura pertenece a exactamente un suministro (RD-015, `DOMAIN_MODEL.md` §7.5). El payload de importación referencia al suministro por su **clave natural**, `numero_suministro`, no por UUID: el endpoint la resuelve a UUID antes de intentar guardar el registro; si no existe, el registro se rechaza individualmente (ver más abajo), no la request completa.

**Identidad compuesta**: a diferencia de `Cliente`/`Suministro` (una sola clave natural), la identidad de una lectura es el par `(numero_suministro, fecha_lectura)` — un suministro acumula muchas lecturas en el tiempo, una por fecha. `docker/postgres/init/01_schema.sql` no tenía ningún índice único sobre esta clave antes de US-003; se agregó `uq_lecturas_suministro_fecha` (`suministro_id, fecha_lectura`, parcial `WHERE deleted_at IS NULL`) específicamente para que la importación sea idempotente — sin él, reimportar el mismo histórico duplicaba filas en lugar de actualizar/no-hacer-nada.

### POST /api/v1/lecturas/import

Importa lecturas desde un array JSON. Mismo patrón que `POST /api/v1/suministros/import`: es el adaptador de hoy para el puerto `LecturaSource` (ver `domain/ports.py`); un adaptador de archivo o el ETL de Oracle (ADR-004) pueden implementar el mismo puerto más adelante sin tocar dominio ni aplicación.

**Idempotencia**: cada registro se busca por su clave natural compuesta (`numero_suministro` resuelto a `suministro_id`, `fecha_lectura`) antes de decidir si crear, actualizar o no hacer nada. Reimportar el mismo payload no duplica filas: reporta `updated`/`unchanged` en lugar de `created`. Un `(numero_suministro, fecha_lectura)` repetido *dentro* del mismo payload se procesa de forma secuencial: la segunda ocurrencia actualiza sobre la primera, en lugar de entrar en conflicto.

**Rechazo individual**: un registro se rechaza de forma individual, sin abortar el resto del lote, en cualquiera de estos casos:

- `numero_suministro` faltante o el suministro referenciado no existe (`"suministro inexistente: numero_suministro=..."`).
- El registro viola un invariante de `Lectura`: `fecha_lectura` con formato inválido, `lectura_anterior`/`lectura_actual` faltante, no numérico, negativo (un medidor físico no retrocede — invariante solo de dominio, no una CHECK de la base) o con más de 3 decimales o más de 9 dígitos enteros (no entra en `numeric(12,3)`), `lectura_actual` menor que `lectura_anterior` (RD-013), o `dias_facturados` faltante, no entero o no mayor que cero (RD-014).

**Request body** — array JSON, cada elemento:

| Campo | Tipo | Obligatorio | Notas |
|---|---|---|---|
| `numero_suministro` | string | Sí (validado a nivel de dominio, no de schema) | Clave natural de `Suministro`; resuelta a `suministro_id` (UUID) por el puerto `SuministroDirectory` |
| `fecha_lectura` | string (fecha ISO 8601) | Sí (ídem) | Junto a `numero_suministro`/`suministro_id`, forma la clave natural compuesta. Se parsea con `date.fromisoformat` de Python: `YYYY-MM-DD` es la forma recomendada; la forma compacta `YYYYMMDD` también es válida |
| `lectura_anterior` | number | Sí (ídem) | `numeric(12,3)`: máx. 9 dígitos enteros, 3 decimales; no negativo |
| `lectura_actual` | number | Sí (ídem) | Ídem `lectura_anterior`; además debe ser ≥ `lectura_anterior` (RD-013) |
| `dias_facturados` | integer | Sí (ídem) | Debe ser mayor que cero (RD-014) |

Todos los campos son opcionales *a nivel de schema* (permiten `null`) a propósito, igual que en Gestión de Clientes/Suministros: un valor faltante o inválido es un rechazo de **dominio** o de **resolución de referencia** (HTTP 200, reportado en `rejected`), no un error estructural de request.

**Response 200** — `ImportSummary`:

```json
{
  "created": 2,
  "updated": 0,
  "unchanged": 0,
  "rejected": [
    { "record": { "numero_suministro": "no-existe", "fecha_lectura": "2024-01-15", "...": null },
      "reasons": ["suministro inexistente: numero_suministro='no-existe'"] },
    { "record": { "numero_suministro": "SUM-100", "fecha_lectura": "2024-03-15", "...": null },
      "reasons": ["lectura_actual no puede ser menor que lectura_anterior (100.000 < 200.000)"] }
  ]
}
```

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
  "rejected": [
    { "record": { "codigo_lote": "", "nombre": "Sin codigo" },
      "reasons": ["codigo_lote es obligatorio"] },
    { "record": { "codigo_lote": "LOTE-2024-03", "cantidad_registros": -5 },
      "reasons": ["cantidad_registros no puede ser negativo: -5"] }
  ]
}
```

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

## Contenido pendiente (otros contextos)

- Convenciones generales de la API (formato de URLs, versionado, paginación, filtros y ordenamiento).
- Formato estándar de request/response (JSON, envoltorios de éxito y error).
- Autenticación y autorización (JWT, roles, scopes) y su relación con SECURITY_SPEC.md.
- Endpoints por contexto delimitado restante: Motor de Inteligencia Energética, Inspecciones, Integración con RRHH. Dentro de Gestión de Consumos, también queda pendiente `Consumo`. (Gestión de Clientes, Gestión de Suministros y Gestión de Consumos/Lectura/Lote: ver secciones propias arriba.)
- Modelos de datos (schemas Pydantic) de entrada y salida por endpoint.
- Catálogo de códigos de error y formato estándar de mensajes de error.
- Estrategia de versionado de la API y política de compatibilidad hacia atrás.
- Especificación OpenAPI/Swagger generada y su ubicación de publicación.
- Límites de uso (rate limiting) aplicables por endpoint o por rol.
