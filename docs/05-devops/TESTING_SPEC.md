# Testing Specification

| Versión | Fecha | Estado | Autor |
|---|---|---|---|
| 0.1.0 | 2026-07-03 | Pendiente | Rodrigo Zanin |

## Propósito

Este documento especificará la estrategia de testing de EnergIA, abarcando el backend en Python/FastAPI y el frontend en React/TypeScript. Definirá los niveles de prueba a implementar, las herramientas a utilizar (Pytest y Playwright) y los objetivos de cobertura de código, de modo que la calidad del sistema pueda verificarse de forma objetiva a medida que se desarrollan las funcionalidades descritas en USER_STORIES.md.

## Contenido previsto

- Estrategia general de testing por capa (Domain, Application, Infrastructure, Presentation).
- Pruebas unitarias de backend con Pytest: alcance y convenciones.
- Pruebas de integración de backend (API, base de datos, motor de IA).
- Pruebas end-to-end (E2E) con Playwright sobre los flujos críticos del frontend.
- Pruebas unitarias y de componentes en el frontend React/TypeScript.
- Objetivos de cobertura de código: 90% en backend y 85% en frontend.
- Estrategia de datos de prueba (fixtures, datasets sintéticos, anonimización).
- Integración de la suite de tests en el pipeline de CI/CD.
