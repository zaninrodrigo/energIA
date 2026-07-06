# Backend — EnergIA API

API FastAPI del backend de EnergIA (Clean Architecture + DDD, ver ADR-001, ADR-002, ADR-006 en
`../docs/03-architecture/adr/`).

## Puesta en marcha rápida

Requisitos: Python 3.12, y la base de datos local levantada (`docker compose up -d db` desde la
raíz del repositorio — ver `../README.md`).

```bash
cd backend
make install   # crea .venv e instala el proyecto en modo editable con dependencias de dev
make test      # corre unit + integration con gate de cobertura (90% mínimo)
make run       # levanta uvicorn con reload en http://localhost:8000
```

Verificación rápida del servidor:

```bash
curl http://localhost:8000/health
# {"status":"ok","database":"up"}
```

## Targets de Makefile

| Target | Qué hace |
|---|---|
| `make install` | Crea `.venv` e instala el paquete en modo editable junto con las dependencias de desarrollo (`pip install -e ".[dev]"`). |
| `make lint` | `ruff check` (reglas) + `ruff format --check` (estilo); no modifica archivos. |
| `make format` | Aplica `ruff check --fix` y `ruff format` in place. |
| `make typecheck` | `mypy src` — tipado estricto (`disallow_untyped_defs`) solo sobre `src/`. |
| `make test` | Suite completa (unit + integration) con cobertura; falla si baja del 90% (`pyproject.toml`). |
| `make run` | Sirve la API con `uvicorn --reload` en el puerto 8000. |

## Tests

- `tests/unit/` — no requieren red ni base de datos real; la dependencia de sesión de base de
  datos se sobreescribe (`app.dependency_overrides`) para simular los casos "up" y "down" de
  `/health`.
- `tests/integration/` — marcados con `@pytest.mark.integration`, requieren la base de datos
  local corriendo (contenedor `energia-db`, puerto host `5434`). Los valores por defecto de
  `Settings` ya apuntan ahí, así que no hace falta configurar variables de entorno para
  correrlos en desarrollo.

## Configuración

Variables de entorno leídas por `energia.shared.config.Settings` (ver `../env.example`):
`POSTGRES_HOST`, `POSTGRES_HOST_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
ensamblan la URL de conexión; `DATABASE_URL`, si está definida, la reemplaza por completo.

## Estructura

```
backend/
  src/energia/
    api/            # FastAPI app factory + routers (presentation)
    shared/          # config y wiring de base de datos, transversal a todos los contextos
    contexts/         # un paquete por bounded context (ver contexts/README.md) — vacío hasta
                        # que aterrice la primera feature de cada contexto
  tests/
    unit/            # sin red ni DB real
    integration/      # contra energia-db real
```
