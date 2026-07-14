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
  - La mayoría (todo lo que no sea `test_health_integration.py`) corre contra una base
    `energia_test` separada, nunca contra `energia`: `tests/integration/conftest.py` la crea
    (o recrea) y reaplica el DDL de `docker/postgres/init/*.sql` una vez por sesión de test;
    cada contexto compone sus propios fixtures de app/cliente/limpieza sobre esa base (ver
    `tests/integration/contexts/clientes/conftest.py` como primer ejemplo). Funciona igual en
    CI: solo depende de las mismas variables de entorno que `Settings`.

## Configuración

Variables de entorno leídas por `energia.shared.config.Settings` (ver `../env.example`):
`POSTGRES_HOST`, `POSTGRES_HOST_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
ensamblan la URL de conexión; `DATABASE_URL`, si está definida, la reemplaza por completo.

## Datos sintéticos

`energia.tools.synthetic` genera un dataset determinístico y lo carga en una instancia de
EnergIA a través de su API real de importación (clientes, suministros, lotes, lecturas,
consumos), plantando anomalías de consumo conocidas y registrándolas en un manifiesto de ground
truth (`manifest.json`). Sirve como fixture para calibrar y probar el Motor de Inteligencia
Energética (Etapas 3-6, `docs/04-ai/AI_ENGINE_SPEC.md` secciones 6-9) — ver la sección "Datos
sintéticos" del README raíz para el detalle completo (qué genera, qué contiene el manifiesto,
cómo interpretar el resultado).

Requiere una instancia de la API corriendo (`make run` en otra terminal):

```bash
make seed-synthetic BASE_URL=http://localhost:8000 SCALE=small SEED=42
```

**Flags de `python -m energia.tools.synthetic`** (invocado por el target de arriba):

| Flag | Default | Notas |
|---|---|---|
| `--base-url` | `http://localhost:8000` | Instancia de la API donde importar |
| `--scale` | `small` | `small` (100 suministros/24 meses), `medium` (1000/36), `large` (5000/36) |
| `--seed` | `42` | Misma semilla + escala = mismo dataset y manifiesto, byte a byte. Cada identidad natural (`numero_suministro`, `numero_cliente`, `codigo_lote`) incluye la semilla, así que dos semillas distintas producen datasets disjuntos (nunca se pisan al cargarse contra la misma instancia) |
| `--years` | (según `--scale`) | `2` o `3`; sobrescribe la cantidad de meses por defecto de la escala |
| `--out` | `datasets/synthetic/` | El manifiesto se escribe en `<out>/<scale>-seed<seed>/manifest.json` |
| `--batch-size` | `500` | Registros máximos por POST de importación |

### Esquema de `manifest.json`

- `anomalias[].tipo`: una de las 4 formas de fuerza de regla (`sudden_drop`, `zero_consumption_streak`, `gradual_decline`, `spike`) o una de las 2 formas sub-umbral (`sudden_drop_leve`, `spike_leve`) -- ver `anomalies.py` para el detalle de cada una y por qué existen las sub-umbral (aislar el aporte de las ramas estadística/Isolation Forest del motor, que un dataset con solo anomalías de fuerza de regla no puede demostrar).
- `anomalias[].parametros.pct_change_first_month` (solo para los 4 tipos anclados: `sudden_drop(_leve)`/`spike(_leve)`): el cambio porcentual mes a mes REALIZADO en el primer mes afectado, calculado directamente de los valores de `kwh` efectivamente persistidos -- no re-derivado del parámetro sorteado (`drop_fraction`/`multiplier`), que por sí solo no garantiza ese resultado exacto una vez aplicados estacionalidad/tendencia/ruido.
- `anomalias[].periodos_afectados` vs `parametros.duration_months` (solo en `gradual_decline`): son alcances distintos. `periodos_afectados` es la cola completa desde `periodo_inicio` hasta el final de la serie de ese suministro (la caída se sostiene de forma permanente). `parametros.duration_months` es solo la ventana activa de declive (-5%/mes, hasta 12 meses); después de esa ventana el consumo queda constante en el nivel alcanzado, pero sigue contando dentro de `periodos_afectados`.

## Estructura

```
backend/
  src/energia/
    api/            # FastAPI app factory + routers (presentation)
    shared/          # config y wiring de base de datos, transversal a todos los contextos
    contexts/         # un paquete por bounded context (ver contexts/README.md)
      clientes/         # Gestión de Clientes — implementado (US-001, import + listado)
    tools/
      synthetic/       # generador de datos sintéticos (ver "Datos sintéticos" arriba)
  tests/
    unit/            # sin red ni DB real
    integration/      # contra energia-db real
```
