# DATABASE_DESIGN.md

# EnergIA - Diseño de Base de Datos

| Versión | Fecha | Estado | Autor |
|---|---|---|---|
| 1.0.0 | 2026-07-06 | Aprobado | Rodrigo Zanin |

---

## 1. Qué es este documento (y qué no)

El DDL ejecutable vive en [`docker/postgres/init/`](../../docker/postgres/init/) — ese código es la fuente de verdad del esquema físico. Este documento no lo repite: documenta las **decisiones** detrás de ese DDL (qué se decidió, por qué, y qué trade-off se aceptó), para que revisar el código SQL alcance para saber qué existe, y este documento alcance para saber por qué existe así.

Motor: PostgreSQL 16 (ADR-004). UUID como PK vía `gen_random_uuid()` (built-in desde PostgreSQL 13, sin extensión). Auditoría completa y soft delete en todas las tablas (§4).

---

## 2. Cómo levantarla localmente

```bash
cp env.example .env        # ajustar credenciales si hace falta
docker compose up -d db
docker compose ps           # esperar "healthy"
```

Conexión:

```bash
psql -h localhost -p 5434 -U energia -d energia
# o, sin instalar psql en el host:
docker exec -it energia-db psql -U energia -d energia
```

El puerto host es **5434** (no 5432): tanto 5432 como 5433 ya están tomados por otra instancia de PostgreSQL en la máquina de desarrollo. Dentro del contenedor, Postgres sigue escuchando en su puerto estándar 5432; solo cambia el mapeo host:contenedor. Es configurable vía `POSTGRES_HOST_PORT` en `.env`.

Los scripts de `docker/postgres/init/` se ejecutan una única vez, en orden alfabético, la primera vez que el volumen de datos está vacío (comportamiento estándar de la imagen oficial `postgres`):

| Script | Contenido |
|---|---|
| `01_schema.sql` | Función de auditoría + las 24 tablas con sus PK/FK/CHECK/UNIQUE inline. |
| `02_constraints_indexes.sql` | Índices de performance (columnas de FK + patrones de consulta). |
| `03_staging.sql` | Crea el schema `staging`, vacío a propósito (§7). |
| `04_seed.sql` | Semilla de `categorias_tarifarias`. |

---

## 3. Inventario de tablas

24 tablas. Las primeras 15 existían en el borrador original (una renombrada); las 9 restantes son nuevas, agregadas para cubrir entidades de `DOMAIN_MODEL.md` que no tenían tabla.

| Tabla | Entidad de dominio (DOMAIN_MODEL.md) | Origen |
|---|---|---|
| `categorias_tarifarias` | CategoriaTarifaria (§7.3) | **Nueva** — antes era un VARCHAR libre en `suministros` |
| `clientes` | Cliente (§7.1) | Borrador |
| `suministros` | Suministro (§7.2) | Borrador |
| `lotes` | Lote de Facturación (§7.4) | Borrador |
| `modelos_ia` | Modelo IA (§8.6) + Versionado del Modelo (§10.5, fusionada) | Borrador, ampliada |
| `metricas_modelo` | Métricas del Modelo (§10.4) | **Nueva** |
| `reentrenamientos_modelo` | Reentrenamiento del Modelo (§10.3) | **Nueva** |
| `lecturas` | Lectura (§7.5) | Borrador |
| `consumos` | Consumo (§7.6) | Borrador — ahora particionada (§6) |
| `predicciones` | Predicción (§8.7) | **Nueva** |
| `resultados_ia` | ResultadoIA (§8.1) | Borrador |
| `feature_vectors` | Feature Vector (§8.5) | **Nueva** |
| `anomalias` | Anomalía (§8.2) | Borrador — **renombrada** (§4) |
| `ire` | Índice de Riesgo Energético / IRE (§8.3) | Borrador |
| `impacto_economico` | Impacto Económico Estimado / IEE (§8.4) | Borrador |
| `planes_inspeccion` | Plan de Inspección (§9.2) | **Nueva** |
| `ordenes_inspeccion` | Orden de Inspección (§9.1) | Borrador |
| `asignaciones_inspector` | Asignación de Inspector (§9.3) | **Nueva** — antes una columna suelta en `inspecciones` |
| `inspecciones` | Resultado de Inspección (§9.4) | Borrador |
| `hallazgos` | Hallazgo (§9.5) | Borrador |
| `recuperos_economicos` | Recupero Económico (§9.6) | Borrador |
| `tareas_rrhh` | Tarea RRHH (§9.7) | **Nueva** |
| `feedback_modelo` | Feedback del Modelo (§10.1) | Borrador |
| `datasets_etiquetados` | Dataset Etiquetado (§10.2) | **Nueva** |

Esto resuelve la deuda #3 de `PROJECT_MASTER_SPEC.md` ("~9-10 entidades sin tabla"): 9 tablas nuevas + 1 fusión documentada (VersionadoModelo → `modelos_ia`) cubren las entidades que faltaban.

### 3.1 Entidades ambiguas y la decisión tomada

`DOMAIN_MODEL.md` no siempre da suficiente detalle para traducir 1:1 a DDL. Estos son los casos donde hubo que decidir, y por qué:

- **Modelo IA vs. Versionado del Modelo (§8.6 y §10.5).** Ambas secciones describen la misma granularidad de dato (una fila = una versión publicada del motor). Se fusionaron en `modelos_ia` en vez de crear una tabla 1:1 redundante; el `estado` de la tabla usa el enum más rico de §10.5 (Activo/Obsoleto/Experimental/Retirado).
- **Métricas del Modelo (§10.4) vs. columnas de precisión en Modelo IA.** El borrador tenía `precision`/`recall`/`f1_score` directamente en `modelos_ia`. Se movieron a `metricas_modelo` (tabla aparte) porque un mismo modelo puede evaluarse más de una vez (validación de reentrenamiento, reevaluación periódica): guardar histórico de evaluaciones, no solo la última, es necesario para "comparar versiones" (§10.4).
- **Tarea RRHH (§9.7) no tiene tabla de Atributos en el dominio** — es la única entidad de todo el documento sin esa sección. Las columnas de `tareas_rrhh` se infirieron de sus Responsabilidades (crear, consultar estado, sincronizar) y de sus Estados, que sí están explícitos.
- **Inspección vs. Resultado de Inspección (§9.4).** El dominio no define una entidad "Inspección" separada con atributos propios; solo formaliza "Resultado de Inspección". La tabla `inspecciones` representa ejecución + resultado juntos (igual que el borrador original), y se le quitó `inspector_id` — ese dato ahora vive en `asignaciones_inspector`, evitando duplicarlo en dos tablas.
- **Consumo → Lectura (RD-018).** El dominio exige "debe existir una lectura asociada" pero no lista `lecturaId` entre los atributos de Consumo (§7.6). Se agregó `consumos.lectura_id` (FK nullable) para poder representar el invariante; es nullable porque los archivos históricos a recibir podrían no traer el detalle de lectura por período (§7).
- **Campos "Enum" sin lista de valores** (`planes_inspeccion.estado`, `datasets_etiquetados.origen`, `suministros.estado`). El dominio los declara como Enum pero nunca enumera los valores en ninguna sección. Se dejaron como `VARCHAR` sin `CHECK` en vez de inventar una lista no pedida — ver la regla de interpretación en §5.

---

## 4. Fix de deuda: `anomalías` → `anomalias`

El borrador nombraba la tabla `anomalías`, con tilde: un identificador no-ASCII que complica drivers, ORMs y herramientas de línea de comandos (algunos requieren comillas dobles en cada referencia). Se renombra a `anomalias`.

Esto aplica **solo a identificadores** (nombres de tabla/columna/constraint). Los *valores* de datos sí conservan la ortografía correcta del español donde el dominio los define así (`'Crítico'`, `'Atención'`, etc., en los `CHECK IN (...)`): son contenido, no identificadores, y PostgreSQL en UTF-8 no tiene ningún problema con eso.

---

## 5. Mapeo de invariantes de dominio (RD-xxx) a restricciones SQL

Regla de interpretación aplicada de forma consistente en todo `01_schema.sql`: si `DOMAIN_MODEL.md` titula una sección "Estados" / "Tipos" / "Clasificaciones" / "Prioridades" / "Escala" / "Etiquetas" con una lista cerrada, se traduce a `CHECK IN (...)`. Si el título es "Ejemplos" (lista abierta, no exhaustiva — así lo usa el propio dominio para Hallazgo e, implícitamente, para CategoriaTarifaria) o no hay lista en absoluto, la columna queda sin `CHECK`: inventar valores no documentados sería una decisión de diseño no pedida.

El esquema tiene **35 restricciones `CHECK`** (contadas por `grep -c "CONSTRAINT ck_" docker/postgres/init/01_schema.sql`), más 4 restricciones de unicidad que implementan invariantes de negocio directamente (no son `CHECK`, pero cumplen el mismo rol de integridad):

| RD / regla | Tabla.columna | Restricción |
|---|---|---|
| RD-013 | `lecturas.lectura_actual` | `CHECK (lectura_actual >= lectura_anterior)` |
| RD-014 | `lecturas.dias_facturados` | `CHECK (dias_facturados > 0)` |
| RD-050 | `lecturas.lectura_anterior`/`lectura_actual` | Sin `CHECK` — enforced solo en dominio (`Lectura.create()`, ver `backend/src/energia/contexts/consumos/domain/lectura.py`), deliberado: no se agregó una restricción SQL equivalente |
| RD-016 | `consumos.kwh` | `CHECK (kwh >= 0)` |
| RD-017 (parcial) | `consumos` | Índice único parcial `uq_consumos_suministro_periodo (suministro_id, fecha_inicio, fecha_fin) WHERE deleted_at IS NULL` — evita duplicar el mismo período exacto (y permite reimportar uno soft-deleted como fila nueva); no bloquea solapamientos parciales (requeriría `EXCLUDE` con `btree_gist`, no agregado, ver §6.4) |
| RD-018 | `consumos.lectura_id` | `FOREIGN KEY` (nullable, ver §3.1) |
| RD-020/021/022 | `resultados_ia.suministro_id/lote_id/modelo_ia_id` | `FOREIGN KEY` NOT NULL |
| RD-023 | `resultados_ia` | `UNIQUE (suministro_id, lote_id)` |
| RD-027 | `impacto_economico.monto_estimado` | `CHECK (monto_estimado >= 0)` |
| RD-030/031 | `ordenes_inspeccion.suministro_id/resultado_ia_id` | `FOREIGN KEY` NOT NULL |
| RD-033 | `ordenes_inspeccion` | Índice único parcial `WHERE estado NOT IN ('Finalizada','Cancelada')` |
| RD-037/038 | `inspecciones.resultado` | NOT NULL + `CHECK IN (...)` (§9.4 Resultados) |
| RD-040 | `recuperos_economicos.monto_recuperado` | `CHECK (monto_recuperado >= 0)` |
| RD-048 | `modelos_ia` | `UNIQUE (nombre, version)` |
| Invariante global §14 / §8.3 | `ire.valor` | `CHECK (valor BETWEEN 0 AND 100)` |
| AI_ENGINE_SPEC.md §9.3 (reviewer finding, WARNING, 2026-07-15) | `predicciones.score` | `CHECK (score BETWEEN 0 AND 1)` — score normalizado (min-max invertido por lote, DEC-013), sin cota previamente |

Invariantes **no traducibles a `CHECK`** porque dependen del estado de otra fila en otra tabla (`CHECK` en PostgreSQL no puede leer otras tablas; requeriría un trigger o validación de aplicación):

- RD-041 (`recuperos_economicos`: solo si la inspección está finalizada).
- RD-042 (`feedback_modelo`: la inspección asociada debe estar finalizada).
- RD-045 (`datasets_etiquetados`: solo desde inspecciones finalizadas).

Enums cerrados adicionales sin RD numerada, pero con lista explícita en el dominio: `lotes.estado` (§7.4), `resultados_ia.clasificacion`/`predicciones.clasificacion`/`feedback_modelo.prediccion_original` (§8.1, reutilizado), `anomalias.tipo`/`anomalias.severidad` (§8.2), `ordenes_inspeccion.prioridad`/`estado` (§9.1), `asignaciones_inspector.estado` (§9.3), `hallazgos.severidad` (reutiliza §8.2 — es un concepto transversal del lenguaje ubicuo, no redefinido por entidad), `tareas_rrhh.estado` (§9.7), `datasets_etiquetados.etiqueta` (§10.2), `reentrenamientos_modelo.estado` (§10.3), `modelos_ia.algoritmo`/`estado` (§8.6/§10.5), `metricas_modelo.*` (cota matemática 0-1 de precision/recall/f1/ROC-AUC/accuracy, propiedad objetiva sin RD asociada).

---

## 6. Particionado de `consumos`

### 6.1 Estrategia

`consumos` se particiona por `RANGE` sobre `fecha_inicio`, con particiones anuales 2022-2026 y una partición `DEFAULT`:

```
consumos_2022, consumos_2023, consumos_2024, consumos_2025, consumos_2026, consumos_default
```

Se justifica por el volumen declarado en el SRS (RNF-007: más de 500.000 suministros; ADR-004 estima que el particionado alcanza para ese volumen en PostgreSQL sin pasar a un warehouse dedicado). La partición `DEFAULT` existe porque **todavía no se conoce el rango real de años** de los datos históricos a recibir (contexto de esta migración: sin acceso a Oracle, alguien entregará archivos en un formato aún no definido — ver §7); cualquier fecha fuera de 2022-2026 cae ahí en vez de rechazar la carga, y se resegmentará cuando se conozca el rango real.

### 6.2 Trade-off: PK compuesta

PostgreSQL exige que todo índice único de una tabla particionada incluya la columna de particionado. Por eso `consumos` **no puede tener PK simple `(id)`**: su PK es `(id, fecha_inicio)`.

Se evaluaron dos caminos:

1. **PK compuesta `(id, fecha_inicio)`** (elegido). Cualquier FK futura hacia una fila puntual de `consumos` tendría que ser compuesta también: `(consumo_id, fecha_inicio)`.
2. **FK hacia `suministro_id + periodo`** en vez de hacia `consumos.id`, evitando el problema por completo.

Se eligió la opción 1 porque **hoy ninguna otra tabla referencia `consumos.id`**: `resultados_ia`, `feature_vectors` y `predicciones` enlazan por `suministro_id + lote_id` (el resultado del análisis es por lote procesado, no por período de consumo puntual). El trade-off queda documentado pero dormido — no se paga complejidad de grafo hasta que una tabla futura necesite esa FK puntual, y en ese momento la regla de PostgreSQL obliga a la clave compuesta de todos modos.

### 6.3 Triggers sobre tabla particionada

Los triggers de fila (`trg_consumos_set_updated_at`) se definen una sola vez sobre la tabla particionada padre; PostgreSQL 11+ los clona automáticamente a todas las particiones, existentes y futuras. Verificado en la validación de runtime (§8): un `UPDATE` sobre una fila de `consumos_2024` disparó el trigger correctamente.

### 6.4 Limitación conocida: solapamiento de períodos (RD-017)

El índice único parcial `uq_consumos_suministro_periodo (suministro_id, fecha_inicio, fecha_fin) WHERE deleted_at IS NULL` evita cargar dos veces el mismo período exacto (idempotencia de carga, incluyendo la reimportación de un período previamente soft-deleted como fila nueva — deuda #10 de `PROJECT_MASTER_SPEC.md`, resuelta antes de US-004), pero **no impide** que se carguen dos períodos que se solapan parcialmente (por ejemplo, 01/03-31/03 y 15/03-15/04 para el mismo suministro). Prevenir eso a nivel de base de datos requeriría un `EXCLUDE` constraint con rangos de fecha (extensión `btree_gist`). No se agrega en esta versión porque introduciría una extensión no pedida por el alcance actual; queda como mejora futura si la calidad de los datos históricos a recibir lo justifica. `ImportConsumos` (US-004) no implementa detección de solapamiento parcial por esta misma razón — ver `docs/03-architecture/API_SPEC.md` ("Contexto: Gestión de Consumos").

---

## 7. Schema `staging`

`docker/postgres/init/03_staging.sql` crea el schema `staging`, **vacío a propósito**. El contexto de esta migración es explícito: no hay acceso a Oracle, y una persona entregará archivos con el histórico de consumos en un formato todavía no definido (CSV, planilla, extracto de otro sistema — se desconoce). Diseñar tablas de staging ahora sería inventar una estructura sobre datos que no existen todavía. El schema reserva el espacio de nombres; las tablas se diseñan cuando se conozca el formato real (ver deuda nueva en `PROJECT_MASTER_SPEC.md`).

---

## 8. Validación de runtime

Ejecutado el 2026-07-06 con `docker compose -p energia-db-validate up -d` (puerto host de prueba 5544; el `docker-compose.yml` versionado usa 5434 por defecto porque 5432 y 5433 están ocupados en la máquina de desarrollo).

| Verificación | Resultado |
|---|---|
| Los 4 scripts de init corren sin error (`docker logs`, `grep -i error` → 0 coincidencias) | ✅ |
| `\dt` lista 24 tablas + 6 particiones de `consumos` | ✅ |
| Semilla de `categorias_tarifarias`: 5 filas (Residencial, Comercial, Industrial, Grandes Demandas, Alumbrado Público) | ✅ |
| Cadena de INSERT válida cliente → suministro → lote → consumo, aterriza en `consumos_2024` | ✅ |
| Consumo con `fecha_inicio` fuera de 2022-2026 aterriza en `consumos_default` | ✅ |
| `INSERT INTO ire (..., valor) VALUES (..., 150)` → **rechazado** por `ck_ire_valor_rango` | ✅ FALLA como se esperaba |
| `INSERT INTO consumos (..., kwh) VALUES (..., -10)` → **rechazado** por `ck_consumos_kwh_no_negativo` | ✅ FALLA como se esperaba |
| `INSERT INTO ire (..., valor) VALUES (..., 75)` → columna generada `nivel` calculada como `'Alto'` | ✅ |
| `UPDATE clientes SET localidad = ...` → `updated_at` pasa de `NULL` a la hora del update | ✅ |
| `UPDATE consumos SET ...` (fila en partición `consumos_2024`) → `updated_at` se actualiza (trigger clonado a la partición) | ✅ |
| FK inválida (`suministros.cliente_id` inexistente) → **rechazado** | ✅ FALLA como se esperaba |
| Segundo `resultados_ia` para el mismo `(suministro_id, lote_id)` → **rechazado** por `uq_resultados_ia_suministro_lote` (RD-023) | ✅ FALLA como se esperaba |

`docker compose -p energia-db-validate down -v` al final: contenedor, volumen y red eliminados por completo.

---

## 9. Estrategia de índices

`docker/postgres/init/02_constraints_indexes.sql` cubre dos categorías:

1. **Columnas de FK sin índice automático.** PostgreSQL solo indexa automáticamente PK y `UNIQUE`; toda columna de FK que no coincida con uno de esos dos queda sin índice si no se crea explícitamente. Se cubrieron todas.
2. **Patrones de consulta ya identificados en el borrador original** (§6 de la v0.x de este documento): `consumos` por `suministro_id + fecha_inicio`, `resultados_ia` por `lote_id` y por `suministro_id`, `ordenes_inspeccion` por `estado`, `inspecciones` por `fecha_inicio`.

Los índices sobre `consumos` (tabla particionada) se declaran una sola vez sobre el padre; PostgreSQL los propaga a todas las particiones.

---

## 10. Principios generales (heredados del borrador, sin cambios)

- Todo registro es auditable: `created_at`, `updated_at`, `deleted_at`, `created_by`, `updated_by` en las 24 tablas.
- Soft delete: ningún `DELETE` físico. Las claves naturales (`numero_cliente`, `numero_suministro`, `codigo_lote`, `numero_orden`) usan índices únicos parciales `WHERE deleted_at IS NULL`, para poder reutilizar el valor de negocio si el registro original fue soft-deleted. `lecturas` no tenía índice de este tipo en el borrador original; se agregó `uq_lecturas_suministro_fecha` (US-003) sobre la clave natural *compuesta* `(suministro_id, fecha_lectura)` — sin él, reimportar el mismo histórico duplicaba filas en lugar de actualizar/no-hacer-nada. `consumos` tampoco lo tenía: `uq_consumos_suministro_periodo` nació como `CONSTRAINT ... UNIQUE` simple (sin `WHERE deleted_at IS NULL`), la única clave natural de las 24 tablas que rompía esta convención — deuda #10 de `PROJECT_MASTER_SPEC.md`, convertida a índice único parcial antes de implementar US-004.
- `created_by`/`updated_by` son `UUID` sin FK: la tabla de usuarios todavía no existe (ver deuda "matriz de roles y permisos diferida" en `PROJECT_MASTER_SPEC.md`).

---

# FIN DATABASE_DESIGN.md
