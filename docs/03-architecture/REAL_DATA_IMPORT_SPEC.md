# REAL_DATA_IMPORT_SPEC.md

# EnergIA

## Especificación de importación de datos reales

| Versión | Fecha | Estado | Autor |
|---|---|---|---|
| 0.1.0 | 2026-07-21 | Propuesto (pendiente de desarrollo) | Rodrigo Zanin |

Documenta el formato real de los datos de consumo entregados por la distribuidora, su mapeo al modelo de dominio, y dos correcciones/decisiones que surgen de conocer ese formato. Es la especificación para desarrollar el futuro adaptador de importación real. **Ninguna implementación se hace en este documento**: se acordó documentar ahora y desarrollar después.

> **Privacidad.** El archivo fuente contiene datos personales reales (titular, CUIT, coordenadas de domicilio). No se versiona (`/*.csv` está en `.gitignore`; el repositorio es público). Los ejemplos de este documento están enmascarados.

---

## 1. Decisiones tomadas (2026-07-21)

| # | Decisión | Detalle |
|---|---|---|
| D1 | **`numero_suministro` = ruta-folio** | El número de suministro (11 dígitos) ES el ruta-folio. Son sinónimos: el ruta-folio identifica el punto de suministro en la ruta de lectura. |
| D2 | **Agregar `medidor`** | El número de medidor es el serial del aparato físico instalado (longitud variable), un dato DISTINTO del ruta-folio: el aparato se reemplaza, el punto de suministro es fijo. Hoy no existe en el esquema. |
| D3 | **`rutafolio` (campo agregado el 2026-07-20) es redundante** | Duplica a `numero_suministro`. Debe reconciliarse: usar `numero_suministro` como el ruta-folio canónico y eliminar/degradar el campo `rutafolio` a un simple alias de presentación. |
| D4 | **Cada bimestre = un lote de procesamiento** | El consumo real es bimestral. Cada bimestre se trata como un lote (`lotes`), para que el Motor de Inteligencia Energética analice bimestre a bimestre, como haría en producción. `dias_facturados` ≈ 60. |

---

## 2. Formato del archivo fuente

- **Codificación:** ISO-8859-1 (latin-1). Nombres con acentos y ñ.
- **Separador:** `;` (punto y coma). **Fin de línea:** CRLF (Windows).
- **Dimensiones:** 1 fila de encabezado + N filas de datos (la primera entrega: 100 medidores de Formosa).
- **Períodos de consumo:** bimestrales, en **orden cronológico inverso** (la columna más a la izquierda es la más reciente). En la primera entrega: 22 períodos, `2023-B1` … `2026-B4`.

### 2.1 Columnas

| Columna | Descripción | Mapeo al esquema |
|---|---|---|
| *(sin encabezado)* | Índice/contador de la fila | Se ignora |
| `LD_DISTRITO` | Distrito (p. ej. "00 - FORMOSA") | Referencia; no se persiste en v1 |
| `SUMINISTRO` | Ruta-folio, 11 dígitos (D1) | `suministros.numero_suministro` |
| `MEDIDOR` | Serial del aparato físico (4-10 dígitos) | `suministros.medidor` (campo nuevo, D2) |
| `TITULAR` | Nombre del titular | `clientes.nombre` (limpiar espacios) |
| `CUIT` | CUIT del titular, 11 dígitos | `clientes.documento`. Un CUIT puede tener varios suministros |
| `LOCALIDAD_RUTA` | Localidad | `suministros.localidad` |
| `BARRIO_RUTA` | Barrio, con prefijo `"B  "` | `suministros.barrio` (quitar el prefijo `"B  "` y espacios) |
| `DMCLATITUD` | Latitud (decimal, punto) | `suministros.latitud` |
| `DMCLONGITUD` | Longitud (decimal, punto) | `suministros.longitud` |
| `C_P{AAAA}B{n}` | Consumo del bimestre `n` del año `AAAA`, en kWh | Un `consumos` por período (D4) |

### 2.2 Ejemplo de fila (enmascarado)

```
19;00 - FORMOSA;00201002902;334604;TITULAR EJEMPLO;20000000000;FORMOSA;B  San Martin;-26.1848;-58.1953;2700;2859;1625;1086; … (22 valores de consumo)
```

---

## 3. Mapeo a lotes y consumos (D4)

Cada columna `C_P{AAAA}B{n}` se convierte en un `consumos`:

- `lote_id`: el lote del bimestre `AAAA-B{n}` (`codigo_lote` sugerido: `REAL-{AAAA}-B{n}`). Se crea un `lotes` por bimestre presente en el archivo.
- `fecha_inicio` / `fecha_fin`: los límites del bimestre (B1 = ene-feb, B2 = mar-abr, … B6 = nov-dic). `dias_facturados` = días reales del bimestre (~59-62).
- `kwh`: el valor de la celda.
- `lectura_id`: `NULL` (el archivo no trae lecturas de medidor; la FK ya es nullable, RD-018).
- `consumo_promedio_diario`: se recalcula (kwh / dias_facturados), como en la Etapa 7-8.

Un valor `0` es consumo cero legítimo (33 de 2.200 celdas en la primera entrega), no un faltante.

---

## 4. Limpieza requerida

| Campo | Problema observado | Acción |
|---|---|---|
| `BARRIO_RUTA` | Prefijo `"B  "` (doble espacio); algunos con comillas/barras (`2 de Abril "D"`, `Covifol/ Terminal`) | Quitar prefijo y espacios; conservar el nombre tal cual |
| `TITULAR`, `BARRIO_RUTA` | Espacios sobrantes al final | `strip()` |
| *(primera columna)* | Sin encabezado | Ignorar |

---

## 5. Consideraciones para el Motor

El consumo real de Formosa es **fuertemente estacional** (el aire acondicionado en verano dispara el consumo): sobre la primera entrega, una heurística ingenua marca ~39% de los medidores con caídas ≥60% y ~19% con saltos ≥200% bimestre a bimestre — casi todos estacionales, no anómalos. Esto **confirma** por qué el motor debe comparar contra el **mismo bimestre del año anterior** (feature `pct_change_yoy`, F6) y contra la **cohorte** (percentiles, §7), no bimestre contra bimestre crudo. Con solo 22 períodos (3.5 años), la comparación interanual queda disponible para los bimestres de 2024 en adelante.

---

## 6. Pendiente de desarrollo

1. **Migración de esquema:** agregar `suministros.medidor` (varchar, nullable); reconciliar/eliminar `rutafolio` (D3).
2. **Adaptador de importación:** leer el CSV (latin-1, `;`), mapear según §2.1, crear clientes/suministros/lotes/consumos vía la API de importación (no escribir directo a la base), con la limpieza de §4.
3. **Generador sintético:** evaluar si se adapta a períodos bimestrales para seguir sirviendo de banco de pruebas coherente con el formato real.
4. Actualizar `docs/03-architecture/DATABASE_DESIGN.md` y `DOMAIN_MODEL.md` con `medidor` y la aclaración ruta-folio = número de suministro.
