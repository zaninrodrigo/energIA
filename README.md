# EnergIA

Plataforma de inteligencia operacional que detecta consumos eléctricos anómalos y prioriza inspecciones técnicas mediante reglas de negocio, estadística e Inteligencia Artificial.

## Problema y solución

Las distribuidoras eléctricas administran cientos de miles de suministros cuyos consumos se actualizan en cada lote de facturación. Analizar manualmente esos volúmenes para detectar anomalías es lento, depende de la experiencia de cada operador y suele derivar en inspecciones sobre suministros de bajo impacto mientras casos relevantes pasan desapercibidos.

EnergIA incorpora un Motor de Inteligencia Energética que analiza automáticamente cada consumo procesado, combinando reglas de negocio del dominio eléctrico, análisis estadístico de históricos e Isolation Forest (Scikit-Learn). Con ese análisis calcula, para cada suministro, un Índice de Riesgo Energético (IRE, escala 0-100) y un Impacto Económico Estimado (IEE), y genera un ranking priorizado de inspecciones que se integra con el sistema de RRHH para crear órdenes de trabajo. El objetivo es asistir la toma de decisiones de analistas e inspectores, no reemplazar su criterio técnico.

## Estado del proyecto

Fase de documentación y diseño — sin código aún. El repositorio contiene la especificación funcional, de negocio, de arquitectura y de dominio sobre la cual se construirá la implementación.

## Mapa de documentación

| Carpeta | Documentos | Estado |
|---|---|---|
| `docs/01-business` | PRODUCT_VISION.md, BUSINESS_ANALYSIS.md | Completo / Borrador |
| `docs/02-requirements` | SOFTWARE_REQUIREMENTS_SPECIFICATION.md, USER_STORIES.md, ACCEPTANCE_CRITERIA.md | Completo |
| `docs/03-architecture` | DOMAIN_MODEL.md, DATABASE_DESIGN.md, SOFTWARE_ARCHITECTURE_DOCUMENT.md, API_SPEC.md | Completo / Borrador / Esqueleto / Pendiente |
| `docs/04-ai` | AI_ENGINE_SPEC.md, DATA_SCIENCE_NOTEBOOK.md | Pendiente |
| `docs/05-devops` | SECURITY_SPEC.md, TESTING_SPEC.md, DEPLOYMENT_SPEC.md, ROADMAP.md | Pendiente |

Para el detalle de estado de cada documento y la deuda documental conocida, ver [`PROJECT_MASTER_SPEC.md`](./PROJECT_MASTER_SPEC.md).

## Stack planificado

- **Backend:** Python, FastAPI
- **Frontend:** React, TypeScript
- **Base de datos:** PostgreSQL
- **Inteligencia Artificial:** Scikit-Learn, Isolation Forest
- **Contenedores:** Docker
- **Testing:** Pytest, Playwright
- **Origen de datos:** Oracle (facturación por lotes)
- **Arquitectura:** Clean Architecture + Domain-Driven Design

## Estructura del repositorio

```
energIA/
├── backend/           # API FastAPI (Clean Architecture + DDD) — vacío por ahora
├── frontend/          # Aplicación React/TypeScript — vacío por ahora
├── docker/            # Definiciones de contenedores y orquestación local
├── datasets/          # Muestras de datos (los datasets crudos no se versionan)
├── diagrams/          # Diagramas de arquitectura y de dominio
├── scripts/           # Scripts de soporte (ETL, utilidades)
├── docs/
│   ├── 01-business/       # Visión de producto y análisis de negocio
│   ├── 02-requirements/   # SRS, historias de usuario, criterios de aceptación
│   ├── 03-architecture/   # Modelo de dominio, base de datos, arquitectura, API
│   ├── 04-ai/              # Motor de IA y análisis de datos
│   └── 05-devops/         # Seguridad, testing, despliegue, roadmap
├── PROJECT_MASTER_SPEC.md # Índice maestro de toda la documentación
├── CLAUDE.md              # Instrucciones para asistentes de IA que trabajen en el repositorio
└── README.md
```
