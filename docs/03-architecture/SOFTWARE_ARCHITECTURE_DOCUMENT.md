# SOFTWARE_ARCHITECTURE_DOCUMENT.md

# EnergIA

Software Architecture Document

Versión 1.0

---

# 1. Introducción

1.1 Propósito

1.2 Alcance

1.3 Objetivos

1.4 Stakeholders

1.5 Referencias

---

# 2. Visión Arquitectónica

2.1 Objetivos de la Arquitectura

2.2 Restricciones

2.3 Drivers Arquitectónicos

2.4 Atributos de Calidad

- Escalabilidad
- Performance
- Disponibilidad
- Mantenibilidad
- Seguridad
- Testabilidad
- Observabilidad

---

# 3. Principios Arquitectónicos

3.1 Clean Architecture

3.2 Domain Driven Design

3.3 SOLID

3.4 DRY

3.5 KISS

3.6 YAGNI

3.7 API First

3.8 12 Factor App

---

# 4. Vista General

Diagrama general del sistema

---

# 5. Arquitectura Lógica

Frontend

Backend

Motor IA

Persistencia

Servicios

Integraciones

---

# 6. Arquitectura Física

Docker

React

FastAPI

PostgreSQL

---

# 7. Clean Architecture

Domain

Application

Infrastructure

Presentation

Shared

---

# 8. Domain Driven Design

Bounded Contexts

Aggregates

Entities

Value Objects

Repositories

Domain Services

Application Services

---

# 9. Arquitectura React

Pages

Features

Components

Hooks

Services

Layouts

Contexts

---

# 10. Arquitectura Backend

FastAPI

Controllers

Use Cases

Repositories

Services

DTO

Entities

Infrastructure

---

# 11. Data Ingestion Pipeline

Carga Excel

CSV

Validación

Normalización

Persistencia

Feature Engineering

---

# 12. Arquitectura del Motor IA

Isolation Forest

↓

Anomaly Score

↓

IRE

↓

Persistencia

---

# 13. Planificador Inteligente

Ranking

Priorización

Agrupación

Asignación

---

# 14. Arquitectura de Datos

PostgreSQL

Esquemas

Índices

Versionado

---

# 15. Seguridad

JWT

RBAC

OWASP

Auditoría

Rate Limit

HTTPS

---

# 16. Observabilidad

Logging

Metrics

Tracing

Health Checks

Audit Logs

---

# 17. Testing

Unit

Integration

E2E

Performance

Security

---

# 18. DevOps

Docker

GitHub Actions

CI/CD

---

# 19. Decisiones Arquitectónicas

Los Architectural Decision Records (ADR) del proyecto viven en `docs/03-architecture/adr/`. Son registros inmutables: una vez aceptado, un ADR no se edita para cambiar la decisión — si la decisión cambia, se crea un nuevo ADR que reemplaza al anterior. Cada ADR sigue el ciclo de estados Propuesto → Aceptado → Reemplazado.

| ID | Título | Estado | Enlace |
|---|---|---|---|
| ADR-001 | Estilo arquitectónico del backend — Clean Architecture + DDD táctico | Aceptado | [adr/ADR-001-clean-architecture-ddd-tactico.md](adr/ADR-001-clean-architecture-ddd-tactico.md) |
| ADR-002 | Plataforma backend — Python + FastAPI | Aceptado | [adr/ADR-002-backend-python-fastapi.md](adr/ADR-002-backend-python-fastapi.md) |
| ADR-003 | Frontend — React + TypeScript | Aceptado | [adr/ADR-003-frontend-react-typescript.md](adr/ADR-003-frontend-react-typescript.md) |
| ADR-004 | Almacén analítico-operativo — PostgreSQL propio, Oracle como fuente de solo lectura vía ETL incremental | Aceptado | [adr/ADR-004-almacen-postgresql-oracle-etl.md](adr/ADR-004-almacen-postgresql-oracle-etl.md) |
| ADR-005 | Motor de detección — enfoque híbrido (reglas + estadística + Isolation Forest no supervisado) | Aceptado | [adr/ADR-005-motor-deteccion-hibrido.md](adr/ADR-005-motor-deteccion-hibrido.md) |
| ADR-006 | Topología de despliegue — monolito modular contenedorizado (Docker), no microservicios | Aceptado | [adr/ADR-006-monolito-modular-docker.md](adr/ADR-006-monolito-modular-docker.md) |
| ADR-007 | Modelo de procesamiento — batch orientado a lotes de facturación, no streaming | Aceptado | [adr/ADR-007-procesamiento-batch-no-streaming.md](adr/ADR-007-procesamiento-batch-no-streaming.md) |

---

# 20. Diagramas C4

Context

Container

Component

Code

---

# 21. Diagramas de Secuencia

Carga de Datos

Detección

Inspección

---

# 22. Escalabilidad

Escenario 100.000 suministros

Escenario 500.000 suministros

Escenario 1.000.000 suministros

---

# 23. Riesgos

Técnicos

Negocio

IA

Datos

---

# 24. Roadmap Tecnológico

v1

v2

v3

---

# 25. Conclusiones