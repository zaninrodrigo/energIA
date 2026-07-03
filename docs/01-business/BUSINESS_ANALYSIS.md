# BUSINESS_ANALYSIS.md

# EnergIA

## Análisis de Negocio

| Versión | Fecha | Estado | Autor |
|---|---|---|---|
| 0.2.0 | 2026-07-03 | Borrador | Rodrigo Zanin |

Este documento reúne el análisis de negocio de EnergIA: el contexto operativo de la distribuidora eléctrica, los procesos de facturación e inspección que la plataforma busca optimizar, las reglas de negocio, el glosario de términos, los KPIs de seguimiento y el valor diferencial del proyecto. Los antiguos anexos A a F quedan integrados en las secciones correspondientes de este documento.

---

## 1. Introducción

> Pendiente de redacción.

---

## 2. Objetivos del Documento

> Pendiente de redacción.

---

## 3. Contexto del Negocio

> Pendiente de redacción.

---

## 4. Situación Actual (AS-IS)

El siguiente diagrama describe el proceso actual, basado en análisis manual, previo a la incorporación de EnergIA:

```
Lectura de Medidor
        │
        ▼
Facturación por Lote
        │
        ▼
Obtención de Consumos
        │
        ▼
Análisis Manual
        │
        ▼
Selección de Casos
        │
        ▼
Asignación a Inspector
        │
        ▼
Inspección
        │
        ▼
Resultado
```

---

## 5. Situación Propuesta (TO-BE)

El siguiente diagrama describe el proceso propuesto, incorporando el Motor de Inteligencia Energética de EnergIA:

```
               Lectura de Medidor
                       │
                       ▼
             Facturación por Lote
                       │
                       ▼
             Importación Automática
                       │
                       ▼
         Motor de Inteligencia Energética
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
 Reglas de Negocio  Estadística   IA (Isolation Forest)
        │              │              │
        └──────────────┼──────────────┘
                       ▼
             Cálculo del IRE
                       │
                       ▼
      Estimación del Impacto Económico
                       │
                       ▼
      Ranking Inteligente de Inspecciones
                       │
                       ▼
      Agrupación por Barrio / Localidad
                       │
                       ▼
     Integración con Sistema de RRHH
                       │
                       ▼
           Creación de Orden de Trabajo
                       │
                       ▼
                Inspector
                       │
                       ▼
        Registro del Resultado
                       │
                       ▼
         Retroalimentación del Modelo
```

---

## 6. Stakeholders

> Pendiente de redacción.

---

## 7. Procesos del Negocio

> Pendiente de redacción.

---

## 8. Proceso de Facturación por Lotes

> Pendiente de redacción.

---

## 9. Proceso de Inspecciones

> Pendiente de redacción.

---

## 10. Problemas Detectados

> Pendiente de redacción.

---

## 11. Oportunidades de Mejora

> Pendiente de redacción.

---

## 12. Objetivos Estratégicos

> Pendiente de redacción.

---

## 13. Alcance

> Pendiente de redacción.

---

## 14. Fuera de Alcance

> Pendiente de redacción.

---

## 15. Reglas de Negocio

Las siguientes reglas expresan las restricciones y comportamientos que el negocio exige a la plataforma. Este catálogo es la fuente canónica única de reglas de negocio (RN-xxx) del proyecto: toda regla de negocio nueva debe incorporarse aquí, nunca redefinirse de forma independiente en otro documento. Los invariantes de nivel de entidad del modelo de dominio se numeran de forma independiente con el prefijo RD-xxx en `docs/03-architecture/DOMAIN_MODEL.md`.

#### RN-001

Todo suministro pertenece a un único cliente.

---

#### RN-002

Un cliente puede poseer múltiples suministros.

---

#### RN-003

Todo suministro pertenece a una única categoría tarifaria vigente.

---

#### RN-004

Los consumos son generados únicamente a partir de procesos de facturación por lotes.

---

#### RN-005

Cada nuevo consumo procesado deberá ser analizado automáticamente por el Motor de Inteligencia Energética.

---

#### RN-006

Toda anomalía detectada deberá recibir un Índice de Riesgo Energético (IRE).

---

#### RN-007

El IRE deberá expresarse en una escala de 0 a 100.

---

#### RN-008

La detección de una anomalía no constituye evidencia de fraude.

---

#### RN-009

Las inspecciones deberán priorizarse según el IRE y el Impacto Económico Estimado.

---

#### RN-010

Toda inspección deberá registrar un resultado.

---

#### RN-011

Los resultados de inspección podrán utilizarse para mejorar futuros modelos de Inteligencia Artificial.

---

#### RN-012

Todas las decisiones automáticas deberán ser explicables.

---

#### RN-013

Todo lote deberá procesarse completamente antes de ejecutar la IA.

---

## 16. Glosario

A continuación se listan los términos de negocio utilizados a lo largo de este documento y del resto de la documentación del proyecto:

### Suministro

Unidad de servicio eléctrico asociada a un cliente, identificada mediante un número único y utilizada como base para registrar lecturas, consumos, facturación e inspecciones.

---

### Cliente

Persona física o jurídica titular de uno o más suministros eléctricos.

---

### Lectura

Valor registrado por el medidor en una fecha determinada.

Las lecturas permiten calcular el consumo energético del período.

---

### Consumo

Cantidad de energía eléctrica utilizada durante un período de facturación, expresada en kWh.

---

### Lote de Facturación

Conjunto de suministros procesados en una misma ejecución del sistema de facturación.

Cada lote posee una fecha de procesamiento y comprende uno o más sectores geográficos.

---

### Categoría Tarifaria

Clasificación comercial utilizada para agrupar suministros según sus características de consumo.

Ejemplos:

- Residencial
- Comercial
- Industrial

---

### Anomalía

Comportamiento estadísticamente inusual detectado por el sistema respecto del consumo esperado de un suministro.

Una anomalía no implica necesariamente fraude.

---

### IRE

Índice de Riesgo Energético.

Indicador entre 0 y 100 que representa la probabilidad de que un suministro requiera una inspección.

---

### IEE

Impacto Económico Estimado.

Estimación del posible perjuicio económico asociado a una anomalía detectada.

---

### Orden de Inspección

Conjunto de tareas asignadas a una cuadrilla o inspector para verificar suministros priorizados por EnergIA.

---

### Resultado de Inspección

Conclusión obtenida luego de una inspección técnica.

Ejemplos:

- Sin anomalías.
- Error de lectura.
- Medidor defectuoso.
- Conexión irregular.
- Fraude confirmado.

---

## 17. KPIs

Los siguientes indicadores permiten medir el desempeño operativo, el rendimiento del motor de Inteligencia Artificial y el impacto de negocio de la plataforma:

### Operativos

- Suministros procesados
- Tiempo promedio de análisis por lote
- Cantidad de anomalías
- Tiempo promedio de generación del ranking

---

### IA

- Precisión del modelo
- Recall
- Falsos positivos
- Falsos negativos
- Cantidad de anomalías confirmadas

---

### Negocio

- Recuperación económica estimada
- Inspecciones realizadas
- Eficiencia de cuadrillas
- Tiempo promedio hasta inspección
- Cantidad de anomalías por localidad
- Cantidad de anomalías por barrio
- Cantidad de anomalías por categoría

---

## 18. Riesgos

> Pendiente de redacción.

---

## 19. Beneficios Esperados

El valor diferencial de EnergIA respecto de las herramientas tradicionales se resume a continuación. A diferencia de las herramientas tradicionales basadas únicamente en consultas o reglas de negocio, EnergIA propone un enfoque híbrido que combina:

- Inteligencia Artificial para detectar patrones anómalos.
- Análisis estadístico de históricos de consumo.
- Reglas de negocio específicas del dominio eléctrico.
- Explicabilidad de los resultados para facilitar su interpretación.
- Priorización inteligente de inspecciones.
- Integración con los sistemas corporativos existentes.

El objetivo no es reemplazar el criterio técnico de los analistas e inspectores, sino proporcionar una herramienta de apoyo a la toma de decisiones que permita optimizar recursos, reducir pérdidas no técnicas y acelerar la identificación de casos relevantes.

---

## 20. Casos de Uso del Negocio

> Pendiente de redacción.

---

## 21. Anexos

> Pendiente de redacción.
