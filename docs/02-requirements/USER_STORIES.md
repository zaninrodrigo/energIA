# USER_STORIES.md

# EnergIA
## Product Backlog

**Versión:** 1.0.0

**Metodología:** Scrum

**Estado:** Draft

---

# Introducción

Este documento contiene el Product Backlog inicial del proyecto EnergIA.

Las historias de usuario describen las funcionalidades desde la perspectiva del usuario final y sirven como base para la planificación de los sprints, el desarrollo del software y la validación del producto.

Cada historia mantiene trazabilidad con:

- Requisitos Funcionales (RF)
- Casos de Uso (CU)
- Prioridad (MoSCoW)
- Épica correspondiente

---

# Convenciones

Formato:

Como <rol>

Quiero <objetivo>

Para <beneficio>

---

Prioridades

Must

Should

Could

Won't

---

# ÉPICA 1
## Integración de Datos

---

### US-001

**Como** Administrador

**Quiero** importar automáticamente los clientes desde Oracle

**Para** mantener la información actualizada.

RF relacionados

RF-001

Prioridad

Must

---

### US-002

Como Administrador

Quiero importar automáticamente los suministros

Para evitar cargas manuales.

RF

RF-001

Must

---

### US-003

Como Administrador

Quiero importar las lecturas históricas

Para disponer del histórico completo.

---

### US-004

Como Administrador

Quiero importar los consumos históricos

Para entrenar el modelo de IA.

---

### US-005

Como Administrador

Quiero importar nuevos lotes de facturación

Para procesar automáticamente cada período.

---

# ÉPICA 2
## Procesamiento

---

### US-006

Como Sistema

Quiero validar la integridad de los datos importados

Para evitar errores en el análisis.

---

### US-007

Como Sistema

Quiero detectar registros duplicados

Para mantener la calidad de los datos.

---

### US-008

Como Sistema

Quiero generar automáticamente las variables necesarias para IA

Para alimentar el modelo.

---

### US-009

Como Sistema

Quiero calcular indicadores estadísticos

Para enriquecer el análisis.

---

### US-010

Como Sistema

Quiero ejecutar automáticamente el Motor de Inteligencia Energética

Cuando finalice un lote.

---

# ÉPICA 3
## Motor de Inteligencia Energética

---

### US-011

Como Analista

Quiero que el sistema detecte consumos anómalos

Para identificar posibles casos de inspección.

---

### US-012

Como Analista

Quiero visualizar el Índice de Riesgo Energético

Para conocer la prioridad del caso.

---

### US-013

Como Analista

Quiero conocer por qué un suministro fue clasificado como anómalo

Para comprender el resultado.

---

### US-014

Como Analista

Quiero visualizar el consumo histórico

Para validar la anomalía.

---

### US-015

Como Analista

Quiero comparar el consumo con períodos anteriores

Para analizar tendencias.

---

### US-016

Como Analista

Quiero comparar el consumo con suministros similares

Para detectar comportamientos atípicos.

---

### US-017

Como Analista

Quiero conocer el impacto económico estimado

Para priorizar inspecciones.

---

### US-018

Como Analista

Quiero visualizar la evolución del IRE

Para analizar la persistencia de la anomalía.

---

# ÉPICA 4
## Dashboard Ejecutivo

---

### US-019

Como Gerente

Quiero visualizar un tablero ejecutivo

Para conocer el estado general.

---

### US-020

Como Gerente

Quiero conocer las localidades con mayor cantidad de anomalías

Para planificar acciones.

---

### US-021

Como Gerente

Quiero visualizar las anomalías por barrio

Para identificar zonas críticas.

---

### US-022

Como Gerente

Quiero visualizar indicadores de recuperación económica estimada

Para medir el impacto del sistema.

---

### US-023

Como Gerente

Quiero visualizar tendencias históricas

Para evaluar resultados.

---

# ÉPICA 5
## Gestión de Inspecciones

---

### US-024

Como Supervisor

Quiero visualizar un ranking de inspecciones

Para asignar prioridades.

---

### US-025

Como Supervisor

Quiero agrupar inspecciones por localidad

Para optimizar recorridos.

---

### US-026

Como Supervisor

Quiero agrupar inspecciones por barrio

Para reducir tiempos de traslado.

---

### US-027

Como Supervisor

Quiero generar órdenes de trabajo

Para enviarlas al sistema RRHH.

---

### US-028

Como Inspector

Quiero registrar el resultado de una inspección

Para actualizar el estado del suministro.

---

### US-029

Como Supervisor

Quiero visualizar inspecciones pendientes

Para gestionar el trabajo diario.

---

### US-030

Como Supervisor

Quiero visualizar inspecciones finalizadas

Para medir productividad.

---

# ÉPICA 6
## Seguridad

---

### US-031

Como Usuario

Quiero iniciar sesión

Para acceder al sistema.

---

### US-032

Como Administrador

Quiero administrar roles

Para controlar permisos.

---

### US-033

Como Administrador

Quiero auditar acciones

Para cumplir requisitos de seguridad.

---

# ÉPICA 7
## Reportes

---

### US-034

Como Gerente

Quiero exportar resultados a Excel

Para compartir información.

---

### US-035

Como Gerente

Quiero exportar reportes PDF

Para presentar indicadores.

---

# ÉPICA 8
## Integración RRHH

---

### US-036

Como Supervisor

Quiero crear automáticamente tareas en el sistema RRHH

Para asignarlas a inspectores.

---

### US-037

Como Supervisor

Quiero consultar el estado de las tareas

Para realizar seguimiento.

---

### US-038

Como Supervisor

Quiero sincronizar los resultados de inspección

Para mantener ambos sistemas actualizados.

---

# ÉPICA 9
## Administración

---

### US-039

Como Administrador

Quiero configurar parámetros del modelo

Para ajustar el comportamiento.

---

### US-040

Como Administrador

Quiero consultar logs del sistema

Para diagnosticar problemas.

---

# Roadmap

Sprint 1

US-001 al US-010

---

Sprint 2

US-011 al US-018

---

Sprint 3

US-019 al US-023

---

Sprint 4

US-024 al US-030

---

Sprint 5

US-031 al US-040

---

# Fin del Documento