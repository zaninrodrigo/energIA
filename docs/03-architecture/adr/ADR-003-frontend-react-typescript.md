# ADR-003: Frontend — React + TypeScript

| Campo | Valor |
|---|---|
| Estado | Aceptado |
| Fecha | 2026-07-06 |
| Autor | Rodrigo Zanin |
| Decisores | Rodrigo Zanin (2026-07-06) |

## Contexto

`docs/01-business/PRODUCT_VISION.md` §15 declara Frontend: React + TypeScript. `docs/02-requirements/SOFTWARE_REQUIREMENTS_SPECIFICATION.md` §8 fija "Frontend desarrollado en React" como restricción; RNF-004 exige que todos los componentes React se documenten mediante Storybook, y RNF-006 exige una cobertura mínima de frontend del 85%.

El producto es intensivo en dashboards e interactividad: RF-012 exige visualizar el historial completo de consumo de un suministro, RF-013 exige mostrar la explicación del IRE, RF-018 exige filtrar simultáneamente por localidad, barrio, categoría, lote, estado e IRE, y el bounded context "Dashboard Ejecutivo" (`DOMAIN_MODEL.md` §4.7) es responsable de la visualización de indicadores gerenciales.

## Decisión

Adoptar **React + TypeScript** como stack de frontend, con contratos tipados generados contra la API (OpenAPI/Pydantic del lado del backend).

## Alternativas consideradas

### Vue

Curva de aprendizaje más suave y menos boilerplate que React para equipos chicos. **Ganaría** en una herramienta interna simple con necesidades de interactividad bajas y sin exigencia de un ecosistema de tipado tan maduro como el de React + TypeScript. En este proyecto la elección ya está fijada como restricción explícita en PRODUCT_VISION.md y en el SRS, por lo que esta alternativa se documenta por completitud del análisis, no como opción realmente abierta.

### Server-rendered (Jinja2 / HTMX servido desde el propio FastAPI)

Simplicidad genuina para dashboards internos: sin build de SPA separado, sin superficie doble de CORS/autenticación, un único artefacto desplegable, sin gestión de estado de cliente. **Esta alternativa gana** de forma honesta si el producto fuera únicamente un conjunto de vistas de consulta estáticas construidas por un desarrollador único sin necesidad de interactividad rica — que es, de hecho, la descripción exacta del contexto "Dashboard Ejecutivo" (§4.7: "exclusivamente de consultas y visualización... no contiene reglas de negocio").

Se descarta para el frontend completo porque RNF-004 exige documentación de componentes vía Storybook, un concepto que asume una arquitectura basada en componentes de UI reutilizables y aislados — HTMX no tiene una unidad equivalente de "componente documentable de forma aislada", por lo que adoptarlo entraría en conflicto directo con un requisito no funcional ya declarado. Además, RF-012 (historial completo de consumo, previsiblemente con gráficos de series temporales) y RF-013 (explicación interactiva del IRE) se benefician de un ecosistema de componentes ricos que HTMX no ofrece de forma nativa.

## Consecuencias

### Positivas

- Contratos tipados entre API y UI reducen errores de integración en tiempo de ejecución.
- Storybook (RNF-004) encaja de forma nativa con una arquitectura basada en componentes React.
- El ecosistema de gráficos e interactividad de React facilita RF-012 y RF-013 (explicabilidad visual del IRE).

### Negativas / costos aceptados

- Para el contexto "Dashboard Ejecutivo" — que por definición propia no contiene reglas de negocio y es solo consulta/visualización — una SPA completa es más infraestructura de la que ese contexto puntual necesita: build pipeline separado, configuración de CORS, y manejo de sesión/autenticación duplicado entre API y SPA frente a lo que un enfoque server-rendered habría resuelto con menos piezas móviles.

### Riesgos y mitigaciones

- **Riesgo:** sobre-construir estado de cliente donde solo se necesita presentar datos de solo lectura. **Mitigación:** aplicar el patrón container-presentational de forma estricta en las vistas de Dashboard Ejecutivo, manteniendo esos componentes deliberadamente simples y sin lógica de estado innecesaria, en vez de aplicar la misma complejidad que en las vistas operativas con interactividad real.
