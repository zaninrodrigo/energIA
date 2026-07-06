# ADR-002: Plataforma backend — Python + FastAPI

| Campo | Valor |
|---|---|
| Estado | Propuesto |
| Fecha | 2026-07-06 |
| Autor | Rodrigo Zanin |
| Decisores | Pendiente de validación |

## Contexto

`docs/01-business/PRODUCT_VISION.md` §15 (Visión Tecnológica) ya declara Backend: FastAPI + Python, e Inteligencia Artificial: Scikit-Learn + Isolation Forest. `docs/02-requirements/SOFTWARE_REQUIREMENTS_SPECIFICATION.md` §8 fija "Backend desarrollado en FastAPI" como restricción, y RNF-003 exige que todas las APIs se documenten mediante OpenAPI.

El pipeline de detección de anomalías (Scikit-Learn, Isolation Forest) es Python por naturaleza: es el ecosistema de referencia para ese tipo de modelos. El equipo es un desarrollador único.

## Decisión

Adoptar **Python + FastAPI** como plataforma del backend y de la capa de API, el mismo lenguaje que el motor de Machine Learning (Scikit-Learn / Isolation Forest).

## Alternativas consideradas

### Django + Django REST Framework (DRF)

Batteries-included: admin panel, ORM, sistema de autenticación y migraciones ya resueltos de fábrica. **Esta alternativa gana** en aplicaciones con fuerte necesidad de paneles administrativos CRUD y donde no se requiere async nativo — habría acelerado el arranque de un desarrollador único que hoy tiene que construir a mano parte de ese andamiaje (paneles de administración, gestión de usuarios).

Se descarta porque el soporte async nativo y la generación automática de contratos tipados (OpenAPI vía Pydantic) es más directa en FastAPI, y RNF-003 exige documentación OpenAPI como requisito explícito, no como añadido opcional vía librerías de terceros (drf-spectacular u similares en DRF).

### Node.js + NestJS

Ganaría en homogeneidad de lenguaje si todo el stack (frontend React/TypeScript + backend) compartiera TypeScript de punta a punta: mismo pool de contrataciones, tipos compartidos entre cliente y servidor, una sola toolchain.

Se descarta porque el motor de ML (Scikit-Learn) es Python y no tiene un equivalente maduro en el ecosistema Node. Adoptar NestJS obligaría a separar la API de negocio (Node) del motor de IA (Python) en dos procesos/servicios distintos, comunicados por HTTP o gRPC. Eso introduce exactamente el límite de integración que se busca evitar, además de un salto de red adicional en el camino crítico del análisis de lote — un riesgo directo para RNF-001 (< 10 minutos de análisis por lote).

## Consecuencias

### Positivas

- Un único lenguaje para API y motor de IA elimina el límite de integración (serialización, contrato de red) entre ambos.
- FastAPI genera OpenAPI automáticamente a partir de los modelos Pydantic, cumpliendo RNF-003 sin trabajo adicional.
- Pydantic aporta validación de payloads y contratos tipados en los límites de la API.

### Negativas / costos aceptados

- Python es más lento que Node/Java en cómputo intensivo de CPU, y el GIL limita el paralelismo real dentro de un mismo proceso. El análisis de lote (RNF-001: < 10 minutos) sobre volúmenes de RNF-007 (> 500.000 suministros) es justamente CPU-bound, no I/O-bound — el async nativo de FastAPI no ayuda ahí; ese cuello de botella requiere workers/multiprocessing explícitos, complejidad que Node no evitaría del todo pero que Python hace más visible desde el día uno.
- Se pierde el andamiaje "batteries included" de Django (panel de administración, gestión de usuarios lista para usar). Para un desarrollador único, eso significa construir a mano piezas que Django resuelve de fábrica.

### Riesgos y mitigaciones

- **Riesgo:** el análisis de lote no cumple RNF-001 al escalar hacia los volúmenes de RNF-007. **Mitigación:** paralelizar el scoring de Isolation Forest mediante multiprocessing/joblib (ver también ADR-006 sobre aislar el cómputo pesado en un proceso worker separado), y medir el tiempo real de análisis en pruebas de carga antes del lanzamiento de v1.
