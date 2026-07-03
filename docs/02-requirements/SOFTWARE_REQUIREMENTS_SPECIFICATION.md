# SOFTWARE_REQUIREMENTS_SPECIFICATION.md

# EnergIA
## Software Requirements Specification (SRS)

**Versión:** 1.0.0

**Estado:** Draft

**Autor:** Rodrigo Zanin

**Estándar:** IEEE 29148 Software Requirements Specification

---

# Historial de Versiones

| Versión | Fecha | Autor | Descripción |
|----------|-------|--------|-------------|
|1.0.0|2026-07-03|Rodrigo Zanin|Versión inicial|

---

# Índice

1. Introducción
2. Propósito
3. Alcance
4. Definiciones
5. Descripción General
6. Stakeholders
7. Suposiciones
8. Restricciones
9. Requisitos Funcionales
10. Requisitos No Funcionales
11. Reglas de Negocio
12. Interfaces
13. Casos de Uso
14. Modelo de Datos Conceptual
15. Criterios de Aceptación
16. Trazabilidad

---

# 1. Introducción

Este documento define los requisitos funcionales y no funcionales del sistema EnergIA.

Su propósito es servir como contrato entre los interesados del proyecto y el equipo de desarrollo, asegurando que todos los componentes del sistema respondan a las necesidades del negocio.

---

# 2. Propósito

EnergIA tiene como objetivo analizar automáticamente los consumos históricos de los suministros eléctricos para detectar comportamientos anómalos, calcular un Índice de Riesgo Energético (IRE) y generar un ranking inteligente de inspecciones.

La plataforma actuará como una herramienta de apoyo a la toma de decisiones y no reemplazará los sistemas comerciales existentes.

---

# 3. Alcance

El sistema deberá:

- Importar información desde Oracle.
- Procesar consumos por lotes de facturación.
- Detectar anomalías.
- Calcular el Índice de Riesgo Energético.
- Estimar el Impacto Económico Estimado (IEE).
- Priorizar inspecciones.
- Integrarse con el sistema corporativo de RRHH.
- Mostrar dashboards ejecutivos y operativos.

---

# 4. Definiciones

| Término | Definición |
|----------|------------|
|IRE|Índice de Riesgo Energético|
|IEE|Impacto Económico Estimado|
|Lote|Conjunto de suministros procesados en una ejecución de facturación|
|Anomalía|Comportamiento atípico detectado por el sistema|
|Suministro|Unidad de servicio eléctrico|

---

# 5. Descripción General

## Usuarios

- Gerente
- Supervisor
- Analista
- Inspector
- Administrador

---

## Sistemas Externos

- Oracle
- Sistema RRHH
- Active Directory (opcional)

---

## Arquitectura

Frontend React

↓

API FastAPI

↓

Motor IA

↓

PostgreSQL

↓

Oracle

---

# 6. Stakeholders

| Stakeholder | Interés |
|-------------|---------|
|Gerencia|Reducir pérdidas|
|Supervisores|Gestionar inspecciones|
|Analistas|Analizar anomalías|
|Inspectores|Ejecutar tareas|
|Área Sistemas|Mantener la plataforma|

---

# 7. Suposiciones

- Oracle contiene históricos confiables.
- Existen al menos dos años de consumos.
- El sistema RRHH dispone de API.
- Los usuarios cuentan con credenciales corporativas.

---

# 8. Restricciones

- Backend desarrollado en FastAPI.
- Frontend desarrollado en React.
- PostgreSQL como base principal.
- Docker obligatorio.
- Arquitectura Clean Architecture.
- OWASP Top 10.
- OpenAPI.
- Storybook.
- Playwright.

---

# 9. Requisitos Funcionales

## RF-001

El sistema deberá importar automáticamente los consumos históricos desde Oracle.

Prioridad: Must

---

## RF-002

El sistema deberá importar nuevos lotes de facturación.

Prioridad: Must

---

## RF-003

El sistema deberá almacenar el histórico completo de consumos.

Prioridad: Must

---

## RF-004

El sistema deberá generar variables (features) para el modelo de IA.

Prioridad: Must

---

## RF-005

El sistema deberá ejecutar el Motor de Inteligencia Energética al finalizar el procesamiento de un lote.

Prioridad: Must

---

## RF-006

El sistema deberá detectar consumos anómalos.

Prioridad: Must

---

## RF-007

El sistema deberá calcular un Índice de Riesgo Energético (IRE) entre 0 y 100.

Prioridad: Must

---

## RF-008

El sistema deberá estimar el Impacto Económico Estimado (IEE).

Prioridad: Should

---

## RF-009

El sistema deberá generar un ranking de inspecciones.

Prioridad: Must

---

## RF-010

El sistema deberá agrupar inspecciones por localidad.

Prioridad: Should

---

## RF-011

El sistema deberá agrupar inspecciones por barrio.

Prioridad: Should

---

## RF-012

El sistema deberá visualizar el historial completo de consumo de un suministro.

Prioridad: Must

---

## RF-013

El sistema deberá mostrar la explicación del IRE.

Prioridad: Must

---

## RF-014

El sistema deberá registrar el resultado de una inspección.

Prioridad: Must

---

## RF-015

El sistema deberá integrarse con el sistema de RRHH para generar órdenes de trabajo.

Prioridad: Must

---

## RF-016

El sistema deberá generar dashboards ejecutivos.

Prioridad: Must

---

## RF-017

El sistema deberá generar dashboards operativos.

Prioridad: Must

---

## RF-018

El sistema deberá permitir filtrar por:

- localidad
- barrio
- categoría
- lote
- estado
- IRE

---

## RF-019

El sistema deberá exportar resultados en formato Excel y PDF.

Prioridad: Could

---

## RF-020

El sistema deberá mantener auditoría de todas las operaciones.

Prioridad: Must

---

# 10. Requisitos No Funcionales

## RNF-001

Tiempo máximo de análisis de un lote:

< 10 minutos.

---

## RNF-002

Disponibilidad mínima:

99 %

---

## RNF-003

Todas las APIs deberán documentarse mediante OpenAPI.

---

## RNF-004

Todos los componentes React deberán documentarse mediante Storybook.

---

## RNF-005

Cobertura mínima:

Backend

90 %

---

## RNF-006

Cobertura Frontend

85 %

---

## RNF-007

El sistema deberá soportar más de 500.000 suministros.

---

## RNF-008

Todas las comunicaciones deberán realizarse mediante HTTPS.

---

## RNF-009

La autenticación deberá implementarse mediante JWT.

---

## RNF-010

El sistema deberá cumplir las recomendaciones OWASP Top 10.

---

# 11. Reglas de Negocio

RN-001

Todo suministro pertenece a un único cliente.

RN-002

Todo lote deberá procesarse completamente antes de ejecutar la IA.

RN-003

Toda anomalía deberá poseer un IRE.

RN-004

El IRE siempre estará entre 0 y 100.

RN-005

Una anomalía no implica fraude.

RN-006

Toda inspección deberá registrar un resultado.

RN-007

Los resultados podrán utilizarse para mejorar el modelo.

---

# 12. Interfaces

## Oracle

Importación de:

- Clientes
- Suministros
- Lecturas
- Consumos

---

## RRHH

API REST.

Operaciones:

- Crear tarea
- Consultar estado
- Actualizar tarea

---

# 13. Casos de Uso

CU-001

Importar lote.

CU-002

Procesar lote.

CU-003

Detectar anomalías.

CU-004

Calcular IRE.

CU-005

Visualizar anomalía.

CU-006

Generar ranking.

CU-007

Crear orden de inspección.

CU-008

Registrar resultado.

CU-009

Consultar dashboards.

---

# 14. Modelo Conceptual

Cliente

↓

Suministro

↓

Lecturas

↓

Consumos

↓

Anomalía

↓

IRE

↓

Orden de Inspección

↓

Resultado

---

# 15. Criterios de Aceptación

Ejemplo

RF-006

Dado un lote correctamente procesado

Cuando finaliza el Motor IA

Entonces deberán registrarse todas las anomalías detectadas junto con su IRE correspondiente.

---

# 16. Matriz de Trazabilidad

| Objetivo | RF | Caso de Uso |
|----------|-----|-------------|
|Detectar anomalías|RF-006|CU-003|
|Calcular IRE|RF-007|CU-004|
|Ranking|RF-009|CU-006|
|RRHH|RF-015|CU-007|

---

# Anexo A - Priorización MoSCoW

## Must

- Importación
- IA
- IRE
- Dashboard
- Integración RRHH

## Should

- IEE
- Agrupaciones
- Métricas

## Could

- Exportaciones
- Reportes avanzados
- Alertas automáticas

## Won't (v1)

- App móvil
- GIS
- Predicción de demanda
- Modelos supervisados

---

# Anexo B - Dependencias

- Oracle
- PostgreSQL
- FastAPI
- React
- Docker
- Scikit-Learn
- Playwright
- Storybook
- GitHub Actions

---

# Fin del Documento