# EnergIA

**Plataforma de soporte a la decisión que detecta consumos eléctricos anómalos y prioriza inspecciones técnicas**, combinando reglas de negocio, análisis estadístico e Inteligencia Artificial. Para cada suministro calcula un Índice de Riesgo Energético (IRE, 0-100) y un Impacto Económico Estimado (IEE), y produce un ranking de inspección que explica, factor por factor, por qué cada medidor es sospechoso.

Está construida para una distribuidora eléctrica: procesa los datos de facturación por lotes, analiza el histórico de cada medidor y le dice al equipo técnico **a qué inspeccionar primero y por qué** — sin reemplazar el criterio del inspector, sino ordenándole el trabajo.

---

## Qué hace hoy

El sistema funciona de punta a punta: un lote de facturación entra crudo y sale con cada medidor puntuado, clasificado y georreferenciado.

| Capacidad | Detalle |
|---|---|
| **Importación de datos** | Clientes, suministros, lecturas, consumos y lotes se cargan por API, con validación e idempotencia (una re-importación no duplica ni corrompe). |
| **Motor de Inteligencia Energética** | Pipeline de 8 etapas: validación de integridad → detección de duplicidades → 17 features → indicadores estadísticos → reglas de negocio → Isolation Forest → composición del IRE → estimación del IEE. |
| **Ranking de riesgo** | Los suministros de un lote procesado se ordenan por IRE descendente: la lista priorizada de inspección (RN-009). |
| **Explicabilidad (RN-012)** | Cada puntaje trae su desglose: qué factor aportó cuántos puntos y por qué, con el aporte del modelo de IA marcado honestamente como aproximación. |
| **Mapa de riesgo** | Los medidores se grafican geolocalizados, coloreados y dimensionados por su nivel de riesgo. |
| **Riesgo por barrio** | Vista agregada por localidad y barrio, coloreada por su medidor de mayor potencial — para decidir a qué zona mandar la cuadrilla. |

Tres pantallas web consumen todo esto: **Suministros**, **Ranking de Riesgo** (con mapa) y **Riesgo por Barrio**.

---

## Cómo verlo funcionar

Requisitos: Docker, Python 3.12, Node 22 y pnpm.

```bash
# 1. Base de datos (PostgreSQL 16 en Docker, puerto host 5434)
cp env.example .env
docker compose up -d db

# 2. Backend (FastAPI) — en una terminal
cd backend && make install && make run       # http://localhost:8000

# 3. Datos de prueba con anomalías conocidas — en otra terminal
cd backend && make seed-synthetic BASE_URL=http://localhost:8000 SCALE=small SEED=42

# 4. Procesar algunos lotes por el motor (para poblar el ranking)
curl -X POST http://localhost:8000/api/v1/motor/lotes/LOTE-SYN-S42-2023-07/procesar

# 5. Frontend — en otra terminal
cd frontend && pnpm install && pnpm dev       # http://localhost:5173
```

Abrí **http://localhost:5173**, entrá a **Ranking de Riesgo**, elegí un lote y hacé clic en el medidor de mayor riesgo del mapa: se abre el desglose de por qué fue marcado. Ese panel es el corazón del sistema.

Guías detalladas: [`backend/README.md`](./backend/README.md) · [`frontend/README.md`](./frontend/README.md).

---

## Cómo funciona por dentro

- **Arquitectura:** Clean Architecture + Domain-Driven Design. Cada bounded context (clientes, suministros, consumos, motor) tiene sus capas de dominio, aplicación, infraestructura y presentación. Las decisiones están registradas como ADR en [`docs/03-architecture/adr/`](./docs/03-architecture/adr/).
- **Motor híbrido:** reglas explícitas (auditables), estadística (z-score, percentiles de cohorte) e Isolation Forest no supervisado convergen en el IRE. El diseño completo, con sus 18 decisiones validadas, está en [`docs/04-ai/AI_ENGINE_SPEC.md`](./docs/04-ai/AI_ENGINE_SPEC.md).
- **Datos sintéticos con verdad conocida:** el generador ([`backend/src/energia/tools/synthetic/`](./backend/src/energia/tools/synthetic/)) crea medidores con estacionalidad, tendencia y anomalías plantadas, y escribe un `manifest.json` con qué anomalía tiene cada suministro. Es la referencia para medir cuánto detecta el motor sin depender de datos reales etiquetados (que todavía no existen).

**Stack:** Python/FastAPI · React/TypeScript/Vite · PostgreSQL 16 · Scikit-Learn (Isolation Forest) · Docker · Pytest/Playwright · Leaflet (mapas).

---

## Calidad

| Métrica | Estado |
|---|---|
| Tests backend | ~965, gate de cobertura 90% |
| Tests frontend | 169, gate de cobertura 85% (RNF-006) |
| Integración continua | GitHub Actions (backend + frontend) en cada push |
| Base de datos | DDL ejecutable, 24 tablas, restricciones CHECK mapeadas a invariantes de dominio |

Todo el código pasó por desarrollo dirigido por tests (TDD) y por una revisión adversarial de contexto fresco antes de integrarse.

---

## Estado y alcance

El sistema está **funcionando de punta a punta**. Para ser transparente sobre qué es permanente y qué es andamiaje de demostración:

- **Permanente:** toda la arquitectura, el motor de IA, la base de datos, los endpoints y las pantallas.
- **Datos de demostración:** los medidores, sus números de rutafolio, sus coordenadas de Formosa y sus barrios son sintéticos (generados/rellenados), a la espera de los datos reales de producción.

**Pendientes conocidos** (ninguno bloquea la operación; detalle en [`PROJECT_MASTER_SPEC.md`](./PROJECT_MASTER_SPEC.md)):

1. Integrar rutafolio, georreferencia y barrio al pipeline de importación (hoy se cargan por backfill).
2. Recalibrar los pesos del IRE con datos reales (DEC-014).
3. Definir la matriz de roles y permisos al implementar autenticación.

---

## Documentación

Índice completo y estado de cada documento en [`PROJECT_MASTER_SPEC.md`](./PROJECT_MASTER_SPEC.md).

| Carpeta | Contenido | Estado |
|---|---|---|
| `docs/01-business` | Visión de producto, análisis de negocio, reglas de negocio | Completo |
| `docs/02-requirements` | SRS (IEEE 29148), historias de usuario, criterios de aceptación | Completo |
| `docs/03-architecture` | Modelo de dominio, diseño de base de datos, ADRs, especificación de API | Completo / DDL ejecutable / 7 ADR aceptados |
| `docs/04-ai` | Especificación del Motor de Inteligencia Energética | Aceptado (v1.0.0) |
| `docs/05-devops` | Seguridad, testing, despliegue, roadmap | Pendiente |

---

## Estructura del repositorio

```
energIA/
├── backend/                # API FastAPI (Clean Architecture + DDD) + motor de IA + generador sintético
├── frontend/               # Aplicación React/TypeScript (Vite): 3 pantallas con mapas
├── docker/                 # PostgreSQL local + DDL ejecutable (docker/postgres/init/)
├── docs/
│   ├── 01-business/            # Visión de producto y análisis de negocio
│   ├── 02-requirements/        # SRS, historias de usuario, criterios de aceptación
│   ├── 03-architecture/        # Dominio, base de datos, arquitectura, API, ADRs
│   ├── 04-ai/                  # Especificación del motor de IA
│   └── 05-devops/              # Seguridad, testing, despliegue, roadmap
├── PROJECT_MASTER_SPEC.md  # Índice maestro de documentación y deuda conocida
├── CLAUDE.md               # Instrucciones para asistentes de IA que trabajen en el repositorio
└── README.md
```
