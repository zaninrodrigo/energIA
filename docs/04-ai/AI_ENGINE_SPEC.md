# Especificación del Motor de Inteligencia Energética

| Versión | Fecha | Estado | Autor |
|---|---|---|---|
| 1.0.0 | 2026-07-14 | Aceptado | Rodrigo Zanin |

> **Estado del documento.** Las 18 decisiones marcadas en el cuerpo como **DECISIÓN (DEC-xxx)**
> y consolidadas en la §15 fueron validadas por **Rodrigo Zanin el 2026-07-14**: 17 según su
> recomendación por defecto, y **DEC-017** según su alternativa (IEE expresado en kWh, sin
> monetización en v1). Los valores numéricos de este documento (pesos, umbrales,
> hiperparámetros) son definitivos a partir de esa validación.
>
> **Corrección de consistencia interna en §2 (estados del lote); decisiones sin cambios
> (2026-07-14).** §2.1-§2.3 tenían una inconsistencia interna: describían al motor actuando
> sobre lotes en `Procesado` (terminal, RD-010), pero DEC-004 exige transicionar a `Error`
> cuando la validación cae por debajo del 95 % — una transición imposible desde un estado
> terminal. Corregido: el disparador es un lote `Pendiente` (o `Error`, reintento) completo; es
> el propio motor quien transiciona a `Procesando` y luego a `Procesado`/`Error`. Las 18
> decisiones (DEC-001..018, §15) no se modifican.

## Resumen ejecutivo

El **Motor de Inteligencia Energética** analiza cada lote de facturación completo y produce,
por suministro, un `ResultadoIA` con su `IRE` (0-100), su `IEE` y sus `Anomalías`, para
**priorizar inspecciones**. No decide fraude: una anomalía es una señal para revisión humana
(RN-008, RD-025).

Es un **motor híbrido de tres ramas** (ADR-005): reglas de negocio explícitas, análisis
estadístico e Isolation Forest no supervisado, que convergen en el IRE. Corre **por lote**
(ADR-007), disparado por un lote `Pendiente` (o `Error`, reintento) **completo** (§2.1-§2.2),
en un **proceso worker** aislado (ADR-006, ADR-002), con un presupuesto de **menos de 10
minutos** (RNF-001) para volúmenes de hasta **500.000 suministros** (RNF-007).

| | |
|---|---|
| **Entrada** | `consumos`, `lecturas`, `suministros`, `categorias_tarifarias` de un lote `Pendiente`/`Error` completo (§2.1) |
| **Salida** | `resultados_ia`, `predicciones`, `anomalias`, `ire`, `impacto_economico`, `feature_vectors` |
| **Contrato de esquema** | `docker/postgres/init/01_schema.sql` (no se modifica; brechas en §16) |
| **Decisiones validadas** | 18 (DEC-001 a DEC-018), aceptadas el 2026-07-14 (DEC-017 por alternativa), consolidadas en §15 |

---

## 1. Propósito y alcance

### 1.1 Qué es

El motor transforma consumos históricos en información accionable (DOMAIN_MODEL §8): detecta
patrones anómalos, estima riesgo (IRE) e impacto económico (IEE), y alimenta el ranking de
inspecciones. Es el núcleo analítico del sistema y el bounded context `motor`
(`contexts/README.md` — nombre de paquete, superseding el placeholder `intelligence_engine`
usado antes de que este contexto tuviera código; Etapa 1 —US-006 + US-010— y Etapa 2 —US-007—,
ya implementadas).

### 1.2 Qué NO es

- **No es un detector de fraude.** RN-008 / RD-025: "la detección de una anomalía no constituye
  evidencia de fraude". El motor produce señales; el veredicto es de la inspección humana.
- **No modifica datos operativos** (DOMAIN_MODEL §8): solo genera conocimiento. Nunca escribe
  sobre `consumos`, `lecturas` ni `suministros`.
- **No es supervisado en v1.** No existe dataset de fraude etiquetado (ADR-005, "cold-start
  explícito"). Los modelos supervisados son roadmap v2.0.

### 1.3 Relación con las decisiones arquitectónicas

| ADR | Consecuencia sobre el motor |
|---|---|
| ADR-005 | Enfoque híbrido; Isolation Forest como algoritmo principal; tensión de explicabilidad (§10.4) |
| ADR-007 | Ejecución batch por lote; disparo en lote `Pendiente`/`Error` completo (§2.1-§2.2); RN-013 lote completo |
| ADR-006 | Cómputo pesado aislado en proceso worker; monolito modular |
| ADR-002 | Python/Scikit-Learn; GIL ⇒ scoring CPU-bound ⇒ multiprocessing/joblib |

---

## 2. Disparo y orquestación

### 2.1 Disparador

El motor se ejecuta **una vez por lote**, disparado por un lote en estado `Pendiente` (o
reintentado desde `Error`, §2.2) cuya **carga de datos está completa** (ADR-007; DOMAIN_MODEL
§7.4). RN-013 exige lote completo antes de correr la IA, porque la comparación de cohorte
(RD-009: "la IA solo compara suministros de categorías equivalentes") necesita la cohorte
completa. RF-005 lo formaliza: "ejecutar el Motor al finalizar el procesamiento de un lote" —
"finalizar el procesamiento" significa que la **carga** terminó de completarse, no que el lote
ya esté `Procesado`: esa transición es precisamente lo que el motor decide al correr (§2.2).

**Definición de completitud.** Un lote está listo para el motor cuando:

1. `cantidad_registros > 0`, **y**
2. la cantidad de `consumos` activos (no soft-deleted) con ese `lote_id` es exactamente igual a
   `cantidad_registros`.

`cantidad_registros == 0` **nunca** está listo, sin importar el conteo de consumos: un lote que
no declara cuántos registros espera no terminó de describirse, no es "trivialmente completo con
cero registros". Un desajuste entre ambos números —de más o de menos— tampoco está listo; el
motor reporta ambos valores (`cantidad_registros` declarada y el conteo real de consumos
activos), para que quien dispara el análisis entienda la brecha exacta. Implementado en
`backend/src/energia/contexts/motor/domain/completitud.py`; expuesto como `422` con ambos
números en el cuerpo de la respuesta (`docs/03-architecture/API_SPEC.md`).

> **Tensión de fuente (resuelta por DEC-001, 2026-07-14).** RN-005 exigía originalmente "cada
> nuevo **consumo** procesado deberá ser analizado automáticamente", granularidad por consumo,
> en contradicción con la granularidad **por lote** de RN-013 + ADR-007. DEC-001 ratificó la
> granularidad **por lote**, y RN-005 fue reformulada en consecuencia
> (`docs/01-business/BUSINESS_ANALYSIS.md` §15): el disparo ocurre al completar la carga del
> lote, alineado con RN-013 y ADR-007. Ver **DEC-001** (§15).

### 2.2 Máquina de estados del lote

```
Pendiente ──▶ Procesando ──▶ Procesado   (terminal, RD-010)
                  │
                  └────────▶ Error ──▶ Procesando   (reintento, decisión 2026-07-13)
```

El diagrama de transiciones no cambia — es exactamente el que `consumos/domain/lote.py` ya
declaraba (`ALLOWED_TRANSITIONS`) antes de que el motor existiera para accionarlo. Lo que esta
sección corrige es la **semántica** de cada estado: la máquina describe el ciclo de vida del
**análisis**, no (solo) el de la importación.

| Estado | Significado |
|---|---|
| `Pendiente` | El lote fue importado; su carga de datos está en curso o a la espera de análisis. |
| `Procesando` | El motor está ejecutando sus chequeos sobre este lote ahora mismo. |
| `Procesado` | El análisis terminó **exitosamente** (terminal, RD-010: "un lote no puede ejecutarse dos veces"). |
| `Error` | Falló la carga **o** el análisis. Admite reintento: `Error → Procesando` (decisión de negocio, 2026-07-13; PROJECT_MASTER_SPEC #12; `contexts/README.md`). |

El disparador del motor (§2.1) es un lote `Pendiente` (o `Error`, vía reintento) **completo**: es
el propio motor quien lo transiciona a `Procesando` y, según el resultado de la validación de
integridad (§4), a `Procesado` o a `Error` — nunca al revés. `Procesado` sigue siendo terminal
(RD-010); `Lote.estado` nunca se acepta desde el payload de importación (`contexts/README.md`),
así que el único camino a `Procesado` es esta ejecución real del motor.

### 2.3 Idempotencia (RD-010)

`resultados_ia` tiene `UNIQUE (suministro_id, lote_id)` (RD-023): existe a lo sumo un
`ResultadoIA` por suministro y lote — esa garantía llega con la etapa de scoring, cuando el
motor empiece a escribir en esa tabla (§4.2). Para Etapa 1 (esta implementación), la
idempotencia se sostiene en `Lote.estado` mismo: `Procesado` es terminal (RD-010,
`ALLOWED_TRANSITIONS`, §2.2) — pedir procesar un lote ya `Procesado` se rechaza (`409`, sin
reprocesar), y el estado terminal lo garantiza.

Un pedido concurrente contra el MISMO lote `Pendiente`/`Error` se resuelve con una transición
**optimista** (`UPDATE lotes SET estado = ... WHERE estado IN (...)`, verificando `rowcount`):
solo una de las dos solicitudes concurrentes gana la carrera hacia `Procesando`; la otra recibe
`409` (`backend/src/energia/contexts/motor/infrastructure/lote_procesamiento.py`). Ver
**DEC-002** para la política de reproceso deliberado (nueva versión de modelo, todavía v2).

### 2.4 Ejecución en worker aislado

El scoring es CPU-bound y el GIL limita el paralelismo intra-proceso (ADR-002). El motor corre
en un **proceso worker separado** (ADR-006), fuera del pool de hilos de la API, para no
degradar la latencia de los dashboards concurrentes. El paralelismo real se obtiene con
multiprocessing/joblib sobre el scoring de Isolation Forest (§12).

**Etapa 1, esta implementación: síncrona en el request.** La validación de integridad (§4) es
SQL-bound (consultas set-based, sin cómputo pesado en Python) y corre **síncronamente dentro del
request HTTP** (`POST /api/v1/motor/lotes/{codigo_lote}/procesar`,
`backend/src/energia/contexts/motor/presentation/routes.py`) — no hay proceso worker todavía. El
aislamiento en worker que describe el párrafo anterior sigue siendo el objetivo (ADR-006 no
cambia); se activa cuando aterricen las etapas CPU-bound (3-6: features, estadística, Isolation
Forest) — la única etapa implementada hoy no lo necesita.

### 2.5 Semántica de fallo

| Situación | Qué persiste | Estado resultante |
|---|---|---|
| Fallo antes de escribir resultados | Nada (transacción no confirmada) | El lote queda auditable en su estado previo; el reintento reprocesa el lote entero |
| Fallo a mitad de escritura | La escritura se hace **por lote transaccional**, no por suministro suelto | Rollback total; sin resultados parciales |
| Suministro con datos inválidos | Se excluye del scoring y se anota (§4) | El resto del lote se procesa |
| Fallo entre `Procesando` y `Procesado`/`Error` (Etapa 1, esta implementación) | Nada — un único `commit()` al final de todo el flujo (§2.1-§2.3), nunca uno intermedio tras la transición a `Procesando` | El rollback automático de la sesión revierte también esa transición; el lote queda en su estado previo (`Pendiente`/`Error`), nunca atascado en `Procesando` |
| Inserción concurrente de un consumo para el MISMO lote entre el gate de completitud (§2.1) y la relectura posterior a `fetch_chain` (Etapa 1, esta implementación) | Nada — `LoteModificadoError` (`409`) aborta antes de construir el informe; el único cambio de esta transacción (la transición a `Procesando`) se revierte con el mismo rollback | El lote vuelve a su estado previo (`Pendiente`/`Error`); el reintento vuelve a evaluar la completitud desde cero |
| Fallo en Etapa 2 (detección de duplicidades, §5) | Nada — misma transacción única que el resto del flujo; una excepción acá revierte también la transición a `Procesando`, exactamente el mismo diseño que un fallo de `fetch_chain` (fila anterior): consistencia sobre resultados parciales, deliberado, no accidental | El lote vuelve a su estado previo (`Pendiente`/`Error`); el reintento reprocesa el lote entero (incluida la Etapa 2) |

El motor persiste el conjunto del lote de forma atómica: no deja un lote medio analizado. Ver
**DEC-003** (outcome de validación) para el tratamiento fino por suministro.

**Guardia de completitud atómica y su ventana residual (implementación v1, Etapa 1).** Tras
`fetch_chain`, dentro de la MISMA transacción que certificó la completitud en el gate (§2.1), el
motor vuelve a contar los consumos activos del lote y lo compara contra el conteo certificado;
un desajuste levanta `LoteModificadoError` (fila anterior). Esto no cierra la ventana de carrera
por completo: entre ese recount y el `commit()` final (`presentation/routes.py`) queda una
**micro-ventana residual** — bajo `READ COMMITTED` (el nivel por defecto de esta transacción),
una inserción que aterrice DESPUÉS del recount y ANTES del commit no queda cubierta por este
chequeo. **Nota operativa:** no importar consumos hacia un lote mientras se dispara su
procesamiento. **Alternativa evaluada y descartada:** subir toda la transacción de
`ProcesarLote` a aislamiento `REPEATABLE READ` cerraría la ventana por completo (la transacción
vería una foto fija desde su inicio, haciendo el recount redundante) — descartada por ahora para
no introducir fallos de serialización/reintentos de transacción en una ruta que hoy no los
necesita; un recount explícito y acotado es más simple y suficientemente seguro para el volumen
esperado.

---

## 3. Pipeline: visión general

```
Lote Pendiente/Error completo
   │
   ▼
[1] Validación de integridad (US-006)      ── excluye/anota suministros inválidos
   ▼
[2] Detección de duplicados (US-007)       ── solapamientos, repeticiones entre lotes
   ▼
[3] Generación de features (US-008)        ──▶ feature_vectors
   ▼
[4] Indicadores estadísticos (US-009)      ── z-score, percentiles de cohorte, IQR
   ▼
[5] Reglas de negocio                       ── umbrales explícitos ──▶ anomalias (rama reglas)
   ▼
[6] Isolation Forest (US-011)              ──▶ predicciones, resultados_ia.score_anomalia
   ▼
[7] Composición del IRE                     ──▶ ire (8 factores §8.3) + anomalias (rama estadística/ML)
   ▼
[8] Impacto Económico Estimado             ──▶ impacto_economico
```

El primer recuadro es la PRECONDICIÓN de entrada (§2.1), no un estado que el pipeline reciba ya
resuelto: es el propio pipeline quien produce el `Procesado`/`Error` final (§2.2) al terminar,
nunca al revés.

Las etapas 4, 5 y 6 son las **tres ramas** del híbrido (ADR-005; TO-BE de BUSINESS_ANALYSIS §5).
Convergen en la etapa 7.

---

## 4. Etapa 1 — Validación de integridad (US-006)

US-006: "validar la integridad de los datos importados para evitar errores en el análisis".
Aunque el esquema ya impone restricciones en la carga (CHECK, FK, índices únicos), varias
condiciones solo son detectables **en tiempo de análisis** cruzando la cadena importada.

### 4.1 Chequeos concretos

| # | Chequeo | Fuente | Detalle |
|---|---|---|---|
| V1 | Consumo sin lectura asociada | RD-018 | `consumos.lectura_id IS NULL` (la FK es nullable; los históricos pueden no traer detalle) |
| V2 | Coherencia kwh vs delta de lectura | RD-013 | Con `lectura_id` presente: comparar `kwh` contra `lectura_actual − lectura_anterior` dentro de una tolerancia |
| V3 | Coherencia de días facturados | RD-014 | `consumos.dias_facturados` vs `lecturas.dias_facturados`; ambos > 0 |
| V4 | Continuidad de períodos por suministro | RD-017 | Huecos o solapamientos entre `(fecha_inicio, fecha_fin)` consecutivos del mismo suministro |
| V5 | Solapamiento parcial de períodos | RD-017 | El índice único evita duplicar el período **exacto**, pero no solapamientos parciales (comentario del DDL; DATABASE_DESIGN §6.4). Detectable acá |
| V6 | Suministro sin categoría tarifaria válida | RD-009 | Necesaria para la comparación de cohorte; sin ella el suministro no es comparable |
| V7 | Consumo negativo o días ≤ 0 | RD-016, RD-014 | Redundante con los CHECK, pero se verifica por defensa en profundidad |

### 4.2 Contrato de salida

> **DEC-003 — Outcome de un chequeo fallido (aceptada según recomendación, 2026-07-14).**
> **Excluir** el suministro del scoring y **anotar** el motivo (en `resultados_ia.observaciones` del lote o en un log de
> calidad), sin abortar el lote; abortar (marcar `Error`) solo si la fracción de registros
> inválidos supera un umbral. Alternativas: (a) anotar sin excluir y dejar que el scoring
> absorba el ruido; (b) fallar el lote ante cualquier inválido. Impacto: cobertura del análisis
> vs. calidad del scoring.

> **DEC-004 — Umbral de completitud del lote (aceptada según recomendación, 2026-07-14).** Se
> permite el análisis si al menos **95 %** de los suministros del lote pasan la validación; por debajo, marcar `Error` y exigir
> recarga. Alternativas: 90 %, 99 %, o sin umbral. Impacto: robustez de la cohorte (RD-009,
> ADR-007 "cohorte completa") vs. tolerancia operativa.

> **Persistencia del informe (implementación v1, Etapa 1 aislada).** `resultados_ia` exige
> `modelo_ia_id` (`NOT NULL`, FK a `modelos_ia`) y `clasificacion` (`NOT NULL`, CHECK con los 4
> valores de §8.1) — ninguno de los dos existe todavía cuando solo corrió la Etapa 1: no hay
> modelo IA entrenado, no hay score que clasificar. Escribir una fila de `resultados_ia`
> "solo-Etapa-1" implicaría inventar un `modelo_ia_id`/`clasificacion` que no representarían nada
> real, así que esta implementación no lo hace. **Decisión v1**: el `InformeValidacion` completo
> (hallazgos V1-V7, exclusiones por suministro con motivo, fracción válida, veredicto de umbral)
> se devuelve en el cuerpo de la respuesta de
> `POST /api/v1/motor/lotes/{codigo_lote}/procesar` (`docs/03-architecture/API_SPEC.md`), **sin
> persistencia en base de datos** — la persistencia del informe llega con la etapa de scoring
> (§9, cuando exista un `modelo_ia_id`/`clasificacion` reales que rellenar). El único efecto
> persistente de esta implementación es la transición de `lotes.estado`
> (`Pendiente`/`Error` → `Procesando` → `Procesado`/`Error`, §2.2).

---

## 5. Etapa 2 — Detección de duplicados (US-007)

Los duplicados **exactos** ya los previene la base: índices únicos parciales sobre
`(suministro_id, fecha_inicio, fecha_fin)`, `(suministro_id, fecha_lectura)`, etc. Por eso, en
esta etapa "duplicado" significa lo que la base **no** previene:

| Tipo | Definición | Fuente |
|---|---|---|
| Solapamiento de períodos | Dos consumos del mismo suministro con períodos que se cruzan sin ser idénticos | RD-017 |
| Drift de conteo entre lotes | Un lote cuya cantidad de `consumos` activos ya no coincide con su `cantidad_registros` declarada, porque una fila migró hacia otro lote (o desde otro lote) — solo lotes con cantidad declarada (`cantidad_registros > 0`); `cantidad_registros == 0` es el default legítimo de "conteo aún no declarado", no algo de lo que pueda haber drift | — |
| Near-duplicate de lecturas | Lecturas del mismo suministro con fechas muy próximas y valores idénticos | §7.5 |

> **Corrección de consistencia interna (implementación v1, 2026-07-14).** La versión original de
> esta tabla definía "consumo repetido entre lotes" como "el mismo período reimportado en un lote
> distinto" — un estado **estructuralmente imposible** de alcanzar hoy: el upsert por clave
> natural de `consumos` (`uq_consumos_suministro_periodo` sobre `(suministro_id, fecha_inicio,
> fecha_fin) WHERE deleted_at IS NULL`, `SqlAlchemyConsumoRepository.save`) hace que reimportar el
> MISMO período actualice la fila existente en el lugar, incluyendo su `lote_id`, que migra hacia
> el lote que reimportó — nunca existen dos filas, una por lote, para el mismo período. El residuo
> DETECTABLE de esa migración es el drift de conteo: el lote que perdió la fila (o el que la ganó)
> termina con una cantidad de `consumos` activos que ya no coincide con su propia
> `cantidad_registros` declarada. Eso es lo que esta etapa detecta y anota.

**Outcome aceptado (DEC-005, 2026-07-14):** las duplicidades no borran datos (el motor no modifica
operativos, §1.2); se **anotan** y el período conflictivo se marca para no contarse dos veces en
las ventanas de features (§6), en lugar de excluir el más reciente, el más antiguo, o promediar.
DEC-005 se mantiene sin cambios respecto de la validación original.

**Implementación v1 (2026-07-14, Etapa 2 implementada).**

- **Alcance por suministro.** El solapamiento de períodos y el near-duplicate de lecturas se
  calculan para TODOS los suministros del lote en curso, sin filtrar por las exclusiones de la
  Etapa 1 (V1-V7) — decisión deliberada: V4/V5 (Etapa 1) y esta etapa detectan el MISMO fenómeno
  (solapamiento de períodos) en dos alcances distintos: V5 excluye al suministro del scoring de
  ESTE lote apenas el solapamiento toca los datos del lote actual; la marca de esta etapa es la
  anotación durable que debe sobrevivir para las ventanas de features de lotes FUTUROS (§6). Si
  esta etapa omitiera los suministros que V5 acaba de excluir, la marca del caso que precisamente
  motiva su existencia — un suministro excluido en este lote por el solapamiento que Etapa 3
  necesita marcado — nunca se generaría. El drift de conteo entre lotes, en cambio, nunca dependió
  de esta exclusión: reporta la integridad de OTROS lotes, no marcas sobre los suministros de este.
- **Drift de conteo, alcance best-effort.** Solo son descubribles los lotes OTROS que todavía
  comparten, en el estado actual de `consumos`, al menos un período activo con algún suministro
  del lote en curso. Un lote que pierde el ÚLTIMO vínculo compartido (todos sus períodos para esos
  suministros migraron hacia otro lado, sin dejar rastro soft-deleted porque el upsert nunca borra)
  deja de ser descubrible por esta vía — señal best-effort, no exhaustiva.
- **Ventana de near-duplicate de lecturas.** `VENTANA_DIAS_LECTURA_NEAR_DUPLICATE = 3` días
  (`backend/src/energia/contexts/motor/domain/duplicidades.py`) — constante de implementación, no
  una de las decisiones DEC-0xx; calibración pendiente contra datos reales
  (`PROJECT_MASTER_SPEC.md` #8), igual que `TOLERANCIA_KWH_LECTURA`/la contigüidad de un día de
  V4 (§4.1).
- **Nunca cambia el resultado de la Etapa 1.** `duplicidades` es un campo adicional de la
  respuesta de `POST /api/v1/motor/lotes/{codigo_lote}/procesar`; el umbral del 95 % (DEC-004)
  sigue decidiéndose únicamente por el `InformeValidacion` de la Etapa 1. Ver
  `docs/03-architecture/API_SPEC.md` para el contrato completo de los nuevos campos.

---

## 6. Etapa 3 — Generación de features (US-008, RF-004)

US-008/RF-004: "generar variables (features) para el modelo de IA". Se materializa un
`FeatureVector` por suministro y lote, persistido en `feature_vectors` (`features` jsonb,
`version` varchar). La lista v1 concreta se deriva de los "Ejemplos de Features" de
DOMAIN_MODEL §8.5, ampliada con las señales que el IRE (§8.3) exige.

### 6.1 Conjunto de features v1

| # | Identificador (English) | Fórmula / definición | Ventana | Cold-start (historia corta) |
|---|---|---|---|---|
| F1 | `avg_consumption` | media de `kwh` | 12 meses | media de lo disponible; flag si < 3 períodos |
| F2 | `max_consumption` | máximo de `kwh` | 12 meses | disponible |
| F3 | `min_consumption` | mínimo de `kwh` | 12 meses | disponible |
| F4 | `stddev_consumption` | desvío estándar de `kwh` | 12 meses | `null` si < 3 períodos |
| F5 | `pct_change_prev_period` | (`kwh_t` − `kwh_{t-1}`) / `kwh_{t-1}` | 2 períodos | `null` si no hay período previo |
| F6 | `pct_change_yoy` | (`kwh_t` − `kwh_{t-12}`) / `kwh_{t-12}` | mismo mes año anterior | `null` si < 12 meses de historia |
| F7 | `moving_avg_6m` | media móvil de `kwh` | 6 meses | media de lo disponible |
| F8 | `moving_avg_12m` | media móvil de `kwh` | 12 meses | media de lo disponible |
| F9 | `deviation_from_baseline` | (`kwh_t` − `moving_avg_12m`) / `stddev_consumption` | 12 meses | `null` si F4 nulo |
| F10 | `zero_consumption_streak` | racha de períodos consecutivos con `kwh = 0` | histórico | 0 |
| F11 | `trend_slope` | pendiente de regresión lineal de `kwh` vs tiempo | 12 meses | `null` si < 4 períodos |
| F12 | `seasonality_index` | `kwh_t` / media del mismo mes en años previos | histórico | 1.0 (neutro) |
| F13 | `peer_ratio` | `kwh_t` / mediana de la cohorte (categoría × localidad) | lote actual | ratio vs categoría sola si el peer group es chico |
| F14 | `supply_age_days` | `fecha_inicio − suministros.fecha_alta` | — | disponible |
| F15 | `prior_anomaly_count` | anomalías históricas del suministro | histórico | 0 |
| F16 | `billed_days` | `consumos.dias_facturados` | período actual | disponible |
| F17 | `categoria_tarifaria` | id de categoría (one-hot o embedding en scoring) | — | requerida (V6) |

### 6.2 Manejo de nulos y cold-start por suministro

El cold-start es **por suministro**, no solo por modelo: un suministro recién dado de alta no
tiene 12 meses de historia. Política aceptada (DEC-006/DEC-007, 2026-07-14):

- Features de ventana larga con historia insuficiente ⇒ `null` explícito en el jsonb (no 0, que
  el modelo interpretaría como señal real).
- El scoring (§9) usa imputación por la mediana de la cohorte para los nulos, y registra una
  feature booleana `is_cold_start` para que el IRE no penalice la falta de historia como anomalía.

Ver **DEC-006** (conjunto exacto de features) y **DEC-007** (tamaños de ventana y mínimos de
historia). Mapeo a esquema: todo el vector va en `feature_vectors.features` (jsonb); `version`
identifica la versión del contrato de features (p. ej. `"v1"`), respetando
`UNIQUE (suministro_id, lote_id, version)`.

### 6.3 Exclusión de períodos conflictivos de las ventanas (nota, DEC-005)

Todo período marcado en `periodos_conflictivos` (Etapa 2, §5) se excluye de **toda** ventana de
agregación de la Etapa 3 — **ambos lados del par**, no solo el más reciente: DEC-005 no define
una regla de canonicidad que prefiera un lado sobre el otro (§5), así que esta implementación no
inventa una. La exclusión ocurre en la capa de aplicación (`ProcesarLote._generar_features`,
`backend/src/energia/contexts/motor/application/procesar_lote.py`), **antes** de que la historia
llegue a las funciones puras de dominio (`backend/src/energia/contexts/motor/domain/features.py`):
el dominio no tiene noción de "conflictivo" en absoluto, simplemente refleja la historia que
recibe — ver el docstring de ese módulo y
`test_conflicted_period_exclusion_is_a_prior_filtering_concern`
(`backend/tests/unit/contexts/motor/domain/test_features.py`).

**Consecuencia, documentada honestamente:** si el ÚNICO período de un suministro en el lote que
se está procesando es uno de los lados de un par conflictivo, ese suministro no recibe
`FeatureVector` en esta corrida (no hay período "actual" no conflictivo al cual anclar las
ventanas) — política v1, no un error.

**Nota (FIX 1, hallazgo de revisión, CRITICAL, 2026-07-15) — F10 `zero_consumption_streak` es la
ÚNICA excepción a "el dominio no tiene noción de conflictivo".** Excluir de las ventanas un período
conflictivo pero con consumo REAL Y DISTINTO DE CERO (p. ej. un mes facturado dos veces) puentea el
hueco que deja: dos períodos en cero que solo quedan adyacentes porque el período real intermedio
fue eliminado parecen una racha ininterrumpida, inflando `zero_consumption_streak` por encima de lo
que las lecturas físicas realmente muestran — un `streak` persistido en `3` alcanza el disparador
de severidad Alta de R1 (`>= 3`, §8) cuando el valor real es `2`. Una racha de cero consumo es un
enunciado sobre LECTURAS CRUDAS OBSERVADAS: un período conflictivo, aunque se excluya de toda otra
ventana, sigue evidenciando que el suministro fue facturado con consumo ese período si su propio
`kwh` es distinto de cero (corta la racha); un período conflictivo cuyo propio `kwh` es cero no
evidencia nada y la racha continúa a través de él. Por esto, `construir_feature_vector`
(`backend/src/energia/contexts/motor/domain/features.py`) recibe una SEGUNDA historia, SIN
filtrar (`historial_suministro_completo`), usada EXCLUSIVAMENTE para F10 — el resto de las
features (F1-F9, F11-F17) sigue leyendo la historia filtrada sin cambios. Ver el docstring del
módulo y `test_zero_streak_bridges_conflicted_nonzero_period_using_unfiltered_history`/
`test_zero_streak_continues_through_a_conflicted_period_that_is_itself_zero`
(`backend/tests/unit/contexts/motor/domain/test_features.py`).

### 6.4 Implementación (estado, 2026-07-15)

Etapas 3 y 4 (§6/§7) están **implementadas**: `domain/features.py` (funciones puras F1-F17 +
`is_cold_start` + `zscore_self`), `infrastructure/features_data_source.py` (lecturas set-based),
`infrastructure/feature_vector_repository.py` (upsert en `feature_vectors`), integradas en
`ProcesarLote` (`application/procesar_lote.py`). Desviaciones honestas frente a este documento:

- **F1 (`avg_consumption`) y F8 (`moving_avg_12m`) son matemáticamente idénticas** bajo la
  definición de ventana v1 (ambas son la media de los últimos 12 períodos, incluyendo el actual)
  — no es un error, es una observación: el documento las nombra para roles distintos (F1 como
  señal de exposición general para el IRE, §10.1; F8 específicamente como término base de F9),
  pero ambas claves se escriben igual en el jsonb, tal como exige el contrato de identificadores.
- **Etapas 3-4 corren incondicionalmente**, sin importar si el lote termina `Procesado` o `Error`
  por el umbral de DEC-004 — la misma política que Etapa 2 (duplicidades) ya estableció. No
  estaba explícito en ninguna dirección en este documento; se documenta como una decisión v1
  razonada, no un supuesto silencioso (ver el docstring de `_generar_features`).
- **Precisión numérica:** `Decimal` (tipo de `consumos.kwh`) se convierte a `float` en el borde
  del dominio, antes de cualquier cómputo — jsonb no tiene un tipo decimal de punto fijo. Pérdida
  de precisión despreciable a esta magnitud (`numeric(12,3)` cabe cómodo en `float64`).
- **F13/percentile_peer/iqr_outlier_flag** reutilizan el mismo umbral de cohorte (DEC-008,
  `COHORTE_MINIMA = 10`, `COHORTE_FALLBACK_MINIMA = 3`) — F13 nunca queda `null` por cohorte
  chica (siempre resuelve contra al menos la categoría propia), a diferencia de los dos
  indicadores de Etapa 4, que sí quedan `null` bajo `COHORTE_FALLBACK_MINIMA`.
- **F12 (`seasonality_index`) distingue "sin dato de año anterior" de "media de año anterior
  igual a cero"** (FIX 2, hallazgo de revisión, WARNING, 2026-07-15): sin ningún período del mismo
  mes en un año anterior, el valor neutro `1.0` (cold-start, DEC-007) sigue aplicando sin cambios;
  pero si SÍ existe dato del mismo mes en un año anterior y su media da exactamente `0`, ahora es
  `null` (división por cero real), no `1.0` — el valor anterior enmascaraba un salto de `0` a
  varios miles de kWh como "perfectamente estacional".
- **F11 (`trend_slope`) regresiona contra meses calendario transcurridos desde el primer período
  de la ventana, no contra la posición en la lista** (FIX 4, hallazgo de revisión, WARNING,
  2026-07-15): para una ventana mensual contigua el resultado es idéntico al anterior (ambos
  cuentan "1 unidad" por mes), pero un hueco calendario real entre dos períodos (un suministro sin
  lectura/facturación por varios meses) ahora pesa proporcionalmente en la pendiente, en vez de
  tratarse como si fuera simplemente "el siguiente" período contiguo.
- **F14 (`supply_age_days`) nunca es negativo** (FIX 5(a), hallazgo de revisión, 2026-07-15): si
  `suministros.fecha_alta` es POSTERIOR al `fecha_inicio` del período evaluado (inconsistencia de
  datos — un suministro no puede facturar antes de darse de alta), el valor es `null`, no un
  conteo de días negativo.

---

## 7. Etapa 4 — Indicadores estadísticos (US-009)

US-009: "calcular indicadores estadísticos para enriquecer el análisis". Es la **rama
estadística** del híbrido. Complementa —no duplica— al ML: opera sobre reglas transparentes y
auditables (fuerte para RN-012), mientras que Isolation Forest captura lo multivariado.

| Indicador | Definición | Base de comparación |
|---|---|---|
| `zscore_self` | (`kwh_t` − media propia) / desvío propio | historia del **propio** suministro |
| `percentile_peer` | percentil de `kwh_t` dentro de su cohorte | categoría × localidad (RD-009) |
| `iqr_outlier_flag` | `kwh_t` fuera de [Q1 − 1.5·IQR, Q3 + 1.5·IQR] | cohorte |

Estos indicadores producen directamente `Anomalías` de tipo `Desvío Estadístico` (§8.2) cuando
superan sus umbrales, y alimentan factores del IRE (§10). **DEC-008 (aceptada según
recomendación, 2026-07-14):** el peer group es categoría × localidad, con fallback a categoría
sola. El umbral exacto de "cohorte chica" que dispara ese fallback se calibra con el tamaño
típico de cohorte de los datos reales, que hoy se desconocen (staging pendiente,
PROJECT_MASTER_SPEC #8) — esa calibración numérica queda abierta; la elección del peer group no.

**Implementación (estado, 2026-07-15):** implementado en
`domain/features.enriquecer_con_indicadores_cohorte` — cohorte = suministros ANALIZADOS del lote
en curso (no excluidos por Etapa 1), agrupados por categoría × localidad; `percentile_peer` es la
fracción de la cohorte con `kwh <= kwh_t`; Q1/Q3 (para el IQR) usan interpolación lineal, el mismo
método de `PERCENTILE_CONT` de PostgreSQL, para que un chequeo manual por psql coincida sin
corrección. `zscore_self` (§7) se calcula sobre la historia **completa** disponible del propio
suministro (no solo los últimos 12 meses que usan F1-F9), `null` si tiene menos de 3 períodos
(DEC-007) — ver §6.4 para las desviaciones honestas compartidas con la Etapa 3.

**Nota (FIX 3, hallazgo de revisión, WARNING, 2026-07-15) — `iqr_outlier_flag` usa cuartiles
"leave-one-out" (LOO), a diferencia de `percentile_peer`.** Calcular Q1/Q3 INCLUYENDO el propio
`kwh` del suministro evaluado deja que un pico genuino infle su propio límite superior,
enmascarando exactamente el outlier que debería marcar: en una cohorte de fallback de 3 miembros
`[10, 20, 10000]`, los límites inclusivos nunca marcan `10000` (su propio valor arrastra Q3 —y por
lo tanto el límite superior— hacia arriba), mientras que `percentile_peer` para ese mismo vector
reporta correctamente `1.0` — una contradicción interna entre ambos indicadores que este ajuste
resuelve. `percentile_peer` se mantiene inclusivo a propósito (es el estadístico estándar basado
en rango, correcto tal cual); solo los límites del IQR excluyen al suministro evaluado de su
propio cálculo. Ver `domain/features.py`'s `enriquecer_con_indicadores_cohorte` y
`test_iqr_outlier_flag_uses_leave_one_out_bounds_not_masked_by_self`/
`test_iqr_outlier_flag_none_when_loo_cohort_below_minimum`
(`backend/tests/unit/contexts/motor/domain/test_features.py`).

---

## 8. Etapa 5 — Reglas de negocio

La **rama de reglas** (ADR-005) codifica los casos conocidos y explícitos: cada disparo traza a
una regla nombrada, satisfaciendo RN-012 de forma directa. Genera `Anomalías` de tipos concretos
del catálogo cerrado de §8.2.

| Regla | Condición (aceptada v1, DEC-009 — recalibrada 2026-07-15, calibración v1.1) | Tipo de Anomalía (§8.2) | Severidad |
|---|---|---|---|
| R1 | `zero_consumption_streak ≥ 3` con suministro activo | `Persistencia Anómala` | Alta |
| R2 | `pct_change_prev_period ≤ −60 %` en el período ACTUAL (un solo "cliff", ya no "sostenida ≥ 2 períodos" — ver §8.2) | `Caída Brusca` | Alta |
| R3 | `pct_change_prev_period ≥ +200 %` en un período | `Incremento Brusco` | Media |
| R4 | `percentile_peer ≤ percentil 5` **Y** `peer_ratio ≤ 0,4` de su cohorte (conjunción, ya no percentil solo — ver §8.2) | `Consumo Muy Bajo` | Media |
| R5 | `percentile_peer ≥ percentil 95` **Y** `peer_ratio ≥ 2,5` de su cohorte (conjunción, ya no percentil solo — ver §8.2) | `Consumo Muy Alto` | Media |
| R6 | `deviation_from_baseline`, valor absoluto ≥ 3 | `Desvío Estadístico` | Media |

Los umbrales (−60 %, +200 %, 3 períodos, percentiles 5/95, peer_ratio 0,4/2,5) son **candidatos**,
no verdades: dependen del comportamiento real de los datos. Ver **DEC-009**. R2 y R4/R5 fueron
recalibrados el 2026-07-15 (calibración v1.1) contra evidencia sintética real (§8.2) — siguen
siendo candidatos, ahora respaldados por una corrida medida en vez de solo teoría. `Patrón
Irregular` queda como tipo reservado para la rama ML (§9), no para reglas.

> **Persistencia de las anomalías (implementación v1, Etapa 5 aislada — mismo razonamiento que
> §4.2).** `anomalias.resultado_ia_id` es `NOT NULL` (FK a `resultados_ia`,
> `docker/postgres/init/01_schema.sql`): persistir una fila de `Anomalía` exige una fila de
> `ResultadoIA`, que a su vez exige `modelo_ia_id` (`NOT NULL`, FK a `modelos_ia`) y
> `clasificacion` (`NOT NULL`, CHECK con los 4 valores de §8.1) — ninguno de los dos existe todavía
> con solo las Etapas 1-5 corridas: no hay modelo IA entrenado (§9), no hay score que clasificar
> (§10). Escribir una fila de `anomalias` "solo-reglas" implicaría inventar un
> `modelo_ia_id`/`clasificacion` que no representarían nada real, así que esta implementación no
> lo hace. **Decisión v1**: Etapa 5 es **cómputo + reporte únicamente**, igual que la Etapa 1
> (§4.2). Cada disparo de regla (`regla`, `tipo`, `severidad`, `descripcion` con la evidencia
> numérica que lo originó) se devuelve en el campo `reglas` del cuerpo de la respuesta de
> `POST /api/v1/motor/lotes/{codigo_lote}/procesar` (`docs/03-architecture/API_SPEC.md`), **sin
> persistencia en base de datos** — la persistencia de `anomalias` converge en la Etapa 7 (§10),
> en el mismo escritura atómica de `ResultadoIA` que agrega `modelo_ia_id`/`clasificacion` reales:
> ver §14 (mapeo etapa → tabla), donde `anomalias` ya aparece asociada a "[5]/[7]" precisamente por
> esta convergencia. No se fabrican filas de `modelos_ia` ni una `clasificacion` inventada.

### 8.1 Implementación (estado, 2026-07-15)

Etapa 5 está **implementada**: `domain/reglas.py` (funciones puras R1-R6 + `evaluar_reglas` +
`ResumenReglas`/`InformeReglas`), integrada en `ProcesarLote` (`application/procesar_lote.py`)
inmediatamente después de la Etapa 4, sobre los suministros **no excluidos** por la Etapa 1 que
recibieron un `FeatureVector` esta corrida — reutilizando ese vector ya construido, sin recalcular
ninguna feature ni indicador.

- **R2 recalibrada 2026-07-15 (calibración v1.1) — ya no necesita un segundo período de
  historia.** La condición original, "sostenida ≥ 2 períodos", exigía el `pct_change` del período
  INMEDIATAMENTE ANTERIOR además del actual; la corrida empírica de §8.2 midió CERO disparos en
  2.321 evaluaciones reales, incluyendo la anomalía `sudden_drop` plantada para ejercitarla: una
  "caída brusca" real es un escalón (un mes de "cliff", luego el consumo se SOSTIENE al nuevo nivel
  bajo, sin otra caída de −60 % mes a mes), así que el segundo período que la condición exigía
  nunca aparece. R2 ahora dispara con el `pct_change_prev_period` (F5, §6.1) del período ACTUAL
  ÚNICAMENTE — igual que R3 para la dirección opuesta —; la intención de "sostenida" (marcar que el
  nivel bajo persiste) queda cubierta por R6 (`deviation_from_baseline`), que sigue marcando los
  meses posteriores mientras el consumo se mantenga fuera de la línea base histórica.
- **Los nulos nunca disparan una regla.** Cada regla narra su propio guard de nulidad (el mismo
  patrón que `resumir_features` ya usa para leer `features: dict[str, object]`, §6.4): un
  suministro cold-start (F4/F9/`percentile_peer`/`peer_ratio` nulos por historia insuficiente,
  DEC-006/DEC-007) no dispara ninguna regla, sin necesidad de un caso especial explícito para
  `is_cold_start` — la guarda es implícita en cada chequeo de nulidad.
- **Un mismo suministro puede disparar varias reglas a la vez, sin deduplicar.** Son señales
  independientes (RN-012): un suministro con racha larga Y percentil extremo Y desvío extremo
  dispara R1 + R4/R5 + R6, las tres, no solo "la peor".
- **`suministros.estado` no tiene lista de valores enumerada** (a diferencia de `clientes.estado`,
  DDL): R1 compara contra el string exacto `'Activo'` (el default sembrado), documentado como el
  único contrato que existe hoy sobre ese campo.
- **R4/R5 recalibradas 2026-07-15 (calibración v1.1) — conjunción con `peer_ratio` (F13), ya no
  percentil solo.** Con una cohorte de fallback de tamaño exactamente `COHORTE_MINIMA_FALLBACK = 3`
  (DEC-008) y valores empatados o con un máximo/mínimo claro, el miembro con el `kwh` más alto (o
  más bajo) SIEMPRE cae en `percentile_peer = 1.0` (o `0.0`) por construcción ("fracción del grupo
  ≤ mi propio valor", inclusivo a propósito, §7) — la corrida empírica de §8.2 midió que esto
  disparaba R5 en 142 de 144 casos sin que hubiera nada anómalo, solo la posición relativa dentro
  de un grupo chico, mientras que R4 resultaba estructuralmente inalcanzable (percentil ≤ 1 %
  exige ≥ 100 miembros en una sola cohorte). Ambas reglas ahora EXIGEN, además del percentil,
  que `peer_ratio` (`kwh_actual` / mediana de la cohorte) confirme la magnitud relativa — un
  suministro en el extremo del percentil de una cohorte chica, pero con un consumo similar al
  resto (`peer_ratio` cercano a 1), ya no dispara; nulo en CUALQUIERA de los dos componentes
  significa "no evaluable", nunca un disparo. Sigue siendo un candidato a recalibración cuando
  existan datos reales (`PROJECT_MASTER_SPEC.md` #8), igual que el resto de los umbrales de esta
  sección.

### 8.2 Calibración empírica (datos sintéticos, seed 42, escala small)

#### 8.2.1 Calibración v1.0 (2026-07-15) — condiciones originales de DEC-009

Corrida real de las 6 reglas contra las 24 lotes mensuales del dataset sintético determinístico
(seed 42, `backend/src/energia/tools/synthetic/`, mismo dataset de §13.1), sobre las 6 anomalías
plantadas (manifiesto) — cada suministro evaluado en sus 24 lotes (2.321 evaluaciones
suministro-lote en total):

| Suministro plantado | Tipo | Mes de inicio | Regla(s) esperada(s) | Resultado medido |
|---|---|---|---|---|
| SYN-S42-SUM-00005 | `spike_leve` | 2023-01 | Ninguna (sub-umbral, por diseño) | Ninguna regla de incremento; R5 dispara ese mes (percentil 100 %, cohorte chica) — captura incidental, no por diseño (ver más abajo) |
| SYN-S42-SUM-00014 | `sudden_drop` | 2023-05 | R2 | **Ninguna regla dispara, en los 24 meses** — ver hallazgo R2 más abajo |
| SYN-S42-SUM-00024 | `spike` | 2023-07 | R3 | R3 + R5 + R6 disparan juntas, exactamente en 2023-07 (triple señal, sin deduplicar) |
| SYN-S42-SUM-00032 | `sudden_drop_leve` | 2022-10 | Ninguna (sub-umbral) | Ninguna regla dispara en ningún mes relacionado con la caída (confirmado) |
| SYN-S42-SUM-00049 | `zero_consumption_streak` | 2022-11 (racha ≥ 3 recién en 2023-01) | R1 | R1 dispara exactamente en 2023-01 (racha=3) y 2023-02 (racha=4) — nunca antes, cuando la racha era < 3 |
| SYN-S42-SUM-00073 | `gradual_decline` | 2023-05 | Ninguna al inicio | Ninguna regla dispara en ningún mes relacionado con la caída (confirmado) |

**Falsos positivos y hallazgos honestos de calibración (evidencia real, no solo teórica):**

- **R1 y R3 se comportan exactamente como se especifica**: 2/2 disparos de R1 (ambos sobre
  SUM-00049, ningún falso positivo) y 1/1 disparo de R3 (sobre SUM-00024, ningún falso positivo)
  en las 2.321 evaluaciones.
- **R2 nunca dispara, en ningún mes, para ningún suministro del dataset (0 de 2.321
  evaluaciones).** La condición literal "sostenida ≥ 2 períodos" (`pct_change_prev_period` del
  período ACTUAL **y** del INMEDIATAMENTE ANTERIOR, ambos ≤ −60 %) no la satisface la forma de
  `inject_sudden_drop` (`tools/synthetic/anomalies.py`): esa caída ocurre en UN solo mes (una
  caída abrupta que después se sostiene AL NUEVO NIVEL, sin otra caída pronunciada mes a mes) — el
  mes siguiente al de la caída no vuelve a mostrar un `pct_change` ≤ −60 % (compara nivel bajo
  contra nivel bajo, variación normal), así que el segundo período que R2 exige nunca aparece. Esto
  no es un defecto de implementación: R1/R2/R3 fueron implementadas siguiendo la condición literal
  de DEC-009 tal como está escrita en la tabla de §8, verificada exhaustivamente con valores
  calculados a mano (`tests/unit/contexts/motor/domain/test_reglas.py`). Es una **discrepancia real
  entre la definición de la regla y la forma de la anomalía "caída brusca" que hoy planta el
  generador sintético** — candidato explícito a revisar en la próxima calibración: o R2 se
  redefine (p. ej. "el período actual solo, sin exigir el anterior") o el generador sintético gana
  una variante de caída de dos escalones. Se documenta aquí, no se “arregla” silenciosamente
  ninguno de los dos lados.
- **R4 nunca dispara, en ningún mes, para ningún suministro (0 de 2.321 evaluaciones).**
  Estructuralmente inalcanzable con los tamaños de cohorte de este dataset: el mínimo de una
  cohorte de tamaño `N` tiene `percentile_peer = 1/N` (definición inclusiva, §7); para que
  `1/N ≤ 0.01` (R4) se necesita `N ≥ 100` miembros en la MISMA cohorte (categoría × localidad o su
  fallback de categoría sola, DEC-008) — un tamaño que este dataset (100 suministros repartidos
  entre varias categorías/localidades) nunca alcanza en una sola cohorte.
- **R5 dispara exactamente 6 de 100 suministros, en LOS 24 MESES sin excepción** (144 disparos
  totales) — pero solo 2 de esos 144 disparos coinciden con el mes de inicio de una anomalía
  plantada (SUM-00005 en 2023-01, SUM-00024 en 2023-07); el resto (142, ≈ 98,6 % de los disparos de
  R5) caen sobre suministros sin ninguna anomalía plantada ese mes — **falsos positivos
  estructurales, a una tasa constante del 6 % de la cohorte cada mes**, la contracara exacta del
  hallazgo de R4: con cohortes chicas, el miembro de mayor consumo SIEMPRE queda en el percentil
  100 (`percentile_peer` inclusivo, "fracción del grupo ≤ mi propio valor"), sin importar si su
  consumo es genuinamente anómalo o simplemente el más alto de un grupo de 3-10 suministros. R4 y
  R5, con el MISMO par de umbrales (1 %/99 %) y el MISMO tamaño mínimo de cohorte (DEC-008), quedan
  así asimétricos: R4 nunca dispara, R5 dispara siempre — evidencia concreta de que el par
  1 %/99 % + `COHORTE_FALLBACK_MINIMA = 3` necesita recalibrarse contra el tamaño real de cohorte
  que exista en producción (`PROJECT_MASTER_SPEC.md` #8), no contra el tamaño sintético de este
  dataset.
- **R6 dispara 1/1 vez, coincidiendo exactamente con el disparo de R3 (SUM-00024, 2023-07)** — sin
  falsos positivos en las 2.321 evaluaciones.

**Reproducibilidad:** corrida completa contra `energia_scratch` (base descartable, nunca
`energia`/`energia_test`), sembrada vía `python -m energia.tools.synthetic --scale small --seed 42`
y procesada lote por lote (`POST /api/v1/motor/lotes/{codigo_lote}/procesar`) para los 24
`LOTE-SYN-S42-YYYY-MM` del dataset.

#### 8.2.2 Calibración v1.1 (2026-07-15) — recalibración de R2 y R4/R5

Misma corrida (mismo dataset determinístico seed 42/escala small, mismo arnés contra
`energia_scratch`, mismas 2.321 evaluaciones suministro-lote), esta vez con R2 redefinida como
"cliff" de un solo período y R4/R5 en conjunción con `peer_ratio` (ver §8, §8.1). Objetivo:
confirmar que R2 ahora captura `sudden_drop` en su mes de inicio y que R5 deja de disparar
falsamente sobre cohortes chicas, sin introducir falsos positivos nuevos.

| Suministro plantado | Tipo | Mes de inicio | Resultado medido (v1.1) |
|---|---|---|---|
| SYN-S42-SUM-00005 | `spike_leve` | 2023-01 | Ninguna regla dispara (antes: R5 disparaba incidentalmente; la conjunción con `peer_ratio` 1,98, sub-umbral, lo corrige) |
| SYN-S42-SUM-00014 | `sudden_drop` | 2023-05 | **R2 dispara exactamente en 2023-05** (caída de −63 %, mes de inicio) — antes: 0 disparos en 24 meses |
| SYN-S42-SUM-00024 | `spike` | 2023-07 | R3 + R5 + R6 disparan en 2023-07 (sin cambios); además R2 dispara en 2023-08 — ver nota abajo |
| SYN-S42-SUM-00032 | `sudden_drop_leve` | 2022-10 | Ninguna regla dispara (confirmado, sub-umbral) |
| SYN-S42-SUM-00049 | `zero_consumption_streak` | 2022-11 (racha ≥ 3 recién en 2023-01) | R1 dispara en 2023-01 y 2023-02 (sin cambios); R4 dispara en 2022-11, 2022-12, 2023-01 y 2023-02 (4 meses, percentil 4 %, `peer_ratio` 0,00) — antes: 0 disparos de R4 en todo el dataset; además R2 dispara en 2022-11 — ver nota abajo |
| SYN-S42-SUM-00073 | `gradual_decline` | 2023-05 | Ninguna regla dispara (confirmado) |

**Conteo total (2.321 evaluaciones):** R1 = 2, R2 = 3, R3 = 1, R4 = 4, R5 = 1, R6 = 1 (12 disparos
en total, 0 duplicados entre reglas más allá de lo esperado por diseño).

**Falsos positivos por regla (disparos sobre suministros SIN ninguna anomalía plantada, en
CUALQUIER mes):** R1 = 0, R2 = 0, R3 = 0, R4 = 0, R5 = 0, R6 = 0 — cero, en las 6 reglas. Todos
los 12 disparos medidos caen exclusivamente sobre los 6 suministros con una anomalía realmente
plantada.

**Expectativas confirmadas (ninguna falló):**

- **R1 sin cambios**: 2/2 disparos sobre SUM-00049, exactamente cuando `racha ≥ 3` (2023-01,
  2023-02) — idéntico a la calibración v1.0.
- **R2 ahora captura `sudden_drop` en su mes de inicio** (SUM-00014, 2023-05, caída de −63 %) — el
  hallazgo central que motivó esta recalibración (§8.2.1) queda resuelto: de 0/2.321 a capturar la
  ÚNICA anomalía que existe para ejercitarla, en el mes correcto.
- **R3 sin cambios**: 1/1 disparo sobre SUM-00024 (2023-07), sin falsos positivos.
- **Los "leves" (`spike_leve`, `sudden_drop_leve`) y `gradual_decline` siguen silenciosos**: 0
  disparos de cualquier regla relacionados con esas 3 anomalías — confirma que el ajuste de R2/R4/
  R5 no ensanchó los umbrales lo suficiente como para capturar señales deliberadamente
  sub-umbral (diseño intencional de esas 3 anomalías, `tools/synthetic/anomalies.py`).
- **R5 colapsa de 142 falsos positivos a 0** (objetivo: "~0-2"): con la conjunción `peer_ratio`, R5
  dispara UNA sola vez en todo el dataset (SUM-00024, 2023-07, `peer_ratio` 5,07) — exactamente la
  única vez que el disparo coincide con una anomalía real. Los 142 disparos estructurales sobre
  cohortes chicas sin nada anómalo (§8.2.1) desaparecen por completo.
- **R4 deja de estar estructuralmente muerto**: pasa de 0 a 4 disparos, los 4 sobre el mismo
  suministro (SUM-00049) durante su racha de cero consumo (`percentile_peer` 4 %, `peer_ratio`
  0,00 — genuinamente el consumo más bajo posible respecto de sus pares) — sin ningún falso
  positivo.

**Hallazgo honesto adicional (no forzado, no ocultado): R2 dispara 2 veces más allá del caso
"canónico" `sudden_drop`, ambas sobre suministros que SÍ tienen una anomalía plantada (no son
falsos positivos), pero de un tipo distinto al que R2 apunta:**

- **SUM-00024 (`spike`), 2023-08**: el mes siguiente al pico (2023-07) revierte de inmediato a la
  línea base (`inject_spike`'s propio diseño, `tools/synthetic/anomalies.py`: "reverting
  immediately afterward") — esa reversión ES, en los datos reales, una caída de un solo período de
  −79 %, un "cliff" genuino según la nueva definición de R2, aunque el manifiesto no lo etiquete
  como `sudden_drop`. No es un error de R2: es la consecuencia matemática correcta y esperada de
  que un pico que revierte de golpe ES, visto desde el mes siguiente, una caída brusca real.
- **SUM-00049 (`zero_consumption_streak`), 2022-11**: el primer mes de la racha de cero
  (consumo normal → 0) es, por definición, una caída de un solo período del −100 % — otro "cliff"
  genuino, esta vez producido por el INICIO de una racha de cero en vez de por un `sudden_drop`
  plantado.

Ambos casos son disparos CORRECTOS de R2 contra caídas de un solo período genuinas en los datos —
no falsos positivos ni artefactos del ajuste — simplemente no coinciden con el tipo de anomalía
"canónico" que el manifiesto asocia a cada suministro. Se documentan aquí explícitamente, sin
forzar ni ocultar el resultado: R2, tal como quedó redefinida, es sensible a CUALQUIER caída de un
solo período ≥ 60 %, sea cual sea su causa subyacente (una `sudden_drop` plantada, la reversión de
un `spike`, o el inicio de una racha de cero) — un comportamiento consistente con su definición
literal, no un bug.

**Reproducibilidad:** mismo arnés que §8.2.1 (`energia_scratch`, nunca `energia`/`energia_test`),
mismo dataset determinístico (seed 42, escala small, `python -m energia.tools.synthetic`),
procesado lote por lote contra los `domain/reglas.py` recalibrados de esta sección.

---

## 9. Etapa 6 — Isolation Forest (US-011, RF-006)

La **rama de ML** (ADR-005). Isolation Forest es no supervisado: no necesita etiquetas (que no
existen), y su costo ~O(n log n) paralelizable encaja en RNF-001/RNF-007 mejor que LOF u
One-Class SVM (ADR-005). Persiste en `predicciones` y `resultados_ia`.

### 9.1 Estrategia de entrenamiento

> **DEC-010 — Modelo global vs. por categoría (aceptada según recomendación, 2026-07-14).** **Un
> modelo por categoría tarifaria** cuando la categoría tenga volumen suficiente (p. ej. ≥ 1.000 suministros), con
> fallback a un **modelo global** para categorías chicas. Fundamento: RD-009 exige comparar
> dentro de la cohorte; un modelo por categoría lo respeta por construcción. Alternativa: modelo
> único global con la categoría como feature (F17). Impacto: precisión de cohorte vs. cantidad de
> modelos a versionar y mantener (`modelos_ia`).

### 9.2 Hiperparámetros

| Parámetro | Valor aceptado | Resolución |
|---|---|---|
| `contamination` | `0.03` (≈ 3 % anómalos esperados) | **DEC-011** (aceptada, 2026-07-14) |
| `n_estimators` | `200` | **DEC-012** (aceptada, 2026-07-14) |
| `max_samples` | `256` (default de la técnica) | **DEC-012** (aceptada, 2026-07-14) |
| `random_state` | fijo (reproducibilidad, RD-028) | — |
| Escalado de features | `RobustScaler` (resistente a outliers, que son justo la señal) | incluido en **DEC-006** (aceptada, 2026-07-14) |

### 9.3 Normalización del score a 0-100

`decision_function` de Isolation Forest devuelve un score donde valores más negativos son más
anómalos (puede ser negativo, por eso `resultados_ia.score_anomalia` es `numeric(8,4)` sin CHECK
≥ 0). Se persiste:

- **crudo** en `resultados_ia.score_anomalia` (trazabilidad),
- **normalizado a [0,1]** en `resultados_ia.probabilidad` (CHECK 0-1) y `predicciones.score`,
- la contribución del ML al IRE se calcula desde el normalizado (§10).

> **DEC-013 — Método de normalización (aceptada según recomendación, 2026-07-14).** Escala
> **min-max invertida por lote** sobre los scores del lote (0 = menos anómalo, 100 = más
> anómalo), calibrada para que la `contamination` esperada caiga sobre el umbral de "Atención".
> Alternativa descartada: mapeo por percentiles del score histórico. Impacto: estabilidad del IRE
> entre lotes de distinto tamaño.

### 9.4 Persistencia y versionado

Cada corrida escribe una fila en `modelos_ia` (o referencia la versión activa) con `algoritmo =
'Isolation Forest'`, `estado = 'Activo'`, `version`. `resultados_ia.modelo_ia_id` referencia esa
versión (RD-022: "debe registrarse la versión del modelo utilizada"). Las métricas de evaluación
van en `metricas_modelo` cuando existan etiquetas para calcularlas (v2; §13).

### 9.5 Reentrenamiento

**Política v1: modelo (re)ajustado por lote sobre la ventana histórica vigente, sin supervisión.**
El pipeline completo de Aprendizaje Continuo (DOMAIN_MODEL §10: feedback → dataset etiquetado →
reentrenamiento supervisado → publicación) es **v2**, porque depende de inspecciones finalizadas
que aún no existen (RD-045; ADR-005 cold-start). En v1 no hay `feedback_modelo` ni
`datasets_etiquetados` poblados. Ver **DEC-018**.

---

## 10. Composición del IRE

El IRE (0-100) integra las **tres ramas** en un único puntaje de prioridad (RN-006, RN-007,
RF-007). Usa **exactamente los 8 factores** de DOMAIN_MODEL §8.3. No depende solo del score de
IA (§8.3: "no depende únicamente del modelo IA").

### 10.1 Fórmula

Cada factor se normaliza a [0,100] y se pondera:

```
IRE = Σ (wᵢ · factorᵢ)   con Σ wᵢ = 1
```

| Factor (§8.3, canónico) | Fuente de cálculo | Peso aceptado (wᵢ) |
|---|---|---|
| Score del modelo IA | §9.3 normalizado | 0.30 |
| Historial de consumos | `deviation_from_baseline` (F9) | 0.15 |
| Persistencia de anomalías | `prior_anomaly_count` (F15) + rachas | 0.15 |
| Variación porcentual | `pct_change_prev_period` / `pct_change_yoy` (F5/F6) | 0.15 |
| Impacto económico estimado | IEE normalizado (§11) | 0.10 |
| Resultado de inspecciones anteriores | `feedback_modelo` (0 en v1, cold-start) | 0.08 |
| Consumo promedio | `avg_consumption` (F1) como factor de exposición | 0.04 |
| Categoría tarifaria | ajuste por criticidad de categoría | 0.03 |

Suma = 1.00. **En v1** el factor "inspecciones anteriores" es 0 (no hay feedback aún): su peso se
**redistribuye proporcionalmente** entre los otros siete, y así se documenta explícitamente en
lugar de simular un dato inexistente.

> **DEC-014 — Pesos del IRE (aceptada según recomendación, 2026-07-14).** La tabla anterior, con
> el score de IA como factor dominante (0.30) por ser el que captura lo multivariado, y
> persistencia + variación + historial juntos (0.45) para reflejar el comportamiento propio del
> suministro. Alternativas descartadas: pesos iguales (0.125 c/u); esquema calibrado contra los
> primeros lotes reales. Impacto: forma del ranking de inspecciones (RN-009).

### 10.2 Banding (nivel)

El nivel del IRE lo calcula la **columna generada `ire.nivel`** del esquema, que ya fija las
bandas. El diseño debe respetarlas exactamente:

| `ire.valor` | `ire.nivel` (generado por la base) |
|---|---|
| 0 – 20 | Muy Bajo |
| 21 – 40 | Bajo |
| 41 – 60 | Medio |
| 61 – 80 | Alto |
| 81 – 100 | Crítico |

> **DEC-015 — Mapeo IRE → clasificación (aceptada según recomendación, 2026-07-14).**
> `resultados_ia.clasificacion` tiene **4** valores (`Normal`, `Atención`, `Alto Riesgo`,
> `Crítico`), mientras que `ire.nivel` (columna generada, §10.2) tiene **5** bandas. El puente
> definitivo entre ambas es: `0-20 → Normal`, `21-40 → Atención`, `41-70 → Alto Riesgo`,
> `71-100 → Crítico`. La alternativa (colapsar Muy Bajo+Bajo → Normal y Medio → Atención) queda
> descartada. Impacto: semántica del semáforo que ve el analista (US-012).

### 10.3 Contrato de explicabilidad (RN-012, RF-013)

RN-012 exige que toda decisión automática sea explicable; RF-013 obliga a "mostrar la explicación
del IRE"; US-013: "conocer por qué un suministro fue clasificado como anómalo". El motor persiste,
por `ResultadoIA`, un **desglose por factor**:

```
{ "factor": "variacion_porcentual",
  "contribution": 22.5,          // puntos aportados al IRE
  "reason": "Consumo 68% menor que el mismo período del año anterior" }
```

Estructura aceptada (DEC-016): una lista de estos objetos, uno por factor con contribución no
nula, más una razón legible por cada `Anomalía` (`anomalias.descripcion`).

> **Honestidad sobre la rama de ML (tensión de ADR-005).** La contribución del factor "Score del
> modelo IA" es una **aproximación**: la explicación de Isolation Forest se deriva por atribución
> de longitud de camino por feature, no por una regla causal. ADR-005 lo documenta como tensión
> real: RN-012 queda satisfecha de forma **probabilística** para el componente de ML, no con una
> justificación garantizada. Las ramas de reglas y estadística sí son causalmente explicables; el
> desglose debe distinguir visualmente unas de otra.

> **DEC-016 — Dónde persistir el desglose (aceptada según recomendación, 2026-07-14).** El
> esquema **no tiene** una columna estructurada (jsonb) para el desglose del IRE. Se usa
> `resultados_ia.observaciones` (text) con el JSON serializado en v1, y se evaluará agregar una
> columna jsonb dedicada cuando el frontend lo consuma (US-013). Alternativa descartada:
> reconstruir el desglose on-demand desde `feature_vectors`. Impacto: ver §16 (brecha de esquema).

---

## 11. Impacto Económico Estimado (IEE)

El IEE (§8.4, RF-008) estima la pérdida energética recuperable, insumo del ranking (RN-009) y de
la gerencia. **DEC-017 (resuelta por alternativa, 2026-07-14):** en v1 el IEE se expresa en
**kWh** (energía no facturada), sin proxy tarifario ni parámetro de configuración de precio; la
monetización queda diferida a v2, cuando exista una fuente real de precios por categoría
tarifaria. Se persiste en `impacto_economico`; §11.2 documenta la convención v1 de mapeo a las
columnas existentes de esa tabla.

### 11.1 Enfoque

```
IEE_kwh = max(0, kwh_esperado − kwh_facturado)
```

- `kwh_esperado`: consumo legítimo estimado desde la línea base propia del suministro
  (`moving_avg_12m`, F8) o la mediana de su cohorte cuando no hay historia.
- Se recorta a ≥ 0 (`max(0, …)`): solo la **sub-facturación** representa pérdida recuperable
  (RD-027 — el valor nunca es negativo, condición que se preserva en energía igual que en
  moneda).
- Debe ser **reproducible** (RD-028) y conservar histórico (RD-029): por eso se guarda por
  `ResultadoIA` con su `fecha_calculo`.
- **Sin factor de precio.** A diferencia de un diseño monetizado, `IEE_kwh` no multiplica por
  `precio_kwh`: ese dato no existe en el esquema (`categorias_tarifarias` solo tiene `nombre` y
  `descripcion`) y DEC-017 descartó introducir un proxy de configuración para v1.

### 11.2 Convención de persistencia en `impacto_economico` (v1, energía)

El esquema (`docker/postgres/init/01_schema.sql`, no modificado) define `impacto_economico` con
semántica monetaria: `monto_estimado numeric(14,2) NOT NULL` con `CHECK (monto_estimado >= 0)`
(RD-027) y `moneda varchar(3) NOT NULL DEFAULT 'ARS'`. Sin una tabla de tarifas, el motor no puede
llenar esas columnas con un valor monetario real. Convención v1, sin alterar el esquema:

| Columna | Contenido en v1 | Motivo |
|---|---|---|
| `monto_estimado` | El valor de `IEE_kwh`, en **kWh** (no en ARS) | `numeric(14,2)` admite la magnitud y precisión de un valor de energía sin cambios de tipo; el CHECK `>= 0` (RD-027) es igualmente válido para energía que para moneda |
| `moneda` | `'kWh'` (en lugar del default `'ARS'`) | `varchar(3)` acepta el literal `'kWh'` sin cambios de esquema; funciona como discriminador explícito de que la fila está expresada en energía, no en moneda |
| `fecha_calculo` | Sin cambios (RD-028, RD-029) | La reproducibilidad y el histórico no dependen de la unidad |

Este es un **convenio de v1, documentado explícitamente para no confundir energía con dinero**:
todo consumidor de `impacto_economico` (API, frontend, reportes) debe leer `moneda` antes de
interpretar `monto_estimado`, y tratar `'kWh'` como señal de que el valor **no** es un monto en
ARS. Cuando exista una fuente real de precios (v2), la migración consiste en recalcular
`monto_estimado = IEE_kwh × precio_kwh` y volver a escribir `moneda = 'ARS'`; no requiere cambio
de esquema, porque las columnas ya existen con los tipos correctos.

### 11.3 Decisión de proxy tarifario (DEC-017)

> **DEC-017 — Proxy tarifario (resuelta por alternativa, 2026-07-14).** El esquema **no
> almacena precios**: `categorias_tarifarias` tiene solo `nombre` y `descripcion`, sin
> `precio_kwh`. La recomendación original (un parámetro de configuración `precio_kwh_proxy`)
> **no** fue la vía elegida: Rodrigo Zanin optó por la alternativa —expresar el IEE en **kWh**
> (energía no facturada) y postergar la monetización a v2—, para no introducir un precio
> inventado en configuración cuando no existe una fuente real de tarifas. Impacto: §16 (brecha de
> esquema, reencuadrada); los reportes económicos (US-017, US-022) quedan en términos de energía
> hasta v2.

---

## 12. Presupuesto de performance

RNF-001 (< 10 min = 600 s por lote) frente a RNF-007 (hasta 500.000 suministros). Cuenta gruesa
propuesta (a validar con pruebas de carga, ADR-002):

| Etapa | Costo estimado | Estrategia |
|---|---|---|
| [1]-[2] Validación + duplicados | ~60 s | Consultas SQL agregadas sobre particiones (`consumos` particionada por `fecha_inicio`) |
| [3] Features | ~180 s | Vectorización numpy/pandas; chunks de ~10.000 suministros por tarea |
| [4] Estadística | ~60 s | Percentiles/IQR por cohorte en SQL o vectorizados |
| [5] Reglas | ~30 s | Evaluación vectorizada sobre el feature frame |
| [6] IF scoring | ~90 s | `joblib` `n_jobs = nº de cores`; O(n log n) |
| [7]-[8] IRE + IEE + persistencia | ~120 s | Bulk insert por lote |
| **Total** | **~540 s** | Margen ~60 s bajo el techo de 600 s |

**Mitigación del GIL (ADR-002, ADR-006):** el scoring y la generación de features se reparten en
procesos worker vía `multiprocessing`/`joblib`; el async de FastAPI no ayuda en trabajo CPU-bound.
**Chunking:** el lote se particiona por rangos de suministros (o por categoría, alineado con
DEC-010) para paralelizar. **DEC-019** no se abre por separado: la cantidad de workers y el tamaño
de chunk se calibran con datos reales, que hoy no existen (staging pendiente, #8).

---

## 13. Métricas y monitoreo

Los KPIs de IA de BUSINESS_ANALYSIS §17 (precisión, recall, falsos positivos, falsos negativos,
anomalías confirmadas) **requieren etiquetas** de inspecciones finalizadas, que en v1 no existen
(cold-start). Mapeo realista:

| KPI §17 | Medición | Disponible en |
|---|---|---|
| Precisión / Recall / F1 | `metricas_modelo` calculadas contra `feedback_modelo` | **v2** (necesita etiquetas) |
| Falsos positivos / negativos | Comparación predicción vs. `resultado_real` (RD-042) | **v2** |
| Anomalías confirmadas | `datasets_etiquetados` de etiqueta `Anomalía/Fraude Confirmado` | **v2** |
| Anomalías por lote (tasa) | Conteo de `anomalias` / suministros del lote | **v1 (proxy)** |
| Deriva de distribución del score | Comparar histograma de `probabilidad` entre lotes | **v1 (proxy)** |
| Tiempo de análisis por lote | Instrumentar la corrida (KPI operativo §17) | **v1** |

En v1 el monitoreo se apoya en **proxies no supervisados** (tasa de anomalías, deriva del score,
tiempo de análisis); las métricas supervisadas se activan cuando el Feedback Loop (§9.5, v2)
empiece a producir etiquetas.

### 13.1 Línea base v1 (datos sintéticos, seed 42, 2026-07-15)

A falta de etiquetas reales (cold-start, ver arriba), esta es la referencia comprometida que el
motor debe superar una vez existan las ramas de reglas/IA completas: un detector ingenuo por
porcentaje de cambio, corrido sobre el dataset sintético determinístico (`seed 42`, ver
`backend/src/energia/tools/synthetic/`), detecta 5 de 6 anomalías plantadas con 43 falsos positivos
cada 100 suministros sanos. La tabla siguiente muestra, para cada anomalía plantada, cómo la
formalizan los indicadores de Etapa 4 (§7) ya implementados, en el mes de inicio de la anomalía:

| suministro | tipo plantado | \|z\|≥3 | IQR | percentile extremo |
|---|---|---|---|---|
| SYN-S42-SUM-00005 | spike_leve | no (2.83) | sí | sí (1.0) |
| SYN-S42-SUM-00014 | sudden_drop | no (-2.02) | sí | no |
| SYN-S42-SUM-00024 | spike | sí (4.04) | sí | sí (1.0) |
| SYN-S42-SUM-00032 | sudden_drop_leve | no (-1.05) | sí | no |
| SYN-S42-SUM-00049 | zero_consumption_streak | no (-1.42) | sí | no |
| SYN-S42-SUM-00073 | gradual_decline | no | no | no (visible recién 6+ meses después del inicio) |

Falsos positivos sobre 94 sanos (último lote): 1 zscore, 5 IQR, 6 percentile.

**Nota:** esta tabla es anterior a los FIX 3 (IQR leave-one-out, §7) y FIX 4 (`trend_slope` contra
meses calendario, §6.4) de la revisión del 2026-07-15 — ambos pueden desplazar estos números (en
particular la columna IQR, y cualquier detección que dependa de `trend_slope`). Se remedirá con la
Etapa 6 (Isolation Forest, §9), cuando exista el pipeline completo contra el que comparar.

---

## 14. Mapeo etapa → tabla (contrato de persistencia)

| Etapa | Escribe en | Columnas clave |
|---|---|---|
| [3] Features | `feature_vectors` | `features` (jsonb), `version`, `suministro_id`, `lote_id` |
| [6] Isolation Forest | `predicciones` | `modelo_ia_id`, `score`, `clasificacion`, `lote_id` |
| [6]/[7] | `resultados_ia` | `score_anomalia`, `probabilidad`, `clasificacion`, `prediccion_id`, `observaciones` |
| [5]/[7] | `anomalias` | `tipo`, `severidad`, `descripcion`, `resultado_ia_id` |
| [7] | `ire` | `valor` (`nivel` es columna generada) |
| [8] | `impacto_economico` | `monto_estimado`, `moneda` |
| [9] | `modelos_ia` | `algoritmo`, `version`, `estado` |

Orden de escritura: `feature_vectors` (con `resultado_ia_id` nulo) → `predicciones` →
`resultados_ia` → backfill de `feature_vectors.resultado_ia_id` → `anomalias` / `ire` /
`impacto_economico`. La FK nullable `feature_vectors.resultado_ia_id` habilita este orden.

---

## 15. Decisiones validadas (2026-07-14)

Las 18 decisiones fueron validadas por Rodrigo Zanin el 2026-07-14. Salvo DEC-017, todas se
resolvieron según su recomendación por defecto.

| ID | Tema | Recomendación | Alternativas | Impacto | Resolución |
|---|---|---|---|---|---|
| DEC-001 | Granularidad del disparo (RN-005 per-consumo vs RN-013 per-lote) | Ratificar **per-lote** (disparo al completarse la carga del lote), leyendo RN-005 como "al finalizar el lote" | Reescribir RN-005 para alinearlo | Contrato del disparador; consistencia RN-005/RN-013 | Aceptada según recomendación (2026-07-14) |
| DEC-002 | Reproceso de un lote `Procesado` | **No reprocesar** (RD-010 terminal, RD-023 único) | Permitir reproceso con nueva versión de modelo, sobrescribiendo o agregando filas | Idempotencia; trazabilidad histórica | Aceptada según recomendación (2026-07-14) |
| DEC-003 | Outcome de validación de integridad fallida | **Excluir + anotar** el suministro; no abortar salvo umbral | Anotar sin excluir; fallar el lote entero | Cobertura del análisis vs. calidad del scoring | Aceptada según recomendación (2026-07-14) |
| DEC-004 | Umbral de completitud del lote | **≥ 95 %** de suministros válidos para analizar | 90 %, 99 %, sin umbral | Robustez de cohorte (RD-009) vs. tolerancia operativa | Aceptada según recomendación (2026-07-14) |
| DEC-005 | Definición y outcome de "duplicado" | Anotar y excluir el período conflictivo de las ventanas | Excluir más reciente / más antiguo; promediar | Calidad de features; conteo de consumo | Aceptada según recomendación (2026-07-14) |
| DEC-006 | Conjunto de features v1 | Las 17 features de §6.1 con `RobustScaler` | Subconjunto reducido; features derivadas adicionales | Poder de detección; costo de cómputo | Aceptada según recomendación (2026-07-14) |
| DEC-007 | Ventanas y mínimos de historia | 6/12 meses; mínimo 3 períodos para desvíos | 3/6 meses; mínimos distintos | Cold-start; sensibilidad | Aceptada según recomendación (2026-07-14) |
| DEC-008 | Peer group de cohorte | Categoría × localidad, fallback a categoría | Solo categoría; categoría × barrio | Comparabilidad (RD-009); tamaño de cohorte | Aceptada según recomendación (2026-07-14) |
| DEC-009 | Umbrales de las reglas v1 | Los de la tabla §8 (−60 %, +200 %, racha 3, p1/p99) | Umbrales calibrados con datos reales | Falsos positivos de la rama de reglas | Aceptada según recomendación (2026-07-14). Recalibrada a v1.1 el 2026-07-15 con evidencia sintética (§8.2): R2 pasa a precipicio de un período; R4/R5 pasan a p5/p95 en conjunción con `peer_ratio` 0.4/2.5 |
| DEC-010 | Modelo global vs. por categoría | **Por categoría** (≥ 1.000 suministros), fallback global | Único global con categoría como feature | Precisión de cohorte vs. modelos a mantener | Aceptada según recomendación (2026-07-14) |
| DEC-011 | `contamination` de Isolation Forest | **0.03** | `'auto'`; 0.01–0.05 | Tasa base de anomalías | Aceptada según recomendación (2026-07-14) |
| DEC-012 | `n_estimators` / `max_samples` | **200 / 256** | 100 / `'auto'`; valores mayores | Precisión vs. tiempo (RNF-001) | Aceptada según recomendación (2026-07-14) |
| DEC-013 | Normalización del score a 0-100 | Min-max invertida por lote, calibrada a `contamination` | Percentiles del score histórico | Estabilidad del IRE entre lotes | Aceptada según recomendación (2026-07-14) |
| DEC-014 | Pesos del IRE (8 factores §8.3) | Tabla §10.1 (IA 0.30 dominante) | Pesos iguales; calibración empírica | Forma del ranking (RN-009) | Aceptada según recomendación (2026-07-14) |
| DEC-015 | Mapeo IRE (5 bandas) → clasificación (4 valores) | 0-20 Normal / 21-40 Atención / 41-70 Alto Riesgo / 71-100 Crítico | Colapsar Muy Bajo+Bajo → Normal | Semáforo del analista (US-012) | Aceptada según recomendación (2026-07-14) |
| DEC-016 | Persistencia del desglose de explicabilidad | JSON en `resultados_ia.observaciones` (v1); columna jsonb dedicada (futuro) | Reconstruir on-demand desde `feature_vectors` | RN-012/RF-013; brecha de esquema (§16) | Aceptada según recomendación (2026-07-14) |
| DEC-017 | Proxy tarifario para el IEE | Parámetro `precio_kwh_proxy` (config), o IEE en kWh | Postergar monetización | Reportes económicos (US-017/US-022); brecha de esquema | Resuelta por alternativa: IEE en kWh, sin monetización en v1 (2026-07-14) |
| DEC-018 | Política de reentrenamiento v1 | (Re)ajuste no supervisado por lote; Aprendizaje Continuo supervisado en v2 | Modelo estático; reentrenamiento programado | Deriva del modelo; dependencia del Feedback Loop | Aceptada según recomendación (2026-07-14) |

> **Nota sobre DEC-001.** La aceptación incluyó, además de ratificar la granularidad per-lote,
> la reformulación directa de RN-005 en `docs/01-business/BUSINESS_ANALYSIS.md` §15 —en lugar de
> dejar la lectura implícita "RN-005 como 'al finalizar el lote'"— para eliminar de forma
> permanente la contradicción de fuente documentada en §2.1.

---

## 16. Brechas de esquema detectadas

El diseño **no modifica** `docker/postgres/init/01_schema.sql`. Se señalan las columnas que el
motor necesitaría y que hoy no existen, para decisión posterior (no se agregan aquí):

1. **Precio de tarifa ausente** (bloquea únicamente la monetización del IEE, no v1 en sí).
   `categorias_tarifarias` no tiene `precio_kwh` ni existe tabla de tarifas. Resuelto para v1 por
   **DEC-017** (2026-07-14): el IEE se expresa en kWh (§11), sin depender de un precio. La brecha
   queda registrada como trabajo de v2 (incorporar una fuente real de tarifas y migrar
   `impacto_economico` a monto monetario, §11.2), no como bloqueo de v1.
2. **Sin columna estructurada para el desglose del IRE** (RN-012). No hay jsonb dedicado al
   breakdown factor→contribución→razón; se usa `resultados_ia.observaciones` (text). Ver **DEC-016**.
3. **Sin columna para hiperparámetros del modelo** (RD-049: "debe registrarse la configuración
   utilizada"). Ni `modelos_ia` ni `reentrenamientos_modelo` tienen una columna de configuración
   (contamination, n_estimators, ventana de entrenamiento). En v1 puede ir embebida en
   `modelos_ia.version` o `nombre`, pero es una brecha real frente a RD-049.

Ninguna es bloqueante para v1 con las mitigaciones aceptadas (§15); las tres se registran como
deuda de esquema para v2.

---

## 17. Referencias

- **ADR-005** (motor híbrido; Isolation Forest; tensión de explicabilidad; cold-start),
  **ADR-007** (batch por lote; disparo en lote `Pendiente`/`Error` completo; RN-013; RD-009 cohorte), **ADR-006**
  (worker aislado; monolito modular), **ADR-002** (GIL; scoring CPU-bound; multiprocessing/joblib)
  — `docs/03-architecture/adr/`.
- **DOMAIN_MODEL** §8 (ResultadoIA §8.1, Anomalía §8.2, IRE §8.3 — factores canónicos, IEE §8.4,
  Feature Vector §8.5, Modelo IA §8.6, Predicción §8.7), §10 (Aprendizaje Continuo, RD-042 a
  RD-049), §12 (Domain Services), §13 (Domain Events), §14 (invariantes globales) —
  `docs/03-architecture/DOMAIN_MODEL.md`.
- **BUSINESS_ANALYSIS** §5 (TO-BE, tres ramas), §15 (RN-005, RN-006, RN-007, RN-008, RN-009,
  RN-012, RN-013), §17 (KPIs de IA) — `docs/01-business/BUSINESS_ANALYSIS.md`.
- **SRS** RF-004, RF-005, RF-006, RF-007, RF-008, RF-013, RNF-001, RNF-007;
  **USER_STORIES** US-006 a US-013 — `docs/02-requirements/`.
- **Esquema** (contrato de persistencia): tablas `feature_vectors`, `resultados_ia`,
  `predicciones`, `anomalias`, `ire`, `impacto_economico`, `modelos_ia`, `metricas_modelo` —
  `docker/postgres/init/01_schema.sql`.
- **Convenciones de contexto** (bounded context `motor`, estados de `Lote`,
  reintento `Error → Procesando`) — `backend/src/energia/contexts/README.md`.
