# API Specification

| Versión | Fecha | Estado | Autor |
|---|---|---|---|
| 0.1.0 | 2026-07-03 | Pendiente | Rodrigo Zanin |

## Propósito

Este documento especificará la API REST del backend de EnergIA, construido con FastAPI, que expone las operaciones de los distintos contextos delimitados (bounded contexts) del dominio a los clientes frontend e integraciones externas. Definirá las convenciones de diseño, los mecanismos de autenticación y autorización, y el contrato formal de cada endpoint, de modo que sirva como referencia única entre el equipo de backend y el de frontend. Se mantendrá alineado con el modelo de dominio definido en DOMAIN_MODEL.md y con las reglas de negocio de BUSINESS_ANALYSIS.md.

## Contenido previsto

- Convenciones generales de la API (formato de URLs, versionado, paginación, filtros y ordenamiento).
- Formato estándar de request/response (JSON, envoltorios de éxito y error).
- Autenticación y autorización (JWT, roles, scopes) y su relación con SECURITY_SPEC.md.
- Endpoints por contexto delimitado: Suministros y Clientes, Facturación por Lotes, Motor de Inteligencia Energética, Inspecciones, Integración con RRHH.
- Modelos de datos (schemas Pydantic) de entrada y salida por endpoint.
- Catálogo de códigos de error y formato estándar de mensajes de error.
- Estrategia de versionado de la API y política de compatibilidad hacia atrás.
- Especificación OpenAPI/Swagger generada y su ubicación de publicación.
- Límites de uso (rate limiting) aplicables por endpoint o por rol.
