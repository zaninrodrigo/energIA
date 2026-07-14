# Especificación del Motor de Inteligencia Energética

| Versión | Fecha | Estado | Autor |
|---|---|---|---|
| 1.0.0 | 2026-07-14 | Propuesto | Rodrigo Zanin |

> **Estado del documento.** Este diseño nace en estado **Propuesto**. Cada parámetro que
> exige una decisión de negocio o de datos se marca en el cuerpo como **DECISIÓN (DEC-xxx)**
> con una recomendación por defecto y sus alternativas, y se consolida en la §15 para su
> aprobación por Rodrigo Zanin. Ningún valor numérico de este documento (pesos, umbrales,
> hiperparámetros) es definitivo hasta esa validación.

## Resumen ejecutivo

El **Motor de Inteligencia Energética** analiza cada lote de facturación completo y produce,
por suministro, un `ResultadoIA` con su `IRE` (0-100), su `IEE` y sus `Anomalías`, para
**priorizar inspecciones**. No decide fraude: una anomalía es una señal para revisión humana
(RN-008, RD-025).

Es un **motor híbrido de tres ramas** (ADR-005): reglas de negocio explícitas, análisis
estadístico e Isolation Forest no supervisado, que convergen en el IRE. Corre **por lote**
(ADR-007), disparado por la transición del lote a `Procesado`, en un **proceso worker**
aislado (ADR-006, ADR-002), con un presupuesto de **menos de 10 minutos** (RNF-001) para
volúmenes de hasta **500.000 suministros** (RNF-007).

| | |
|---|---|
| **Entrada** | `consumos`, `lecturas`, `suministros`, `categorias_tarifarias` de un lote `Procesado` |
| **Salida** | `resultados_ia`, `predicciones`, `anomalias`, `ire`, `impacto_economico`, `feature_vectors` |
| **Contrato de esquema** | `docker/postgres/init/01_schema.sql` (no se modifica; brechas en §16) |
| **Decisiones pendientes** | 18 (DEC-001 a DEC-018), consolidadas en §15 |

---

## 1. Propósito y alcance

### 1.1 Qué es

El motor transforma consumos históricos en información accionable (DOMAIN_MODEL §8): detecta
patrones anómalos, estima riesgo (IRE) e impacto económico (IEE), y alimenta el ranking de
inspecciones. Es el núcleo analítico del sistema y el bounded context `intelligence_engine`
(`contexts/README.md`), todavía no implementado.

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
| ADR-007 | Ejecución batch por lote; disparo en transición a `Procesado`; RN-013 lote completo |
| ADR-006 | Cómputo pesado aislado en proceso worker; monolito modular |
| ADR-002 | Python/Scikit-Learn; GIL ⇒ scoring CPU-bound ⇒ multiprocessing/joblib |

---

## 2. Disparo y orquestación

### 2.1 Disparador

El motor se ejecuta **una vez por lote**, cuando el `Lote` transiciona a `Procesado`
(ADR-007; DOMAIN_MODEL §7.4). RN-013 exige lote completo antes de correr la IA, porque la
comparación de cohorte (RD-009: "la IA solo compara suministros de categorías equivalentes")
necesita la cohorte completa. RF-005 lo formaliza: "ejecutar el Motor al finalizar el
procesamiento de un lote".

> **Tensión de fuente (contradicción documentada).** RN-005 dice "cada nuevo **consumo**
> procesado deberá ser analizado automáticamente", granularidad por consumo; RN-013 + ADR-007
> imponen granularidad **por lote**. ADR-005 reconcilia leyendo RN-005 como "al finalizar el
> lote". Ver **DEC-001**.

### 2.2 Máquina de estados del lote

```
Pendiente ──▶ Procesando ──▶ Procesado   (terminal, RD-010)
                  │
                  └────────▶ Error ──▶ Procesando   (reintento, decisión 2026-07-13)
```

El motor solo actúa sobre lotes `Procesado`. La transición `Error → Procesando` (reintento
aprobado, PROJECT_MASTER_SPEC #12; `contexts/README.md`) es del pipeline de importación,
**previo** al motor; `Procesado` sigue siendo terminal (RD-010: "un lote no puede ejecutarse
dos veces"). `Lote.estado` nunca se acepta desde el payload de importación (`contexts/README.md`),
así que el único camino a `Procesado` es el pipeline real.

### 2.3 Idempotencia (RD-010)

`resultados_ia` tiene `UNIQUE (suministro_id, lote_id)` (RD-023): existe a lo sumo un
`ResultadoIA` por suministro y lote. Si se pide procesar un lote ya `Procesado`, el motor
**no reprocesa** por defecto: el estado terminal y la restricción única lo garantizan. Ver
**DEC-002** para la política de reproceso deliberado (nueva versión de modelo).

### 2.4 Ejecución en worker aislado

El scoring es CPU-bound y el GIL limita el paralelismo intra-proceso (ADR-002). El motor corre
en un **proceso worker separado** (ADR-006), fuera del pool de hilos de la API, para no
degradar la latencia de los dashboards concurrentes. El paralelismo real se obtiene con
multiprocessing/joblib sobre el scoring de Isolation Forest (§12).

### 2.5 Semántica de fallo

| Situación | Qué persiste | Estado resultante |
|---|---|---|
| Fallo antes de escribir resultados | Nada (transacción no confirmada) | Lote queda auditables; reintento reprocesa el lote entero |
| Fallo a mitad de escritura | La escritura se hace **por lote transaccional**, no por suministro suelto | Rollback total; sin resultados parciales |
| Suministro con datos inválidos | Se excluye del scoring y se anota (§4) | El resto del lote se procesa |

El motor persiste el conjunto del lote de forma atómica: no deja un lote medio analizado. Ver
**DEC-003** (outcome de validación) para el tratamiento fino por suministro.

---

## 3. Pipeline: visión general

```
Lote Procesado
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

> **DEC-003 — Outcome de un chequeo fallido.** Recomendación: **excluir** el suministro del
> scoring y **anotar** el motivo (en `resultados_ia.observaciones` del lote o en un log de
> calidad), sin abortar el lote; abortar (marcar `Error`) solo si la fracción de registros
> inválidos supera un umbral. Alternativas: (a) anotar sin excluir y dejar que el scoring
> absorba el ruido; (b) fallar el lote ante cualquier inválido. Impacto: cobertura del análisis
> vs. calidad del scoring.

> **DEC-004 — Umbral de completitud del lote.** Recomendación: permitir el análisis si al menos
> **95 %** de los suministros del lote pasan la validación; por debajo, marcar `Error` y exigir
> recarga. Alternativas: 90 %, 99 %, o sin umbral. Impacto: robustez de la cohorte (RD-009,
> ADR-007 "cohorte completa") vs. tolerancia operativa.

---

## 5. Etapa 2 — Detección de duplicados (US-007)

Los duplicados **exactos** ya los previene la base: índices únicos parciales sobre
`(suministro_id, fecha_inicio, fecha_fin)`, `(suministro_id, fecha_lectura)`, etc. Por eso, en
esta etapa "duplicado" significa lo que la base **no** previene:

| Tipo | Definición | Fuente |
|---|---|---|
| Solapamiento de períodos | Dos consumos del mismo suministro con períodos que se cruzan sin ser idénticos | RD-017 |
| Consumo repetido entre lotes | El mismo período reimportado en un lote distinto (mismo `suministro_id` + rango, distinto `lote_id`) | — |
| Near-duplicate de lecturas | Lecturas del mismo suministro con fechas muy próximas y valores idénticos | §7.5 |

**Outcome propuesto:** las duplicidades no borran datos (el motor no modifica operativos, §1.2);
se **anotan** y el período conflictivo se marca para no contarse dos veces en las ventanas de
features (§6). Ver **DEC-005** para la política exacta (excluir el más reciente, el más antiguo,
o promediar).

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
tiene 12 meses de historia. Política propuesta:

- Features de ventana larga con historia insuficiente ⇒ `null` explícito en el jsonb (no 0, que
  el modelo interpretaría como señal real).
- El scoring (§9) usa imputación por la mediana de la cohorte para los nulos, y registra una
  feature booleana `is_cold_start` para que el IRE no penalice la falta de historia como anomalía.

Ver **DEC-006** (conjunto exacto de features) y **DEC-007** (tamaños de ventana y mínimos de
historia). Mapeo a esquema: todo el vector va en `feature_vectors.features` (jsonb); `version`
identifica la versión del contrato de features (p. ej. `"v1"`), respetando
`UNIQUE (suministro_id, lote_id, version)`.

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
superan sus umbrales, y alimentan factores del IRE (§10). **DEC-008**: definir el peer group
(categoría × localidad vs. solo categoría) según el tamaño típico de cohorte de los datos reales,
que hoy se desconocen (staging pendiente, PROJECT_MASTER_SPEC #8).

---

## 8. Etapa 5 — Reglas de negocio

La **rama de reglas** (ADR-005) codifica los casos conocidos y explícitos: cada disparo traza a
una regla nombrada, satisfaciendo RN-012 de forma directa. Genera `Anomalías` de tipos concretos
del catálogo cerrado de §8.2.

| Regla | Condición (propuesta v1) | Tipo de Anomalía (§8.2) | Severidad |
|---|---|---|---|
| R1 | `zero_consumption_streak ≥ 3` con suministro activo | `Persistencia Anómala` | Alta |
| R2 | `pct_change_prev_period ≤ −60 %` sostenida ≥ 2 períodos | `Caída Brusca` | Alta |
| R3 | `pct_change_prev_period ≥ +200 %` en un período | `Incremento Brusco` | Media |
| R4 | `kwh` bajo el percentil 1 de su cohorte | `Consumo Muy Bajo` | Media |
| R5 | `kwh` sobre el percentil 99 de su cohorte | `Consumo Muy Alto` | Media |
| R6 | `deviation_from_baseline`, valor absoluto ≥ 3 | `Desvío Estadístico` | Media |

Los umbrales (−60 %, +200 %, 3 períodos, percentiles 1/99) son **candidatos**, no verdades:
dependen del comportamiento real de los datos. Ver **DEC-009**. `Patrón Irregular` queda como
tipo reservado para la rama ML (§9), no para reglas.

---

## 9. Etapa 6 — Isolation Forest (US-011, RF-006)

La **rama de ML** (ADR-005). Isolation Forest es no supervisado: no necesita etiquetas (que no
existen), y su costo ~O(n log n) paralelizable encaja en RNF-001/RNF-007 mejor que LOF u
One-Class SVM (ADR-005). Persiste en `predicciones` y `resultados_ia`.

### 9.1 Estrategia de entrenamiento

> **DEC-010 — Modelo global vs. por categoría.** Recomendación: **un modelo por categoría
> tarifaria** cuando la categoría tenga volumen suficiente (p. ej. ≥ 1.000 suministros), con
> fallback a un **modelo global** para categorías chicas. Fundamento: RD-009 exige comparar
> dentro de la cohorte; un modelo por categoría lo respeta por construcción. Alternativa: modelo
> único global con la categoría como feature (F17). Impacto: precisión de cohorte vs. cantidad de
> modelos a versionar y mantener (`modelos_ia`).

### 9.2 Hiperparámetros

| Parámetro | Default propuesto | Decisión |
|---|---|---|
| `contamination` | `0.03` (≈ 3 % anómalos esperados) | **DEC-011** |
| `n_estimators` | `200` | **DEC-012** |
| `max_samples` | `256` (default de la técnica) | **DEC-012** |
| `random_state` | fijo (reproducibilidad, RD-028) | — |
| Escalado de features | `RobustScaler` (resistente a outliers, que son justo la señal) | incluido en **DEC-006** |

### 9.3 Normalización del score a 0-100

`decision_function` de Isolation Forest devuelve un score donde valores más negativos son más
anómalos (puede ser negativo, por eso `resultados_ia.score_anomalia` es `numeric(8,4)` sin CHECK
≥ 0). Se persiste:

- **crudo** en `resultados_ia.score_anomalia` (trazabilidad),
- **normalizado a [0,1]** en `resultados_ia.probabilidad` (CHECK 0-1) y `predicciones.score`,
- la contribución del ML al IRE se calcula desde el normalizado (§10).

> **DEC-013 — Método de normalización.** Recomendación: escala **min-max invertida por lote**
> sobre los scores del lote (0 = menos anómalo, 100 = más anómalo), calibrada para que la
> `contamination` esperada caiga sobre el umbral de "Atención". Alternativa: mapeo por percentiles
> del score histórico. Impacto: estabilidad del IRE entre lotes de distinto tamaño.

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

| Factor (§8.3, canónico) | Fuente de cálculo | Peso propuesto (wᵢ) |
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

> **DEC-014 — Pesos del IRE.** Recomendación: la tabla anterior, con el score de IA como factor
> dominante (0.30) por ser el que captura lo multivariado, y persistencia + variación + historial
> juntos (0.45) para reflejar el comportamiento propio del suministro. Alternativas: pesos iguales
> (0.125 c/u); esquema calibrado contra los primeros lotes reales. Impacto: forma del ranking de
> inspecciones (RN-009).

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

> **DEC-015 — Mapeo IRE → clasificación.** `resultados_ia.clasificacion` tiene **4** valores
> (`Normal`, `Atención`, `Alto Riesgo`, `Crítico`), pero `ire.nivel` tiene **5** bandas. Hay que
> reconciliarlos. Recomendación: `0-20 → Normal`, `21-40 → Atención`, `41-70 → Alto Riesgo`,
> `71-100 → Crítico`. Alternativa: colapsar Muy Bajo+Bajo → Normal y Medio → Atención. Impacto:
> semántica del semáforo que ve el analista (US-012).

### 10.3 Contrato de explicabilidad (RN-012, RF-013)

RN-012 exige que toda decisión automática sea explicable; RF-013 obliga a "mostrar la explicación
del IRE"; US-013: "conocer por qué un suministro fue clasificado como anómalo". El motor persiste,
por `ResultadoIA`, un **desglose por factor**:

```
{ "factor": "variacion_porcentual",
  "contribution": 22.5,          // puntos aportados al IRE
  "reason": "Consumo 68% menor que el mismo período del año anterior" }
```

Estructura propuesta: una lista de estos objetos, uno por factor con contribución no nula, más una
razón legible por cada `Anomalía` (`anomalias.descripcion`).

> **Honestidad sobre la rama de ML (tensión de ADR-005).** La contribución del factor "Score del
> modelo IA" es una **aproximación**: la explicación de Isolation Forest se deriva por atribución
> de longitud de camino por feature, no por una regla causal. ADR-005 lo documenta como tensión
> real: RN-012 queda satisfecha de forma **probabilística** para el componente de ML, no con una
> justificación garantizada. Las ramas de reglas y estadística sí son causalmente explicables; el
> desglose debe distinguir visualmente unas de otra.

> **DEC-016 — Dónde persistir el desglose.** El esquema **no tiene** una columna estructurada
> (jsonb) para el desglose del IRE. Recomendación: usar `resultados_ia.observaciones` (text) con el
> JSON serializado en v1, y evaluar agregar una columna jsonb dedicada cuando el frontend lo
> consuma (US-013). Alternativa: reconstruir el desglose on-demand desde `feature_vectors`. Impacto:
> ver §16 (brecha de esquema).

---

## 11. Impacto Económico Estimado (IEE)

El IEE (§8.4, RF-008) estima la pérdida potencial recuperable, insumo del ranking (RN-009) y de la
gerencia. Se persiste en `impacto_economico` (`monto_estimado` ≥ 0, RD-027; `moneda` default `ARS`).

### 11.1 Enfoque

```
IEE = max(0, kwh_esperado − kwh_facturado) × precio_kwh
```

- `kwh_esperado`: consumo legítimo estimado desde la línea base propia del suministro
  (`moving_avg_12m`, F8) o la mediana de su cohorte cuando no hay historia.
- Se recorta a ≥ 0 (`max(0, …)`): solo la **sub-facturación** representa pérdida recuperable
  (RD-027).
- Debe ser **reproducible** (RD-028) y conservar histórico (RD-029): por eso se guarda por
  `ResultadoIA` con su `fecha_calculo`.

### 11.2 Brecha de datos: no hay precio de tarifa

> **DEC-017 — Proxy tarifario.** El esquema **no almacena precios**: `categorias_tarifarias` tiene
> solo `nombre` y `descripcion`, sin `precio_kwh`. Sin precio no se puede monetizar el IEE.
> Recomendación v1: un **parámetro de configuración** `precio_kwh_proxy` (un valor ARS/kWh, o un
> mapa por categoría) inyectado al motor, hasta que exista una tabla de tarifas. Alternativa:
> expresar el IEE en **kWh** (energía no facturada) y postergar la monetización. Impacto: §16
> (brecha de esquema); comparabilidad de los reportes económicos (US-017, US-022).

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

## 15. Decisiones pendientes de validación

| ID | Tema | Recomendación | Alternativas | Impacto |
|---|---|---|---|---|
| DEC-001 | Granularidad del disparo (RN-005 per-consumo vs RN-013 per-lote) | Ratificar **per-lote** (transición a `Procesado`), leyendo RN-005 como "al finalizar el lote" | Reescribir RN-005 para alinearlo | Contrato del disparador; consistencia RN-005/RN-013 |
| DEC-002 | Reproceso de un lote `Procesado` | **No reprocesar** (RD-010 terminal, RD-023 único) | Permitir reproceso con nueva versión de modelo, sobrescribiendo o agregando filas | Idempotencia; trazabilidad histórica |
| DEC-003 | Outcome de validación de integridad fallida | **Excluir + anotar** el suministro; no abortar salvo umbral | Anotar sin excluir; fallar el lote entero | Cobertura del análisis vs. calidad del scoring |
| DEC-004 | Umbral de completitud del lote | **≥ 95 %** de suministros válidos para analizar | 90 %, 99 %, sin umbral | Robustez de cohorte (RD-009) vs. tolerancia operativa |
| DEC-005 | Definición y outcome de "duplicado" | Anotar y excluir el período conflictivo de las ventanas | Excluir más reciente / más antiguo; promediar | Calidad de features; conteo de consumo |
| DEC-006 | Conjunto de features v1 | Las 17 features de §6.1 con `RobustScaler` | Subconjunto reducido; features derivadas adicionales | Poder de detección; costo de cómputo |
| DEC-007 | Ventanas y mínimos de historia | 6/12 meses; mínimo 3 períodos para desvíos | 3/6 meses; mínimos distintos | Cold-start; sensibilidad |
| DEC-008 | Peer group de cohorte | Categoría × localidad, fallback a categoría | Solo categoría; categoría × barrio | Comparabilidad (RD-009); tamaño de cohorte |
| DEC-009 | Umbrales de las reglas v1 | Los de la tabla §8 (−60 %, +200 %, racha 3, p1/p99) | Umbrales calibrados con datos reales | Falsos positivos de la rama de reglas |
| DEC-010 | Modelo global vs. por categoría | **Por categoría** (≥ 1.000 suministros), fallback global | Único global con categoría como feature | Precisión de cohorte vs. modelos a mantener |
| DEC-011 | `contamination` de Isolation Forest | **0.03** | `'auto'`; 0.01–0.05 | Tasa base de anomalías |
| DEC-012 | `n_estimators` / `max_samples` | **200 / 256** | 100 / `'auto'`; valores mayores | Precisión vs. tiempo (RNF-001) |
| DEC-013 | Normalización del score a 0-100 | Min-max invertida por lote, calibrada a `contamination` | Percentiles del score histórico | Estabilidad del IRE entre lotes |
| DEC-014 | Pesos del IRE (8 factores §8.3) | Tabla §10.1 (IA 0.30 dominante) | Pesos iguales; calibración empírica | Forma del ranking (RN-009) |
| DEC-015 | Mapeo IRE (5 bandas) → clasificación (4 valores) | 0-20 Normal / 21-40 Atención / 41-70 Alto Riesgo / 71-100 Crítico | Colapsar Muy Bajo+Bajo → Normal | Semáforo del analista (US-012) |
| DEC-016 | Persistencia del desglose de explicabilidad | JSON en `resultados_ia.observaciones` (v1); columna jsonb dedicada (futuro) | Reconstruir on-demand desde `feature_vectors` | RN-012/RF-013; brecha de esquema (§16) |
| DEC-017 | Proxy tarifario para el IEE | Parámetro `precio_kwh_proxy` (config), o IEE en kWh | Postergar monetización | Reportes económicos (US-017/US-022); brecha de esquema |
| DEC-018 | Política de reentrenamiento v1 | (Re)ajuste no supervisado por lote; Aprendizaje Continuo supervisado en v2 | Modelo estático; reentrenamiento programado | Deriva del modelo; dependencia del Feedback Loop |

---

## 16. Brechas de esquema detectadas

El diseño **no modifica** `docker/postgres/init/01_schema.sql`. Se señalan las columnas que el
motor necesitaría y que hoy no existen, para decisión posterior (no se agregan aquí):

1. **Precio de tarifa ausente** (bloquea el IEE monetizado). `categorias_tarifarias` no tiene
   `precio_kwh` ni existe tabla de tarifas. Mitigado por **DEC-017** (proxy en config o IEE en kWh).
2. **Sin columna estructurada para el desglose del IRE** (RN-012). No hay jsonb dedicado al
   breakdown factor→contribución→razón; se usa `resultados_ia.observaciones` (text). Ver **DEC-016**.
3. **Sin columna para hiperparámetros del modelo** (RD-049: "debe registrarse la configuración
   utilizada"). Ni `modelos_ia` ni `reentrenamientos_modelo` tienen una columna de configuración
   (contamination, n_estimators, ventana de entrenamiento). En v1 puede ir embebida en
   `modelos_ia.version` o `nombre`, pero es una brecha real frente a RD-049.

Ninguna es bloqueante para v1 con las mitigaciones propuestas; las tres se registran como deuda de
esquema.

---

## 17. Referencias

- **ADR-005** (motor híbrido; Isolation Forest; tensión de explicabilidad; cold-start),
  **ADR-007** (batch por lote; disparo en `Procesado`; RN-013; RD-009 cohorte), **ADR-006**
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
- **Convenciones de contexto** (bounded context `intelligence_engine`, estados de `Lote`,
  reintento `Error → Procesando`) — `backend/src/energia/contexts/README.md`.
