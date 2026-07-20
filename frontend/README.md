# EnergIA — Frontend

Aplicación React + TypeScript (Vite) para EnergIA. Esta primera versión implementa un único
recorrido vertical completo — listar suministros, paginado, contra la API real — como base sobre
la que se construirán las pantallas siguientes (historial de consumo, explicación del IRE,
Dashboard Ejecutivo).

## Puesta en marcha

Requisitos: Node 22, pnpm 10.

```bash
cd frontend
pnpm install
pnpm dev          # http://localhost:5173
```

Por defecto la app apunta a `http://localhost:8000` (ver "Variables de entorno"). Para probarla
contra un backend real corriendo en local, ver la sección "Probar contra el backend real en
desarrollo" más abajo.

## Scripts disponibles

| Script | Qué hace |
|---|---|
| `pnpm dev` | Servidor de desarrollo (Vite) con recarga en caliente |
| `pnpm build` | Typecheck (`tsc --noEmit`) + build de producción a `dist/` |
| `pnpm preview` | Sirve el build de `dist/` (`vite preview`) |
| `pnpm lint` | ESLint (flat config) |
| `pnpm typecheck` | `tsc --noEmit` |
| `pnpm test` | Vitest con cobertura (gate del 85 %, ver "Estrategia de testing") |
| `pnpm test:watch` | Vitest en modo watch, sin cobertura |
| `pnpm test:e2e` | Smoke E2E con Playwright (build propio `dist-e2e/`, ver más abajo) |

## Variables de entorno

Plantilla en [`env.example`](./env.example) (sin punto inicial, igual que el `env.example` de la
raíz del repositorio — así el archivo versionado no queda oculto como dotfile).

| Variable | Default | Qué controla |
|---|---|---|
| `VITE_API_BASE_URL` | `http://localhost:8000` | Origen base de la API. Un valor **vacío** (`VITE_API_BASE_URL=`) hace que el cliente emita rutas relativas (`/api/v1/...`), que Vite reenvía server-side vía proxy — ver más abajo. El operador `??` en `src/shared/api/client.ts` solo usa el default ante `null`/`undefined`, nunca ante cadena vacía, así que este comportamiento es intencional, no un descuido. |

## Arquitectura

Estructura "screaming"/por feature, reflejando los bounded contexts del backend, con el patrón
container-presentational aplicado de forma estricta:

```
src/
  shared/
    api/    # cliente HTTP tipado (client.ts) + tipos de paginación genéricos (types.ts)
    ui/     # átomos presentacionales puros: Table, Pagination, Spinner, ErrorState, EmptyState
  features/
    suministros/
      api.ts, hooks.ts, types.ts
      components/
        SuministrosTable.tsx   # presentacional puro: recibe items, no sabe de fetching
        SuministrosPage.tsx    # contenedor: dueño del estado de query + paginación
```

- **`shared/ui`** son átomos sin conocimiento de dominio (reciben props, no hacen fetch). Se
  componen desde `features/*` en piezas con contexto de negocio (p. ej. `SuministrosTable` define
  las columnas propias de un suministro usando el `Table` genérico de `shared/ui`).
- **Contenedor vs. presentacional:** `SuministrosPage` es el único lugar que conoce
  `useSuministros` y el estado de paginación (`offset` en estado de componente, no en la URL —
  la opción más simple para este sprint; si más adelante hace falta compartir/bookmarkear una
  página puntual, migrar a `URLSearchParams` es el paso natural). `SuministrosTable` no tiene
  lógica de datos: solo recibe `items` y renderiza.
- **Tipos escritos a mano, sin codegen:** `Suministro`/`SuministrosPage` (en
  `features/suministros/types.ts`) replican `SuministroSchema`/`SuministrosPageSchema` del backend
  campo a campo. Para este sprint es deliberado (superficie de API pequeña, un solo endpoint
  consumido); si la superficie crece, `openapi-typescript` contra el OpenAPI que FastAPI ya expone
  es la vía natural para dejar de mantenerlos a mano.

## Estrategia de testing

| Capa | Herramienta | Nota |
|---|---|---|
| Unidad/componente/hook | Vitest + Testing Library + MSW (`msw/node`) | MSW corre en modo Node (`setupServer`), sin Service Worker de navegador — no hace falta para mockear en tests que ya corren en jsdom |
| E2E | Playwright, 1 smoke test | Mockea la API con `page.route()` a nivel de red (ver debajo) |

**Cobertura mínima: 85 %** (`vite.config.ts`, `test.coverage.thresholds`). No es un número elegido
al pasar: RNF-006 (`docs/02-requirements/SOFTWARE_REQUIREMENTS_SPECIFICATION.md`), ya referenciado
por ADR-003, exige explícitamente una cobertura mínima de frontend del 85 %. Es más bajo que el
gate del backend (90 %) porque ese es un umbral propio y ya vigente para Python, sin relación con
RNF-006.

**Por qué `page.route()` y no `msw/browser` para el E2E:** ambas opciones son válidas para mockear
en un navegador real. Se eligió la interceptación de red nativa de Playwright porque es más simple
y más robusta en CI: no requiere generar ni servir el Service Worker de MSW (`public/mockServiceWorker.js`)
ni inicializarlo en `main.tsx`, una pieza móvil menos para un sprint que solo necesita un smoke
test.

**Por qué el E2E usa un build propio (`dist-e2e/`, vía `pnpm build:e2e` + `pnpm preview:e2e`):**
ese build se compila con `VITE_API_BASE_URL=""`, de modo que el bundle emite pedidos relativos
(mismo origen que `vite preview`). Así `page.route()` los intercepta sin depender de un backend
real ni de configurar CORS en la respuesta mockeada — el build de producción normal (`pnpm build`
→ `dist/`) sigue usando el default documentado (`http://localhost:8000`) sin verse afectado.

## Probar contra el backend real en desarrollo

El backend (`backend/src/energia/api/app.py`) no tiene middleware de CORS configurado — y este
proyecto no debe modificar `backend/` para agregarlo. La solución es un proxy del lado del
servidor de Vite, no CORS del lado del navegador:

`vite.config.ts` define `server.proxy` y `preview.proxy` reenviando `/api` →
`http://localhost:8000`. Con `VITE_API_BASE_URL=""` el cliente emite pedidos relativos
(`/api/v1/suministros`), que nunca cruzan un origen distinto en el navegador — Vite los reenvía
server-side.

```bash
# 1. Base de datos (si no está corriendo ya)
docker compose up -d db

# 2. Backend, en una terminal
cd backend && make run

# 3. Frontend, en otra terminal — VITE_API_BASE_URL vacío fuerza rutas relativas vía el proxy
cd frontend && VITE_API_BASE_URL= pnpm dev
```

## Deuda conocida

1. **`cliente_id` y `categoria_tarifaria_id` se muestran como UUID crudo.** `GET
   /api/v1/suministros` no expone todavía un endpoint de resolución de nombres, así que la columna
   "Categoría" de la tabla (y cualquier referencia a cliente) muestra el UUID tal cual — un gap
   conocido, no un error. Seguimiento en `PROJECT_MASTER_SPEC.md`.
2. **Storybook (RNF-004) no está armado en este sprint.** RNF-006 (cobertura) sí se cumple; RNF-004
   (documentar todos los componentes React vía Storybook) queda pendiente para un sprint
   siguiente. Seguimiento en `PROJECT_MASTER_SPEC.md`.
