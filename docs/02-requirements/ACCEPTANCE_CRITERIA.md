# ACCEPTANCE_CRITERIA.md

# EnergIA
## Acceptance Criteria

**Versión:** 1.0.0

**Estado:** Draft

**Autor:** Rodrigo Zanin

**Metodología:** BDD (Behavior Driven Development)

---

# Introducción

Este documento define los criterios de aceptación del proyecto EnergIA.

Los criterios de aceptación representan las condiciones que deben cumplirse para considerar una funcionalidad como terminada.

Cada criterio mantiene trazabilidad con:

- User Story
- Requisito Funcional
- Caso de Uso
- Casos de prueba

---

# Convenciones

Formato BDD

Given (Dado)

When (Cuando)

Then (Entonces)

---

# ÉPICA 1
## Integración de Datos

---

## AC-001

**Historia**

US-001

**Requisito**

RF-001

### Escenario

**Given**

Que Oracle contiene nuevos clientes.

**When**

El proceso de importación es ejecutado.

**Then**

Todos los clientes válidos deberán almacenarse en PostgreSQL.

---

## AC-002

Historia

US-002

RF-001

### Escenario

Given

Existen nuevos suministros.

When

Se ejecuta la importación.

Then

Todos los suministros deberán registrarse correctamente.

---

## AC-003

Historia

US-005

RF-002

### Escenario

Given

Existe un nuevo lote de facturación.

When

Finaliza la importación.

Then

El lote deberá quedar disponible para procesamiento.

---

# ÉPICA 2
## Procesamiento

---

## AC-004

Historia

US-006

RF-004

### Escenario

Given

Los datos fueron importados.

When

Comienza el procesamiento.

Then

El sistema deberá validar la integridad de la información.

---

## AC-005

Historia

US-008

RF-004

### Escenario

Given

Existe un lote válido.

When

Se ejecuta el Feature Engineering.

Then

Todas las variables requeridas deberán generarse correctamente.

---

## AC-006

Historia

US-010

RF-005

### Escenario

Given

Finalizó el procesamiento del lote.

When

El sistema detecta que existen nuevos consumos.

Then

El Motor IA deberá ejecutarse automáticamente.

---

# ÉPICA 3
## Motor IA

---

## AC-007

Historia

US-011

RF-006

### Escenario

Given

Existe un histórico de consumos.

When

Se ejecuta el modelo de IA.

Then

El sistema deberá detectar los consumos anómalos.

---

## AC-008

Historia

US-012

RF-007

### Escenario

Given

Se detectó una anomalía.

When

Finaliza el análisis.

Then

El sistema deberá calcular un IRE entre 0 y 100.

---

## AC-009

Historia

US-013

RF-013

### Escenario

Given

El usuario consulta una anomalía.

When

Visualiza el detalle.

Then

El sistema deberá mostrar una explicación del resultado.

---

## AC-010

Historia

US-017

RF-008

### Escenario

Given

Existe una anomalía detectada.

When

Se consulta el detalle.

Then

El sistema deberá calcular el Impacto Económico Estimado.

---

# ÉPICA 4
## Dashboard Ejecutivo

---

## AC-011

Historia

US-019

RF-016

### Escenario

Given

Existen anomalías procesadas.

When

El gerente accede al dashboard.

Then

El sistema deberá mostrar los indicadores ejecutivos.

---

## AC-012

Historia

US-020

RF-016

### Escenario

Given

Existen anomalías registradas.

When

Se consulta el mapa.

Then

Las anomalías deberán agruparse por localidad.

---

## AC-013

Historia

US-022

RF-016

### Escenario

Given

Existen cálculos de impacto económico.

When

Se consulta el dashboard.

Then

El sistema deberá mostrar el impacto económico estimado.

---

# ÉPICA 5
## Gestión de Inspecciones

---

## AC-014

Historia

US-024

RF-009

### Escenario

Given

Existen anomalías detectadas.

When

Se genera el ranking.

Then

Los suministros deberán ordenarse según el IRE.

---

## AC-015

Historia

US-027

RF-015

### Escenario

Given

Existe un ranking generado.

When

El supervisor selecciona casos.

Then

El sistema deberá crear órdenes de trabajo.

---

## AC-016

Historia

US-028

RF-014

### Escenario

Given

El inspector finaliza una inspección.

When

Registra el resultado.

Then

El estado deberá actualizarse correctamente.

---

# ÉPICA 6
## Seguridad

---

## AC-017

Historia

US-031

RF relacionado

RNF-009

### Escenario

Given

Un usuario válido.

When

Ingresa sus credenciales.

Then

El sistema deberá emitir un JWT válido.

---

## AC-018

Historia

US-032

RNF-010

### Escenario

Given

Un usuario sin permisos.

When

Intenta acceder a un recurso restringido.

Then

El acceso deberá denegarse.

---

# ÉPICA 7
## Reportes

---

## AC-019

Historia

US-034

RF-019

### Escenario

Given

Existe un ranking de inspecciones.

When

El usuario exporta a Excel.

Then

El archivo deberá contener toda la información visible.

---

## AC-020

Historia

US-035

RF-019

### Escenario

Given

Existe un dashboard.

When

El usuario exporta a PDF.

Then

El documento deberá generarse correctamente.

---

# ÉPICA 8
## Integración RRHH

---

## AC-021

Historia

US-036

RF-015

### Escenario

Given

Existe una orden de inspección.

When

El supervisor la confirma.

Then

La tarea deberá enviarse al sistema RRHH.

---

## AC-022

Historia

US-038

RF-015

### Escenario

Given

Una inspección fue finalizada.

When

RRHH informa el resultado.

Then

EnergIA deberá actualizar el estado automáticamente.

---

# Reglas Generales de Aceptación

Todas las funcionalidades deberán cumplir además con los siguientes criterios generales.

## Rendimiento

- El procesamiento de un lote deberá finalizar en menos de 10 minutos.

---

## Seguridad

- Todas las APIs deberán requerir autenticación.
- Los permisos deberán validarse mediante RBAC.
- Toda comunicación utilizará HTTPS.

---

## Calidad

- Cobertura Backend superior al 90%.
- Cobertura Frontend superior al 85%.
- Sin vulnerabilidades críticas OWASP.

---

## Auditoría

Todas las operaciones deberán registrarse indicando:

- Usuario
- Fecha
- Hora
- Acción
- Resultado

---

## Observabilidad

El sistema deberá registrar:

- Inicio del procesamiento.
- Finalización del procesamiento.
- Errores.
- Tiempo de ejecución.
- Versión del modelo IA utilizada.

---

# Matriz de Trazabilidad

| User Story | RF | Caso de Uso | Acceptance Criteria |
|------------|----|-------------|---------------------|
| US-001 | RF-001 | CU-001 | AC-001 |
| US-005 | RF-002 | CU-002 | AC-003 |
| US-010 | RF-005 | CU-003 | AC-006 |
| US-011 | RF-006 | CU-003 | AC-007 |
| US-012 | RF-007 | CU-004 | AC-008 |
| US-017 | RF-008 | CU-004 | AC-010 |
| US-024 | RF-009 | CU-006 | AC-014 |
| US-027 | RF-015 | CU-007 | AC-015 |
| US-028 | RF-014 | CU-008 | AC-016 |

---

# Definición de Done (Definition of Done)

Una funcionalidad se considerará terminada únicamente cuando:

- Todos los criterios de aceptación hayan sido cumplidos.
- Los tests unitarios pasen correctamente.
- Los tests de integración sean exitosos.
- Los tests E2E con Playwright sean satisfactorios.
- La documentación esté actualizada.
- Storybook incluya los componentes afectados (Frontend).
- La API esté documentada en OpenAPI.
- No existan vulnerabilidades críticas de seguridad.
- El código haya sido revisado y aprobado mediante Pull Request.
- La cobertura de pruebas cumpla los mínimos establecidos.

---

# Fin del Documento