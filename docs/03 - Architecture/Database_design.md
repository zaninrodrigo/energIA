# DATABASE_DESIGN.md

# EnergIA - Diseño de Base de Datos

## Versión: 1.0.0

---

# 1. Introducción

Este documento define el diseño lógico y físico de la base de datos del sistema EnergIA.

La base de datos está diseñada bajo principios de:

- Normalización hasta 3FN
- Escalabilidad horizontal
- Auditoría completa
- Alta disponibilidad lógica
- Optimización para consultas analíticas
- Compatibilidad con pipelines de Machine Learning

---

# 2. Motor de Base de Datos

Se utilizará PostgreSQL como motor principal debido a:

- Soporte para JSONB (features de IA)
- Particionado de tablas
- Índices avanzados
- Escalabilidad
- Compatibilidad con analítica

---

# 3. Principios de Diseño

- Todo registro es auditable
- No se eliminan datos (Soft Delete)
- Uso de UUID como clave primaria
- Separación entre datos operativos y analíticos
- Trazabilidad completa del ciclo IA
- Optimización para lectura analítica

---

# 4. Convenciones Generales

## 4.1 Claves Primarias

- Todas las tablas usan `UUID`

---

## 4.2 Auditoría

Todas las tablas incluyen:

- created_at
- updated_at
- deleted_at
- created_by
- updated_by

---

## 4.3 Soft Delete

Ningún registro se elimina físicamente.

---

# 5. Modelo de Tablas

---

# 5.1 clientes

```sql
id UUID PRIMARY KEY
numero_cliente VARCHAR UNIQUE
nombre VARCHAR
documento VARCHAR
localidad VARCHAR
barrio VARCHAR
direccion JSONB
estado VARCHAR
created_at TIMESTAMP
updated_at TIMESTAMP
deleted_at TIMESTAMP
```

---

# 5.2 suministros

```sql
id UUID PRIMARY KEY
cliente_id UUID FK
numero_suministro VARCHAR UNIQUE
categoria_tarifaria VARCHAR
localidad VARCHAR
barrio VARCHAR
estado VARCHAR
fecha_alta DATE
created_at TIMESTAMP
updated_at TIMESTAMP
deleted_at TIMESTAMP
```

---

# 5.3 lecturas

```sql
id UUID PRIMARY KEY
suministro_id UUID FK
fecha_lectura DATE
lectura_anterior DECIMAL
lectura_actual DECIMAL
dias_facturados INT
created_at TIMESTAMP
```

---

# 5.4 consumos

```sql
id UUID PRIMARY KEY
suministro_id UUID FK
lote_id UUID FK
fecha_inicio DATE
fecha_fin DATE
kwh DECIMAL
consumo_promedio_diario DECIMAL
created_at TIMESTAMP
```

---

# 5.5 lotes

```sql
id UUID PRIMARY KEY
nombre VARCHAR
fecha_importacion TIMESTAMP
cantidad_registros INT
estado VARCHAR
created_at TIMESTAMP
```

---

# 5.6 resultados_ia

```sql
id UUID PRIMARY KEY
suministro_id UUID FK
lote_id UUID FK
modelo_ia_id UUID FK
score_anomalia DECIMAL
probabilidad DECIMAL
clasificacion VARCHAR
fecha_analisis TIMESTAMP
created_at TIMESTAMP
```

---

# 5.7 anomalías

```sql
id UUID PRIMARY KEY
resultado_ia_id UUID FK
tipo VARCHAR
severidad VARCHAR
descripcion TEXT
fecha_deteccion TIMESTAMP
created_at TIMESTAMP
```

---

# 5.8 ire

```sql
id UUID PRIMARY KEY
resultado_ia_id UUID FK
valor DECIMAL
nivel VARCHAR
fecha_calculo TIMESTAMP
created_at TIMESTAMP
```

---

# 5.9 impacto_economico

```sql
id UUID PRIMARY KEY
resultado_ia_id UUID FK
monto_estimado DECIMAL
moneda VARCHAR
fecha_calculo TIMESTAMP
created_at TIMESTAMP
```

---

# 5.10 ordenes_inspeccion

```sql
id UUID PRIMARY KEY
numero_orden VARCHAR UNIQUE
suministro_id UUID FK
resultado_ia_id UUID FK
prioridad VARCHAR
estado VARCHAR
fecha_generacion TIMESTAMP
fecha_programada DATE
created_at TIMESTAMP
```

---

# 5.11 inspecciones

```sql
id UUID PRIMARY KEY
orden_id UUID FK
inspector_id UUID
fecha_inicio TIMESTAMP
fecha_fin TIMESTAMP
resultado VARCHAR
observaciones TEXT
created_at TIMESTAMP
```

---

# 5.12 hallazgos

```sql
id UUID PRIMARY KEY
inspeccion_id UUID FK
tipo VARCHAR
descripcion TEXT
severidad VARCHAR
created_at TIMESTAMP
```

---

# 5.13 recuperos_economicos

```sql
id UUID PRIMARY KEY
inspeccion_id UUID FK
monto_recuperado DECIMAL
moneda VARCHAR
created_at TIMESTAMP
```

---

# 5.14 modelos_ia

```sql
id UUID PRIMARY KEY
nombre VARCHAR
version VARCHAR
algoritmo VARCHAR
precision DECIMAL
recall DECIMAL
f1_score DECIMAL
estado VARCHAR
fecha_entrenamiento TIMESTAMP
created_at TIMESTAMP
```

---

# 5.15 feedback_modelo

```sql
id UUID PRIMARY KEY
resultado_ia_id UUID FK
inspeccion_id UUID FK
prediccion_original VARCHAR
resultado_real VARCHAR
coincidencia BOOLEAN
created_at TIMESTAMP
```

---

# 6. Índices Estratégicos

## Consumos

```sql
CREATE INDEX idx_consumos_suministro_fecha
ON consumos (suministro_id, fecha_inicio);
```

---

## Resultados IA

```sql
CREATE INDEX idx_resultados_lote
ON resultados_ia (lote_id);
```

```sql
CREATE INDEX idx_resultados_suministro
ON resultados_ia (suministro_id);
```

---

## Ordenes

```sql
CREATE INDEX idx_ordenes_estado
ON ordenes_inspeccion (estado);
```

---

## Inspecciones

```sql
CREATE INDEX idx_inspecciones_fecha
ON inspecciones (fecha_inicio);
```

---

# 7. Particionado

## Tabla consumos

Particionado por fecha_inicio:

- consumos_2024
- consumos_2025
- consumos_2026

---

# 8. Estrategia de Performance

- Índices compuestos en consumo
- Materialized views para dashboard
- Agregaciones precalculadas para IA
- Cache de resultados IA recientes

---

# 9. Escalabilidad

El modelo está preparado para:

- +1.000.000 suministros
- +100M consumos históricos
- Procesamiento batch de lotes
- Consultas analíticas en tiempo real

---

# 10. Relación con el Dominio

Este modelo implementa directamente el DOMAIN_MODEL:

- Suministro → Consumo
- Consumo → IA
- IA → Anomalía
- Anomalía → Orden
- Orden → Inspección
- Inspección → Recupero
- Recupero → Feedback

---

# 11. Conclusión

La base de datos de EnergIA está diseñada no solo para almacenar información, sino para soportar un sistema de inteligencia operacional en tiempo real.

Permite trazabilidad completa del ciclo energético:

Consumo → Análisis → Decisión → Acción → Resultado → Aprendizaje

---

# FIN DATABASE_DESIGN.md