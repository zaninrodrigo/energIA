# ADR-001: Estilo arquitectónico del backend — Clean Architecture + DDD táctico

| Campo | Valor |
|---|---|
| Estado | Propuesto |
| Fecha | 2026-07-06 |
| Autor | Rodrigo Zanin |
| Decisores | Pendiente de validación |

## Contexto

`docs/03-architecture/DOMAIN_MODEL.md` ya modela el dominio de EnergIA en 7 bounded contexts (Gestión de Clientes, Gestión de Suministros, Gestión de Consumos, Motor de Inteligencia Artificial, Gestión del Riesgo, Gestión de Inspecciones, Dashboard Ejecutivo — §4), cada uno con su propio Aggregate Root (Suministro, ResultadoIA, ModeloIA, Orden de Inspección) y un catálogo de invariantes RD-xxx. El mismo documento declara como principios de diseño "Domain Driven Design (DDD), Clean Architecture, SOLID, alta cohesión, bajo acoplamiento, persistencia ignorante" (§2) y afirma que "las reglas del negocio nunca dependerán de frameworks, motores de bases de datos ni librerías externas".

`docs/02-requirements/SOFTWARE_REQUIREMENTS_SPECIFICATION.md` §8 (Restricciones) ya fija "Arquitectura Clean Architecture" como restricción del proyecto, y RNF-005 exige una cobertura de tests mínima del 90% en backend — una cifra que solo es alcanzable de forma sostenible si la lógica de dominio puede testearse aislada de infraestructura (FastAPI, PostgreSQL, Oracle).

El equipo es un único desarrollador (Rodrigo Zanin). Sin un segundo par de ojos para el code review, la disciplina arquitectónica tiene que imponerla la estructura del código, no la revisión humana.

Este ADR no introduce una decisión nueva: documenta el razonamiento detrás de una decisión que los documentos de negocio y requisitos ya dan por hecha.

## Decisión

Adoptar **Clean Architecture** (capas Domain / Application / Infrastructure / Presentation / Shared, per SAD §7) combinada con **patrones tácticos de DDD** (entidades, value objects, aggregates, domain services, repositorios — SAD §8) como estilo arquitectónico del backend. Los 7 bounded contexts de `DOMAIN_MODEL.md` se traducen en límites de módulo dentro del backend.

## Alternativas consideradas

### Layered MVC clásico (Controller → Service → Repository, sin DDD táctico)

Menor ceremonia: menos archivos por feature, curva de aprendizaje más baja, velocidad inicial más alta. **Esta alternativa gana** en proyectos CRUD pequeños de un solo contexto, o en un MVP descartable sin plan de mantenimiento a largo plazo.

Para EnergIA, con 7 contextos y un catálogo de invariantes de dominio (RD-001 a RD-049) que deben cumplirse siempre, un modelo anémico con lógica repartida entre controllers y services tiende a duplicar validaciones y a perder la garantía de que una regla se cumple en todos los puntos de entrada. Sin un segundo revisor, ese riesgo se vuelve más probable con el tiempo, no menos.

### Hexagonal puro (Ports & Adapters) sin DDD táctico

Ofrece el mismo aislamiento de infraestructura y testabilidad que Clean Architecture, pero sin modelar aggregates ni entidades con invariantes propias — la lógica de negocio vive en casos de uso, no en el modelo.

Dado que `DOMAIN_MODEL.md` ya define Aggregate Roots explícitos por contexto (Suministro, ResultadoIA, ModeloIA, Orden de Inspección — §7, §8, §9, §10) y un conjunto extenso de invariantes, aplicar DDD táctico es una continuación natural de ese trabajo de modelado, no una capa adicional de complejidad artificial.

## Consecuencias

### Positivas

- El dominio queda testeable sin mocks de infraestructura, lo que hace alcanzable el 90% de cobertura backend exigido por RNF-005.
- La disciplina que impone la estructura (capas, dependencias apuntando hacia adentro) compensa la ausencia de revisión por pares.
- Los 7 bounded contexts ya modelados se mapean directamente a los límites de módulo del backend, sin necesidad de un rediseño.

### Negativas / costos aceptados

- Ceremonia significativamente mayor por feature: interfaces de repositorio, casos de uso, DTOs y mappers donde un CRUD simple bastaría con un controller y un ORM. Para un desarrollador único esto es un costo real de velocidad, no solo teórico.
- El bounded context "Dashboard Ejecutivo" (`DOMAIN_MODEL.md` §4.7) se declara explícitamente "encargado exclusivamente de consultas y visualización de indicadores. No contiene reglas de negocio". Aplicarle el mismo aparato de entidades/aggregates/casos de uso que a un contexto con invariantes reales (Suministro, ResultadoIA) es sobre-ingeniería honesta que hay que reconocer, no ocultar.

### Riesgos y mitigaciones

- **Riesgo:** el desarrollador único se ahoga en boilerplate repetitivo por cada nuevo caso de uso. **Mitigación:** plantillas/generadores de código para el andamiaje repetitivo (casos de uso, DTOs, mappers).
- **Riesgo:** sobre-ingeniería en contextos de solo lectura (Dashboard Ejecutivo). **Mitigación:** para ese contexto específico, usar servicios de consulta livianos (estilo CQRS de solo lectura) que no pasen por el ciclo completo de entidades/aggregates, documentando la excepción explícitamente en vez de aplicar la plantilla por inercia.
