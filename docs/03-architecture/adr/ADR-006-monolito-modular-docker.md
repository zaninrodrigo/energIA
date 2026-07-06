# ADR-006: Topología de despliegue — monolito modular contenedorizado (Docker), no microservicios

| Campo | Valor |
|---|---|
| Estado | Aceptado |
| Fecha | 2026-07-06 |
| Autor | Rodrigo Zanin |
| Decisores | Rodrigo Zanin (2026-07-06) |

## Contexto

El equipo del proyecto es un desarrollador único (Rodrigo Zanin). `docs/02-requirements/SOFTWARE_REQUIREMENTS_SPECIFICATION.md` §8 ya fija Docker como restricción obligatoria. `docs/01-business/PRODUCT_VISION.md` §8 declara "Modularidad: cada componente podrá evolucionar de forma independiente" como principio de producto — una intención de modularidad, no necesariamente de despliegue distribuido. RNF-002 exige una disponibilidad mínima del 99%.

`docs/03-architecture/DOMAIN_MODEL.md` §4 ya modela 7 bounded contexts con Aggregate Roots propios por contexto (Suministro, ResultadoIA, ModeloIA, Orden de Inspección), lo que ofrece una costura natural para una eventual extracción a servicios independientes si el proyecto llegara a necesitarlo.

## Decisión

Desplegar EnergIA como un **monolito modular contenedorizado con Docker**, con los límites de módulo trazados a lo largo de los 7 bounded contexts (paquetes/módulos internos, no servicios ni despliegues separados).

## Alternativas consideradas

### Microservicios por bounded context (7 servicios independientes)

Gana en escalado independiente, despliegue independiente y aislamiento de fallas por servicio — la opción correcta si EnergIA llegara a tener varios equipos y contextos con perfiles de carga muy distintos entre sí (por ejemplo, el Motor de Inteligencia Energética con cómputo intensivo por lote frente a Dashboard Ejecutivo con lecturas livianas).

Se descarta para el estado actual del proyecto porque, con un desarrollador único, los microservicios multiplican la carga operativa de forma prácticamente lineal con la cantidad de servicios: 7 despliegues, 7 conjuntos de logs/health checks/pipelines de CI, y llamadas de red entre servicios (con sus propios modos de falla — reintentos, timeouts, tracing distribuido) reemplazando lo que de otro modo serían llamadas de función in-process. Nada de eso está justificado sin un equipo que lo sostenga, y el objetivo de disponibilidad del 99% (RNF-002) es más difícil de sostener con más saltos de red que pueden fallar de forma independiente, no más fácil.

### Serverless (funciones por caso de uso)

Gana en simplicidad operativa para cargas de trabajo esporádicas o dirigidas por eventos (sin costo de infraestructura ociosa, auto-escalado), y encajaría razonablemente con el disparador de RN-005 ("cada nuevo consumo procesado").

Se descarta porque RNF-001 (análisis de lote en menos de 10 minutos, sobre potencialmente cientos de miles de suministros por RNF-007) es un trabajo de procesamiento por lotes, largo, con estado y CPU-intensivo — un mal encaje para los límites típicos de tiempo de ejecución/memoria y la latencia de arranque en frío de las plataformas FaaS. Adoptarlo obligaría a re-arquitecturar el pipeline de ML en torno a orquestación por fragmentos (step functions o similar) solo para encajar en la plataforma, una complejidad no motivada por ningún requisito real.

## Consecuencias

### Positivas

- Un único artefacto desplegable, un único pipeline de CI/CD, un único conjunto de logs — lo que un desarrollador único puede operar de forma realista.
- Los bounded contexts, como límites de módulo internos, preservan un camino de extracción futura a microservicios por contexto si el tamaño del equipo o el perfil de carga llegaran a justificarlo — esta decisión es una cobertura explícita, no un callejón sin salida.
- Docker ya era una restricción declarada (SRS §8); este ADR no agrega infraestructura nueva, solo decide cómo se organizan los módulos dentro de ese contenedor.

### Negativas / costos aceptados

- Un monolito comparte un único dominio de falla y una única dimensión de escalado: si la corrida del Motor de Inteligencia Energética es intensiva en CPU durante el procesamiento de un lote (RNF-001), puede degradar la capacidad de respuesta de la API para usuarios de dashboard concurrentes, salvo que se separe explícitamente en procesos worker.
- Los límites entre módulos no los impone el runtime, a diferencia de una frontera de red real entre microservicios — para un desarrollador único bajo presión de tiempo, la tentación de saltarse un límite de módulo "por esta vez" es real, y solo la autodisciplina (o herramientas de lint) lo detecta.

### Riesgos y mitigaciones

- **Riesgo:** una corrida de lote intensiva en CPU satura el proceso que también atiende requests de la API. **Mitigación:** ejecutar el análisis del Motor de Inteligencia Energética como un proceso worker separado (mismo stack de Docker Compose, mismo lenguaje/código base, pero aislado a nivel de proceso del sistema operativo) para que el cómputo pesado no comparta el mismo pool de hilos que el manejo de requests HTTP.
- **Riesgo:** erosión gradual de los límites de módulo con el tiempo. **Mitigación:** reglas de lint/import-boundaries (architecture fitness functions) verificadas en CI.
