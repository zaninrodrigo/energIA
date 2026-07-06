# ADR-007: Modelo de procesamiento — batch orientado a lotes de facturación, no streaming

| Campo | Valor |
|---|---|
| Estado | Propuesto |
| Fecha | 2026-07-06 |
| Autor | Rodrigo Zanin |
| Decisores | Pendiente de validación |

## Contexto

`docs/01-business/BUSINESS_ANALYSIS.md` §15 (RN-013) exige: "Todo lote deberá procesarse completamente antes de ejecutar la IA". RN-004 refuerza esto: "los consumos son generados únicamente a partir de procesos de facturación por lotes". RN-005 dispara el análisis "al finalizar el procesamiento de un lote", no por consumo individual. RNF-001 fija un presupuesto de menos de 10 minutos para ese análisis **por lote**.

El proceso AS-IS (`BUSINESS_ANALYSIS.md` §4) ya tiene a "Facturación por Lote" como el evento que dispara todo lo demás, y el proceso TO-BE (§5) mantiene esa misma estructura: `Facturación por Lote → Importación Automática → Motor de Inteligencia Energética → ...`. El proceso de facturación de origen (Oracle) es en sí mismo un proceso por lotes periódico, no un flujo continuo de telemetría de medidores en tiempo real. `DOMAIN_MODEL.md` §7.4 define el "Lote de Facturación" como "un conjunto de consumos importados en una misma ejecución" — la unidad de trabajo es, por diseño, un conjunto discreto, no un evento individual.

## Decisión

Procesar el análisis de consumos en **lotes discretos**, una ejecución del Motor de Inteligencia Energética por cada lote de facturación completo — no como un pipeline de streaming/eventos continuo.

## Alternativas consideradas

### Streaming/event-driven continuo (cada lectura o consumo dispara análisis al llegar)

Gana de forma decisiva si las lecturas llegaran en tiempo real — por ejemplo, con medidores inteligentes emitiendo telemetría continua. Sería la opción correcta el día en que exista esa infraestructura de medición.

Se descarta porque el proceso de origen es, hoy, inherentemente por lotes: no hay telemetría continua de medidores que alimentar. Además, RN-013 no es solo una limitación técnica del proceso de facturación — es una regla de negocio explícita que exige esperar el lote completo antes de correr la IA, porque la comparación de un suministro contra su cohorte (`DOMAIN_MODEL.md` RD-009: "la IA solo compara suministros de categorías equivalentes") necesita que esa cohorte esté completa. Analizar un suministro apenas llega su registro individual, antes de que el resto de su cohorte haya sido importado, produciría comparaciones mal informadas o directamente erróneas.

### Micro-batches programados (ejecutar análisis cada N minutos, con los datos disponibles hasta ese momento, sin esperar el cierre del lote)

Gana en latencia promedio entre importación y detección frente a esperar el cierre de un lote potencially grande, y añade resiliencia frente a que un lote enorme bloquee todo el análisis durante horas.

Se descarta porque entra en conflicto directo con RN-013: correr el motor sobre un lote parcial/incompleto viola la regla de negocio explícita, y vuelve a socavar la lógica de comparación por cohorte descrita arriba. También complicaría RD-010/RD-011 (`DOMAIN_MODEL.md`: "un lote no puede ejecutarse dos veces" / "un lote debe finalizar antes de iniciar otro"), ya que un disparador programado desacoplado del estado real del lote necesitaría su propia lógica de coordinación para no procesar dos veces o actuar sobre un lote todavía en estado "Procesando".

## Consecuencias

### Positivas

- Refleja la naturaleza real del proceso de origen (facturación por lotes) sin introducir complejidad artificial para simular tiempo real sobre una fuente que no lo es.
- Satisface RN-013 por construcción: el disparador del motor es, literalmente, la transición del lote a estado "Procesado" (`DOMAIN_MODEL.md` §7.4).
- Habilita comparaciones estadísticas de cohorte completa (líneas base por categoría tarifaria, RD-009) que un modelo por evento individual no podría ofrecer sin, de hecho, terminar acumulando (bufferizando) el mismo lote de todas formas.

### Negativas / costos aceptados

- La latencia de detección tiene un piso marcado por el propio ciclo de facturación: si una distribuidora corre sus lotes con periodicidad mensual, un consumo anómalo puede pasar sin detectarse hasta el cierre de ese ciclo completo — una limitación real frente al objetivo declarado de "detección temprana" (`PRODUCT_VISION.md` §1).
- El presupuesto de RNF-001 (< 10 minutos) es un techo por lote, no un SLA de tiempo real: si el propio lote se demora en cerrarse del lado de Oracle, la detección de EnergIA se demora exactamente lo mismo, y esta arquitectura no tiene forma de compensarlo porque el evento disparador está fuera del control de EnergIA.

### Riesgos y mitigaciones

- **Riesgo:** un lote muy grande, cercano al umbral de RNF-007 (>500.000 suministros), no entra en el presupuesto de 10 minutos de RNF-001 a medida que crece el volumen. **Mitigación:** apoyarse en la naturaleza paralelizable del scoring de Isolation Forest (ADR-005) y en el aislamiento del cómputo pesado en un proceso worker (ADR-006), de forma que el throughput del lote escale aproximadamente en línea con el cómputo agregado.
- **Riesgo:** un lote marcado como "Procesado" a pesar de una importación parcialmente fallida rompe el supuesto de cohorte completa. **Mitigación:** el estado "Error" explícito del Lote de Facturación (`DOMAIN_MODEL.md` §7.4) y la verificación de completitud (RD-012: "todo consumo pertenece a un lote") como condición previa a la transición a "Procesado".
