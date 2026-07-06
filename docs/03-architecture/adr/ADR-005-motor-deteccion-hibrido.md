# ADR-005: Motor de detección — enfoque híbrido (reglas + estadística + Isolation Forest no supervisado)

| Campo | Valor |
|---|---|
| Estado | Propuesto |
| Fecha | 2026-07-06 |
| Autor | Rodrigo Zanin |
| Decisores | Pendiente de validación |

## Contexto

`docs/01-business/BUSINESS_ANALYSIS.md` §15 (RN-012) exige: "Todas las decisiones automáticas deberán ser explicables". RN-008 aclara que "la detección de una anomalía no constituye evidencia de fraude" — el motor produce señales para revisión humana, no veredictos. RN-005 exige que "cada nuevo consumo procesado deberá ser analizado automáticamente por el Motor de Inteligencia Energética".

No existe, al día de hoy, un dataset de fraude etiquetado. `docs/03-architecture/DOMAIN_MODEL.md` §10 (Aprendizaje Continuo) modela explícitamente el mecanismo por el cual esas etiquetas se generarán en el futuro: el Feedback del Modelo (§10.1) y el Dataset Etiquetado (§10.2) se nutren de inspecciones ya finalizadas (RD-045: "solo pueden incorporarse datos provenientes de inspecciones finalizadas (implementa RN-011)"). Es decir, las etiquetas son un producto de v1 en operación, no un insumo disponible antes de arrancar — un problema de cold-start explícito. El roadmap de `PRODUCT_VISION.md` §11 confirma esta secuencia: "Modelos supervisados" y "Feedback automático" están planificados para la versión 2.0, no la 1.0.

`PRODUCT_VISION.md` §15 ya declara Scikit-Learn + Isolation Forest como tecnología de IA. `BUSINESS_ANALYSIS.md` §5 (situación TO-BE) dibuja el motor como una bifurcación de tres ramas — Reglas de Negocio, Estadística, IA (Isolation Forest) — que convergen en el cálculo del IRE, bajo el nombre **Motor de Inteligencia Energética**.

## Decisión

Adoptar un **motor de detección híbrido**: reglas de negocio explícitas + análisis estadístico (desvío respecto de una línea base histórica del propio suministro) + Isolation Forest como componente de Machine Learning no supervisado, combinados en el cálculo del IRE.

Adicionalmente, este ADR **canoniza el nombre del componente de negocio como "Motor de Inteligencia Energética"** — el nombre ya usado en `PRODUCT_VISION.md` (§13, §14) y en `BUSINESS_ANALYSIS.md` (§5, RN-005) — por sobre "Motor de Inteligencia Artificial", usado de forma inconsistente en `DOMAIN_MODEL.md` (título del bounded context 4.4, título de la sección 8) y reflejado en el naming de `DATABASE_DESIGN.md` (`modelo_ia`, `resultado_ia`). El motor real combina reglas + estadística + ML: llamarlo "Inteligencia Artificial" describe solo el tercio del componente que corresponde a Isolation Forest, y subestima el resto.

## Alternativas consideradas

### Solo reglas de negocio (umbrales/reglas explícitas, sin estadística ni ML)

Gana de forma decisiva en explicabilidad pura: cada decisión traza a una regla nombrada y auditable, sin aproximación estadística de por medio, y satisface RN-012 de manera trivial. No tiene problema de cold-start: funciona desde el primer lote. Es una alternativa seria, no un espantapájaros.

Se descarta porque reglas limitadas son exactamente el diagnóstico del problema actual: `PRODUCT_VISION.md` §3 identifica "reglas de negocio limitadas" como una de las causas raíz de que "casos importantes puedan pasar desapercibidos" y de que "se inspeccionen clientes que no presentan anomalías relevantes". Un motor de solo reglas no puede capturar patrones multivariados o derivas graduales que no encajan en un umbral predefinido — precisamente la brecha de "detección temprana" que EnergIA existe para cerrar.

### Modelo supervisado como enfoque principal

Bien entrenado, superaría en precisión/recall a un enfoque no supervisado. Sería la opción ganadora una vez que exista un dataset etiquetado de calidad.

Es inviable como enfoque de v1 porque no existe dataset etiquetado inicial — una restricción dura del proyecto, no una preferencia. Peor: existe una dependencia circular de arranque (`DOMAIN_MODEL.md` §10): se necesitan inspecciones para obtener etiquetas, pero se necesita un detector para saber qué suministros inspeccionar. El roadmap (`PRODUCT_VISION.md` §11, v2.0) confirma que esto es una decisión de secuencia — el modelo supervisado llega después, apoyado en el feedback que genere v1 — no un rechazo del enfoque.

### LOF / One-Class SVM / Autoencoder como algoritmo principal (en vez de Isolation Forest)

`DOMAIN_MODEL.md` §8.6 ya lista estos algoritmos como "Algoritmos soportados" junto a Isolation Forest, lo que confirma que el modelo fue diseñado para poder intercambiarse. Cada uno **ganaría** en un escenario específico: Local Outlier Factor puede superar a Isolation Forest detectando anomalías de densidad local (grupos de consumidores similares-pero-anómalos dentro de un mismo barrio); One-Class SVM puede ser más robusto en espacios de features de baja dimensión bien delimitados; un Autoencoder puede capturar patrones no lineales complejos si hay suficiente volumen de entrenamiento.

Isolation Forest se elige como algoritmo principal de v1 por dos razones concretas: costo computacional (complejidad ~O(n log n) y fácilmente paralelizable, frente al costo cuadrático aproximado de los métodos basados en vecinos/distancia como LOF u One-Class SVM a los volúmenes de RNF-007, con el presupuesto de RNF-001 de menos de 10 minutos por lote) y explicabilidad más directa vía atribución por longitud de camino por feature, más alineada con RN-012 que una frontera de decisión basada en densidad o distancia.

## Consecuencias

### Positivas

- El enfoque híbrido cubre los casos conocidos y explícitos con reglas (barato de explicar, satisface RN-012 directamente) mientras estadística e Isolation Forest capturan lo que las reglas no anticipan — el mismo diferenciador que `PRODUCT_VISION.md` §10 declara: IA + reglas de negocio + estadística en una sola plataforma.
- Reduce el riesgo de sobre-confiar en un score de caja negra: los factores del IRE están explícitamente enumerados (`DOMAIN_MODEL.md` §8.3: score del modelo, historial de consumos, persistencia de anomalías, categoría tarifaria, impacto económico, resultado de inspecciones anteriores), lo que da sustancia a RN-012 más allá del score de Isolation Forest solo.
- Habilita la evolución futura a modelos supervisados (roadmap v2.0) sin descartar el trabajo de v1: el score no supervisado de Isolation Forest puede convertirse en una feature más del modelo supervisado posterior.

### Negativas / costos aceptados

- La explicación de Isolation Forest es una aproximación estadística (atribución por feature sobre longitudes de camino), no una regla causal. RF-013/RN-012 quedan satisfechas para el componente de ML de forma probabilística, no con una justificación garantizada — es una tensión real que estos documentos no terminan de resolver, y hay que decirlo así en vez de presentar la explicabilidad como un problema cerrado.
- Un motor de tres subsistemas (reglas, estadística, ML) es intrínsecamente más difícil de calibrar y validar que cualquiera de los tres por separado: sus salidas deben reconciliarse en un único IRE, lo que multiplica la superficie de ajuste. Para un desarrollador único, mantener tres superficies de tuning en paralelo es un costo operativo continuo, no un costo de diseño único.
- **Deuda documental pendiente:** el nombre "Motor de Inteligencia Artificial" sigue apareciendo en `DOMAIN_MODEL.md` (bounded context 4.4, título de la sección 8, y menciones recurrentes de "Motor IA" a lo largo del documento) y en el naming de tablas/columnas de `DATABASE_DESIGN.md` (`modelo_ia`, `resultado_ia`). Este ADR no reescribe esos documentos; deja registrado el barrido pendiente en `PROJECT_MASTER_SPEC.md`. Nótese que entidades como "Modelo IA" o "ResultadoIA" nombran específicamente al sub-componente de Machine Learning (una pieza real de Inteligencia Artificial dentro del motor) y pueden conservar ese nombre a nivel de entidad; lo que este ADR canoniza es el nombre del **motor/bounded context como un todo**.

### Riesgos y mitigaciones

- **Riesgo:** falsos positivos persistentes erosionan la confianza de los inspectores en el ranking. **Mitigación:** medir precisión/recall/falsos positivos/falsos negativos (KPIs de IA, `BUSINESS_ANALYSIS.md` §17) a través del Feedback Loop (RD-042 a RD-046) e iterar umbrales de forma continua.
- **Riesgo:** el barrido de renombrado quede indefinidamente pendiente y la inconsistencia de nombres se perpetúe. **Mitigación:** registrarlo como deuda documental explícita y condicionarlo a la aceptación de este ADR (ver `PROJECT_MASTER_SPEC.md`, sección de deuda documental).
