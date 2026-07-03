# CLAUDE.md

Instrucciones para asistentes de IA (Claude Code y equivalentes) que trabajen en este repositorio.

## Resumen del proyecto

EnergIA es una plataforma de soporte a la decisión para una distribuidora eléctrica: detecta consumos anómalos a partir de datos de facturación por lotes (origen Oracle) usando reglas de negocio, estadística e Inteligencia Artificial (Isolation Forest), calcula un Índice de Riesgo Energético (IRE) y un Impacto Económico Estimado (IEE), y prioriza inspecciones técnicas integrándose con el sistema de RRHH. El proyecto está en fase de documentación y diseño; todavía no hay código de aplicación.

## Idioma

- La documentación del proyecto se escribe en español neutro y profesional. Sin modismos regionales, sin voseo, sin énfasis estilístico (mayúsculas, exclamaciones).
- Los identificadores de código, comentarios de código y claves de configuración van siempre en inglés.

## Arquitectura

Clean Architecture + Domain-Driven Design (DDD), organizada en capas:

- **Domain:** entidades, value objects, agregados y reglas de negocio del dominio eléctrico.
- **Application:** casos de uso que orquestan el dominio.
- **Infrastructure:** acceso a datos (PostgreSQL), integración con Oracle, integración con el sistema de RRHH, persistencia de modelos de IA.
- **Presentation:** API REST (FastAPI) y frontend (React/TypeScript).

## Stack

Backend en Python con FastAPI, frontend en React con TypeScript, base de datos PostgreSQL, motor de Inteligencia Artificial con Scikit-Learn (Isolation Forest), contenedores con Docker, testing con Pytest (backend) y Playwright (E2E).

## Dónde vive cada tipo de documento

| Carpeta | Contenido |
|---|---|
| `docs/01-business/` | Visión de producto (PRODUCT_VISION.md) y análisis de negocio, reglas de negocio, glosario, KPIs (BUSINESS_ANALYSIS.md) |
| `docs/02-requirements/` | Especificación de requisitos (SRS), historias de usuario, criterios de aceptación |
| `docs/03-architecture/` | Modelo de dominio (DDD), diseño de base de datos, documento de arquitectura de software, especificación de API |
| `docs/04-ai/` | Especificación del motor de IA y plan de análisis de datos |
| `docs/05-devops/` | Seguridad, testing, despliegue, roadmap |

El índice completo con el estado de cada documento está en [`PROJECT_MASTER_SPEC.md`](./PROJECT_MASTER_SPEC.md).

## Convención de commits

Commits convencionales (conventional commits) en español. Nunca agregar "Co-Authored-By" ni ninguna atribución a IA en los mensajes de commit.

## Fuente canónica de reglas de negocio

La fuente canónica de las reglas de negocio (RN-xxx) es `docs/01-business/BUSINESS_ANALYSIS.md`.

Problema conocido pendiente de resolución: `docs/02-requirements/SOFTWARE_REQUIREMENTS_SPECIFICATION.md` (sección 11) y `docs/03-architecture/DOMAIN_MODEL.md` numeran sus propias reglas de negocio también como RN-xxx, pero con esquemas de numeración independientes y no equivalentes entre sí. No usar ni propagar los identificadores RN-xxx de esos dos documentos como si fueran los mismos que los de BUSINESS_ANALYSIS.md hasta que se unifique la numeración.
