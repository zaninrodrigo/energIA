-- =============================================================================
-- EnergIA — Índices de performance
-- =============================================================================
-- 01_schema.sql define las restricciones de integridad (PK, FK, CHECK, UNIQUE).
-- Este archivo agrega los índices que existen solo por motivos de performance:
-- columnas de FK que Postgres NO indexa automáticamente (a diferencia de PK y
-- UNIQUE, que sí generan índice), más los patrones de consulta que ya
-- identificaba el borrador original de DATABASE_DESIGN.md (§6).
--
-- Nota sobre "consumos" (tabla particionada): un CREATE INDEX sobre la tabla
-- particionada se propaga automáticamente a todas las particiones existentes
-- y futuras (comportamiento estándar desde PostgreSQL 11), así que no hace
-- falta repetirlo por partición.

-- -----------------------------------------------------------------------------
-- suministros
-- -----------------------------------------------------------------------------
CREATE INDEX idx_suministros_cliente ON suministros (cliente_id);
CREATE INDEX idx_suministros_categoria_tarifaria ON suministros (categoria_tarifaria_id);

-- -----------------------------------------------------------------------------
-- lecturas
-- -----------------------------------------------------------------------------
CREATE INDEX idx_lecturas_suministro ON lecturas (suministro_id);

-- -----------------------------------------------------------------------------
-- consumos — patrón de consulta ya identificado en el borrador original (§6):
-- "consumos por suministro + fecha".
-- -----------------------------------------------------------------------------
CREATE INDEX idx_consumos_suministro_fecha ON consumos (suministro_id, fecha_inicio);
CREATE INDEX idx_consumos_lote ON consumos (lote_id);
CREATE INDEX idx_consumos_lectura ON consumos (lectura_id);

-- -----------------------------------------------------------------------------
-- predicciones
-- -----------------------------------------------------------------------------
CREATE INDEX idx_predicciones_modelo_ia ON predicciones (modelo_ia_id);
CREATE INDEX idx_predicciones_suministro ON predicciones (suministro_id);
CREATE INDEX idx_predicciones_lote ON predicciones (lote_id);

-- -----------------------------------------------------------------------------
-- resultados_ia — patrones ya identificados en el borrador original (§6):
-- "resultados por lote" y "resultados por suministro".
-- -----------------------------------------------------------------------------
CREATE INDEX idx_resultados_ia_lote ON resultados_ia (lote_id);
CREATE INDEX idx_resultados_ia_suministro ON resultados_ia (suministro_id);
CREATE INDEX idx_resultados_ia_modelo_ia ON resultados_ia (modelo_ia_id);
CREATE INDEX idx_resultados_ia_prediccion ON resultados_ia (prediccion_id);

-- -----------------------------------------------------------------------------
-- feature_vectors
-- -----------------------------------------------------------------------------
CREATE INDEX idx_feature_vectors_suministro ON feature_vectors (suministro_id);
CREATE INDEX idx_feature_vectors_lote ON feature_vectors (lote_id);
CREATE INDEX idx_feature_vectors_resultado_ia ON feature_vectors (resultado_ia_id);

-- -----------------------------------------------------------------------------
-- anomalias / ire / impacto_economico
-- -----------------------------------------------------------------------------
CREATE INDEX idx_anomalias_resultado_ia ON anomalias (resultado_ia_id);
-- ire e impacto_economico ya tienen índice único 1:1 sobre resultado_ia_id
-- (uq_ire_resultado_ia / uq_impacto_economico_resultado_ia en 01_schema.sql),
-- que cubre también el acceso por FK: no hace falta un índice adicional.

-- -----------------------------------------------------------------------------
-- ordenes_inspeccion — patrón ya identificado en el borrador original (§6):
-- "órdenes por estado".
-- -----------------------------------------------------------------------------
CREATE INDEX idx_ordenes_inspeccion_estado ON ordenes_inspeccion (estado);
CREATE INDEX idx_ordenes_inspeccion_plan ON ordenes_inspeccion (plan_inspeccion_id);
-- suministro_id y resultado_ia_id ya están cubiertos por los índices únicos
-- parciales de 01_schema.sql para el primero, y se agrega explícito para el segundo:
CREATE INDEX idx_ordenes_inspeccion_resultado_ia ON ordenes_inspeccion (resultado_ia_id);

-- -----------------------------------------------------------------------------
-- asignaciones_inspector
-- -----------------------------------------------------------------------------
CREATE INDEX idx_asignaciones_inspector_orden ON asignaciones_inspector (orden_inspeccion_id);
CREATE INDEX idx_asignaciones_inspector_inspector ON asignaciones_inspector (inspector_id);

-- -----------------------------------------------------------------------------
-- inspecciones — patrón ya identificado en el borrador original (§6):
-- "inspecciones por fecha".
-- -----------------------------------------------------------------------------
CREATE INDEX idx_inspecciones_fecha ON inspecciones (fecha_inicio);
CREATE INDEX idx_inspecciones_orden ON inspecciones (orden_id);
CREATE INDEX idx_inspecciones_asignacion ON inspecciones (asignacion_id);

-- -----------------------------------------------------------------------------
-- hallazgos / recuperos_economicos
-- -----------------------------------------------------------------------------
CREATE INDEX idx_hallazgos_inspeccion ON hallazgos (inspeccion_id);
CREATE INDEX idx_recuperos_economicos_inspeccion ON recuperos_economicos (inspeccion_id);

-- -----------------------------------------------------------------------------
-- tareas_rrhh
-- -----------------------------------------------------------------------------
CREATE INDEX idx_tareas_rrhh_asignacion ON tareas_rrhh (asignacion_inspector_id);
CREATE INDEX idx_tareas_rrhh_orden ON tareas_rrhh (orden_inspeccion_id);

-- -----------------------------------------------------------------------------
-- feedback_modelo
-- -----------------------------------------------------------------------------
CREATE INDEX idx_feedback_modelo_resultado_ia ON feedback_modelo (resultado_ia_id);
CREATE INDEX idx_feedback_modelo_inspeccion ON feedback_modelo (inspeccion_id);

-- -----------------------------------------------------------------------------
-- datasets_etiquetados
-- -----------------------------------------------------------------------------
CREATE INDEX idx_datasets_etiquetados_suministro ON datasets_etiquetados (suministro_id);

-- -----------------------------------------------------------------------------
-- metricas_modelo / reentrenamientos_modelo
-- -----------------------------------------------------------------------------
CREATE INDEX idx_metricas_modelo_modelo_ia ON metricas_modelo (modelo_ia_id);
CREATE INDEX idx_reentrenamientos_modelo_anterior ON reentrenamientos_modelo (modelo_anterior_id);
CREATE INDEX idx_reentrenamientos_modelo_nuevo ON reentrenamientos_modelo (modelo_nuevo_id);
