# EnergIA

Plataforma de inteligencia operacional que detecta consumos eléctricos anómalos y prioriza inspecciones técnicas mediante reglas de negocio, estadística e Inteligencia Artificial.

## Problema y solución

Las distribuidoras eléctricas administran cientos de miles de suministros cuyos consumos se actualizan en cada lote de facturación. Analizar manualmente esos volúmenes para detectar anomalías es lento, depende de la experiencia de cada operador y suele derivar en inspecciones sobre suministros de bajo impacto mientras casos relevantes pasan desapercibidos.

EnergIA incorpora un Motor de Inteligencia Energética que analiza automáticamente cada consumo procesado, combinando reglas de negocio del dominio eléctrico, análisis estadístico de históricos e Isolation Forest (Scikit-Learn). Con ese análisis calcula, para cada suministro, un Índice de Riesgo Energético (IRE, escala 0-100) y un Impacto Económico Estimado (IEE), y genera un ranking priorizado de inspecciones que se integra con el sistema de RRHH para crear órdenes de trabajo. El objetivo es asistir la toma de decisiones de analistas e inspectores, no reemplazar su criterio técnico.

## Estado del proyecto

Sprint 0 — esqueleto de backend. El repositorio contiene la especificación funcional, de negocio, de arquitectura y de dominio, y ahora también el andamiaje inicial del backend (FastAPI, Clean Architecture, endpoint de salud, cobertura de tests ≥ 90%) sobre el cual se construirán los bounded contexts.

## Mapa de documentación

| Carpeta | Documentos | Estado |
|---|---|---|
| `docs/01-business` | PRODUCT_VISION.md, BUSINESS_ANALYSIS.md | Completo / Borrador |
| `docs/02-requirements` | SOFTWARE_REQUIREMENTS_SPECIFICATION.md, USER_STORIES.md, ACCEPTANCE_CRITERIA.md | Completo |
| `docs/03-architecture` | DOMAIN_MODEL.md, DATABASE_DESIGN.md, SOFTWARE_ARCHITECTURE_DOCUMENT.md, API_SPEC.md | Completo / Completo / Esqueleto / Pendiente |
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

## Base de datos local

PostgreSQL 16 corre en Docker para desarrollo local. El DDL ejecutable (24 tablas, particionado de `consumos`, restricciones CHECK mapeadas a invariantes de dominio) vive en [`docker/postgres/init/`](./docker/postgres/init/); las decisiones detrás de ese diseño están documentadas en [`docs/03-architecture/DATABASE_DESIGN.md`](./docs/03-architecture/DATABASE_DESIGN.md).

Requisitos: Docker y Docker Compose.

```bash
cp env.example .env        # ajustar credenciales si hace falta
docker compose up -d db
docker compose ps           # esperar "healthy"
```

Conexión (puerto host **5434**, no 5432 — ver DATABASE_DESIGN.md §2):

```bash
psql -h localhost -p 5434 -U energia -d energia
# o sin instalar psql en el host:
docker exec -it energia-db psql -U energia -d energia
```

## Backend

API FastAPI (Clean Architecture + DDD, ver `docs/03-architecture/adr/ADR-001` y siguientes). Requiere Python 3.12 y la base de datos local levantada (sección anterior).

```bash
cd backend
make install   # crea .venv e instala el proyecto en modo editable con dependencias de dev
make test      # unit + integration, con gate de cobertura del 90%
make run       # uvicorn con reload en http://localhost:8000
```

Detalle completo (targets de Makefile, estructura, variables de entorno) en [`backend/README.md`](./backend/README.md).

## Datos sintéticos

`backend/src/energia/tools/synthetic/` genera un dataset determinístico de clientes,
suministros, lecturas y consumos, y lo carga en una instancia de EnergIA a través de su propia
API de importación (no escribe directo a la base). Sirve para tener datos de prueba realistas
—con estacionalidad, tendencia y anomalías de consumo conocidas— sin depender de acceso a Oracle
(todavía no existe, ADR-004) ni de un archivo histórico real.

**Uso rápido** (con la API corriendo, `cd backend && make run` en otra terminal):

```bash
cd backend
make seed-synthetic BASE_URL=http://localhost:8000 SCALE=small SEED=42
```

| Concepto | Qué es |
|---|---|
| `--scale` | Tamaño del dataset: `small` (100 suministros/24 meses), `medium` (1000/36), `large` (5000/36) |
| `--seed` | Semilla determinística: misma semilla + escala → mismo dataset y manifiesto, byte a byte. Cada identidad natural (`numero_suministro`, `numero_cliente`, `codigo_lote`) incluye la semilla (p. ej. `SYN-S42-SUM-00001`), así que dos semillas distintas nunca colisionan ni se pisan entre sí al cargarse contra la misma instancia |
| Anomalías de fuerza de regla | `sudden_drop` (caída 60-80%, permanente), `zero_consumption_streak` (3-6 meses en cero), `gradual_decline` (-5%/mes durante 12 meses), `spike` (un mes 3-5x, no fraudulento) — cada una calibrada para disparar, con margen, alguna regla R1/R2/R3 (`AI_ENGINE_SPEC.md` sec. 8) |
| Anomalías sub-umbral | `sudden_drop_leve` (caída 30-50%, deliberadamente por debajo del -60% de R2) y `spike_leve` (multiplicador 1.8-2.6x, por debajo del +200% de R3) — a estas solo las puede detectar el motor estadístico o Isolation Forest, nunca las reglas de negocio; existen para aislar el aporte real de esas dos ramas del motor |
| `manifest.json` | Ground truth: qué suministro recibió qué anomalía, en qué período y con qué parámetros — se escribe en `datasets/synthetic/<scale>-seed<seed>/manifest.json`. Para `sudden_drop(_leve)`/`spike(_leve)`, `parametros.pct_change_first_month` es el cambio porcentual REALIZADO (calculado directo de los `kwh` persistidos, nunca a partir del parámetro sorteado), para que ningún consumidor de calibración tenga que re-derivarlo |

El manifiesto es lo que le da valor al dataset: al no depender de datos reales etiquetados
(que no existen todavía), es la única forma de saber con certeza qué debería detectar el Motor
de Inteligencia Energética al procesar este dataset — la referencia para calibrar y probar sus
Etapas 3 a 6 (reglas de negocio, estadística e Isolation Forest, `docs/04-ai/AI_ENGINE_SPEC.md`
secciones 6 a 9) antes de tener datos de producción reales.

Detalle completo (flags, estructura de módulos) en [`backend/README.md`](./backend/README.md).

## Estructura del repositorio

```
energIA/
├── backend/           # API FastAPI (Clean Architecture + DDD) — esqueleto Sprint 0
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
