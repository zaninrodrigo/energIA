# API Specification

| Versión | Fecha | Estado | Autor |
|---|---|---|---|
| 0.2.0 | 2026-07-06 | En progreso (Gestión de Clientes documentado) | Rodrigo Zanin |

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

## Contenido pendiente (otros contextos)

- Convenciones generales de la API (formato de URLs, versionado, paginación, filtros y ordenamiento).
- Formato estándar de request/response (JSON, envoltorios de éxito y error).
- Autenticación y autorización (JWT, roles, scopes) y su relación con SECURITY_SPEC.md.
- Endpoints por contexto delimitado restante: Suministros, Facturación por Lotes, Motor de Inteligencia Energética, Inspecciones, Integración con RRHH. (Gestión de Clientes: ver sección propia arriba.)
- Modelos de datos (schemas Pydantic) de entrada y salida por endpoint.
- Catálogo de códigos de error y formato estándar de mensajes de error.
- Estrategia de versionado de la API y política de compatibilidad hacia atrás.
- Especificación OpenAPI/Swagger generada y su ubicación de publicación.
- Límites de uso (rate limiting) aplicables por endpoint o por rol.
