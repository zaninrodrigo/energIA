# ADR-004: Almacén analítico-operativo — PostgreSQL propio, Oracle como fuente de solo lectura vía ETL incremental

| Campo | Valor |
|---|---|
| Estado | Aceptado |
| Fecha | 2026-07-06 |
| Autor | Rodrigo Zanin |
| Decisores | Rodrigo Zanin (2026-07-06) |

## Contexto

Oracle es el sistema corporativo de facturación de la distribuidora: es de solo lectura para EnergIA y no puede ser reemplazado ni modificado por este proyecto. `docs/01-business/PRODUCT_VISION.md` §1 lo dice explícitamente: "Su propósito no es reemplazar los sistemas corporativos existentes, sino complementarlos", y §13 (Arquitectura Conceptual) ya dibuja el flujo `Oracle → ETL Incremental → PostgreSQL → Motor de Inteligencia Energética`.

`docs/02-requirements/SOFTWARE_REQUIREMENTS_SPECIFICATION.md` RF-001 exige importar automáticamente los consumos históricos desde Oracle, RF-002 exige importar nuevos lotes de facturación, y §8 fija PostgreSQL como base de datos principal. §7 (Suposiciones) asume que "Oracle contiene históricos confiables" y que "existen al menos dos años de consumos". RNF-007 exige soportar más de 500.000 suministros.

`docs/03-architecture/DOMAIN_MODEL.md` §7.4 modela el "Lote de Facturación" con un ciclo de estados propio (Pendiente, Procesando, Procesado, Error) y aclara que "no representa la facturación comercial... representa únicamente la unidad de procesamiento del sistema EnergIA" — es decir, EnergIA necesita su propio modelo de datos, independiente del esquema transaccional de Oracle.

## Decisión

EnergIA mantiene su propio almacén **PostgreSQL**, poblado mediante un proceso de **ETL incremental** que lee desde Oracle. Oracle es fuente de solo lectura: EnergIA nunca escribe de vuelta hacia el sistema corporativo.

## Alternativas consideradas

### Consultar Oracle directamente (sin almacén propio, vistas/federación en tiempo real)

Gana en frescura de datos (sin lag de replicación) y evita la duplicación de almacenamiento y los problemas de consistencia entre dos copias del mismo dato. En un escenario de bajo volumen, o si Oracle estuviera dedicado exclusivamente a EnergIA, esta sería la opción más simple.

Se descarta porque Oracle es un sistema **corporativo compartido**, dimensionado para carga transaccional (OLTP) de facturación, no para las consultas analíticas repetidas que exige el feature engineering (RF-004) y el entrenamiento/scoring de Isolation Forest sobre historiales de cientos de miles de suministros. Ejecutar ese tipo de carga directamente contra Oracle arriesga degradar el sistema de facturación real, y contradice el principio de "Integración" del producto (PRODUCT_VISION §8: "el producto debe convivir con la infraestructura tecnológica existente", no acoplarse a ella ni competir por sus recursos).

### Data warehouse dedicado (separado del PostgreSQL operativo de la API)

Gana si la carga analítica (feature engineering sobre millones de registros históricos, RNF-007) crece más allá de lo que el particionado de PostgreSQL puede sostener con buen rendimiento, o si coexistieran cargas de BI ad-hoc pesadas junto con el tráfico OLTP de la API.

Se descarta para el alcance actual porque introduce una segunda plataforma de datos (warehouse + operativa) a mantener por un desarrollador único: dos esquemas, un pipeline de sincronización adicional, una tecnología más para operar. El volumen declarado en RNF-007 (>500.000 suministros) es exigente pero razonablemente abordable con particionado e indexado en PostgreSQL para el alcance de v1; introducir un warehouse dedicado hoy sería resolver un problema de escala que todavía no existe.

## Consecuencias

### Positivas

- Desacopla a EnergIA de la carga y la volatilidad de esquema de Oracle: el sistema corporativo sigue haciendo su trabajo sin que el análisis de EnergIA compita por sus recursos.
- PostgreSQL ya es una restricción declarada (SRS §8) y soporta particionado de tablas de consumo para los volúmenes de RNF-007.
- El ETL incremental es el mecanismo natural para materializar el "Lote de Facturación" (RD-010/RD-012) como unidad completa antes de disparar el Motor de Inteligencia Energética, alineado con RN-013.

### Negativas / costos aceptados

- El dato en PostgreSQL es, por definición, una réplica: existe un lag de propagación entre Oracle y EnergIA. Los dashboards y el IRE pueden reflejar momentáneamente información desactualizada respecto de Oracle, y una corrección posterior en Oracle (por ejemplo, un error de facturación corregido después de la importación) exige que el ETL la detecte y reconcilie — un riesgo de consistencia que los documentos actuales no resuelven todavía.
- EnergIA asume su propia estrategia de backup/retención, porque duplica un subconjunto de los datos de Oracle. Esto es un costo de almacenamiento creciente, agravado por RD-007 (`DOMAIN_MODEL.md`): "el historial nunca puede eliminarse".

### Riesgos y mitigaciones

- **Riesgo:** un lote parcialmente importado dispara el Motor de Inteligencia Energética sobre datos incompletos, violando RN-013 ("todo lote deberá procesarse completamente antes de ejecutar la IA"). **Mitigación:** el ciclo de estados del Lote de Facturación (Pendiente / Procesando / Procesado / Error, `DOMAIN_MODEL.md` §7.4) actúa como gate explícito de completitud antes de disparar el motor, reforzado con auditoría de cada corrida del ETL (RF-020).
