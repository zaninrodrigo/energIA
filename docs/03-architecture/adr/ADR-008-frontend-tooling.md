# ADR-008: Frontend — herramientas de build, estado de servidor, routing y testing

| Campo | Valor |
|---|---|
| Estado | Aceptado |
| Fecha | 2026-07-20 |
| Autor | Rodrigo Zanin |
| Decisores | Rodrigo Zanin (2026-07-20) |

## Contexto

ADR-003 fijó **React + TypeScript** como stack de frontend, pero dejó abiertas las herramientas
concretas de build, gestión de estado de servidor, routing y testing con las que implementar ese
stack. Este ADR registra esas decisiones al construir el primer esqueleto de frontend (`frontend/`)
y su primer recorrido vertical completo: listado paginado de suministros contra `GET
/api/v1/suministros` (`docs/03-architecture/API_SPEC.md`, "Contexto: Gestión de Suministros").

RNF-006 (`SOFTWARE_REQUIREMENTS_SPECIFICATION.md` §8, ya referenciado por ADR-003) exige una
cobertura mínima de frontend del 85 %; RNF-004 exige documentar todos los componentes React
mediante Storybook (pendiente, ver `PROJECT_MASTER_SPEC.md`, "Deuda documental conocida"). El
backend (`backend/src/energia/api/app.py`) no tiene middleware de CORS configurado, restricción
que condiciona directamente cómo se prueba el frontend contra un backend real en desarrollo.

## Decisión

- **Build tool: Vite.** Servidor de desarrollo con Hot Module Replacement nativo sobre ESM y build
  de producción con Rollup, sin configuración de bundler manual.
- **Estado de servidor: TanStack Query (`@tanstack/react-query`).** Cachea, deduplica y pagina las
  respuestas de la API; `placeholderData: keepPreviousData` (idioma de la v5) mantiene la página
  anterior visible mientras se resuelve la siguiente, evitando parpadeos de carga en cada click de
  paginación.
- **Routing: React Router.** Una sola ruta hoy (`/` → `SuministrosPage`), como cascarón para las
  pantallas siguientes (historial de consumo, explicación del IRE, Dashboard Ejecutivo — ADR-003,
  Contexto).
- **Tipos escritos a mano, sin codegen.** `Suministro`/`SuministrosPage`
  (`src/features/suministros/types.ts`) replican `SuministroSchema`/`SuministrosPageSchema` del
  backend campo a campo, mantenidos manualmente en lugar de generados desde el OpenAPI que FastAPI
  ya expone.
- **Testing de unidad/componente/hook: Vitest + Testing Library + MSW en modo Node
  (`msw/node`).** MSW intercepta a nivel de módulo HTTP de Node, sin Service Worker de navegador.
- **Cobertura mínima: 85 %, gate aplicado en `vite.config.ts` (`test.coverage.thresholds`).** El
  número no se decide en este ADR: ya es una obligación vigente por RNF-006. Lo que este ADR fija
  es *dónde* se hace cumplir (Vitest + `@vitest/coverage-v8`, mismo mecanismo de gate que
  `pyproject.toml` usa para el 90 % del backend).
- **E2E: Playwright, un único smoke test.** Mockea `GET /api/v1/suministros` a nivel de red con
  `page.route()`, contra un build de `vite preview` compilado con `VITE_API_BASE_URL=""` (rutas
  relativas, mismo origen que el preview, sin necesidad de responder headers de CORS en el mock).

## Alternativas consideradas

### Next.js (en lugar de Vite)

Ganaría si el producto necesitara SSR/SSG, rutas de API propias o SEO — ninguno de los cuales
aplica: EnergIA es una herramienta interna detrás de autenticación, consumida por operadores de la
distribuidora, no un sitio público. Adoptarlo sumaría un servidor Node de aplicación y convenciones
de framework (App Router, server components) sin resolver ningún problema real de este proyecto,
a cambio de una superficie operativa mayor que Vite + una SPA servida como estáticos.

### Redux Toolkit Query (en lugar de TanStack Query)

Alternativa real y comparable en capacidades (cache, invalidación, deduplicación). Se prefirió
TanStack Query porque no exige adoptar Redux como gestor de estado global cuando este frontend, por
ahora, no tiene estado de cliente compartido entre pantallas que lo justifique — únicamente estado
de servidor (datos de la API) y estado local de UI (paginación). Si aparece estado de cliente
genuinamente global y complejo más adelante, esta decisión se puede revisar sin tocar la capa de
datos ya construida (`features/*/hooks.ts` encapsula TanStack Query, no está expuesto directamente
a los componentes de presentación).

### `openapi-typescript` (codegen) en lugar de tipos escritos a mano

Ganaría de forma clara apenas la superficie de API consumida por el frontend crezca más allá de un
puñado de endpoints: mantener tipos a mano deja de ser gratis y empieza a arriesgar divergencia
silenciosa con el backend. Hoy el frontend consume un único endpoint (`GET
/api/v1/suministros`), así que el costo de introducir infraestructura de codegen (pipeline de
generación, sincronización con el OpenAPI del backend, archivos generados a versionar o
regenerar en CI) no se paga todavía. Documentado aquí como la opción a adoptar cuando ese punto de
inflexión llegue, no descartada de forma permanente.

### MSW en modo navegador (`msw/browser` + Service Worker) para el E2E

Alternativa igual de válida en principio: MSW puede interceptar pedidos reales del navegador vía
Service Worker tanto en tests de componente como en E2E, con el mismo set de handlers reutilizado
en ambos casos. Se prefirió la interceptación de red nativa de Playwright (`page.route()`) para el
único smoke test de este sprint porque es más simple y más robusta en CI: no requiere generar ni
servir `public/mockServiceWorker.js` ni inicializarlo en `main.tsx` — una pieza móvil menos para un
sprint que solo necesita confirmar que la app carga y renderiza datos de la API de punta a punta.
Si el número de escenarios E2E crece lo suficiente como para justificar compartir handlers de MSW
entre unit/component tests y E2E, esta decisión se puede revisar.

### Cypress (en lugar de Playwright)

Descartado por comparación directa de capacidades para este caso: Playwright soporta múltiples
motores de navegador con una única API, corre más rápido en CI por su arquitectura fuera del
proceso del navegador, y su primitiva de interceptación de red (`page.route()`) es exactamente la
que resuelve el mockeo del E2E sin piezas adicionales — la razón concreta de la decisión anterior.

## Consecuencias

### Positivas

- TanStack Query resuelve paginación, cache y transiciones de página sin estado manual repetido
  en cada pantalla nueva que la use.
- El cliente HTTP tipado (`src/shared/api/client.ts`) y los tipos escritos a mano dan un contrato
  verificado por `tsc` entre la UI y la forma real de la respuesta del backend, con cero
  infraestructura de codegen que mantener en este sprint.
- El gate de cobertura del 85 % corre en el mismo comando que ya se ejecuta en CI (`pnpm test`),
  sin paso adicional ni configuración externa.
- El smoke E2E corre sin depender de una base de datos ni de un backend real levantado, así que no
  suma tiempo de arranque de servicios a la CI del frontend.

### Negativas / costos aceptados

- Mantener tipos a mano es correcto solo mientras la superficie de API consumida siga siendo
  chica; si crece sin revisar esta decisión, el riesgo de divergencia silenciosa con el backend
  aumenta en silencio.
- El E2E de este sprint corre contra un build (`dist-e2e/`) compilado específicamente con
  `VITE_API_BASE_URL=""`, distinto del build de producción normal (`dist/`) — dos artefactos de
  build a explicar/mantener en lugar de uno, hasta que exista un E2E contra backend real que
  unifique el criterio.
- Ningún escenario E2E ejercita todavía la integración real contra el backend (base de datos,
  CORS real, latencia real): el smoke test solo prueba que la SPA renderiza correctamente los
  datos que recibe, no que el backend real los sirve con esa forma bajo carga.

### Riesgos y mitigaciones

- **Riesgo:** que "un solo smoke E2E mockeado" se lea como cobertura de integración real y postergue
  indefinidamente un E2E contra backend real. **Mitigación:** documentado explícitamente en
  `frontend/README.md` y en los comentarios de `e2e/playwright.config.ts`/`ci.yml` que un job de
  E2E contra backend real llega con la próxima pantalla (Dashboard Ejecutivo), no que esta
  decisión sea definitiva.
- **Riesgo:** tipos a mano desincronizados del backend sin que ningún chequeo automático lo
  detecte. **Mitigación:** la superficie consumida hoy es un único endpoint con schema estable
  (`SuministroSchema`); ante el primer cambio de forma de esa respuesta, el desajuste se manifiesta
  como fallas de tests (`api.test.ts`, `hooks.test.tsx`) contra los fixtures compartidos
  (`src/test/fixtures.ts`), no como un error silencioso en producción.
