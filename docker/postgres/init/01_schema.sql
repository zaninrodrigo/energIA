-- =============================================================================
-- EnergIA — Esquema de base de datos (fuente de verdad ejecutable)
-- =============================================================================
-- Este script es la fuente de verdad del esquema físico de EnergIA.
-- docs/03-architecture/DATABASE_DESIGN.md documenta las DECISIONES de diseño
-- (particionado, mapeo RD -> CHECK, inventario de tablas); este archivo es el DDL real.
--
-- Convenciones aplicadas a TODAS las tablas:
--   - PK: UUID generado con gen_random_uuid() (built-in desde PostgreSQL 13, sin extensión).
--   - Auditoría completa: created_at, updated_at, deleted_at, created_by, updated_by.
--     created_by/updated_by son UUID sin FK: la tabla de usuarios/roles todavía no existe
--     (ver PROJECT_MASTER_SPEC.md, deuda "matriz de roles y permisos diferida").
--   - Soft delete: deleted_at IS NULL representa un registro vigente; nunca se hace DELETE físico.
--   - Identificadores (tablas/columnas/constraints) en snake_case ASCII, sin acentos
--     (fix del debt "anomalías" -> "anomalias"). Los VALORES de datos (CHECK IN (...))
--     sí conservan la ortografía correcta del español cuando el dominio los usa así
--     (por ejemplo 'Crítico', 'Atención'): son datos, no identificadores, y PostgreSQL
--     en UTF8 no tiene ningún problema con eso.
--   - Cada CHECK lleva un comentario citando el RD-xxx de DOMAIN_MODEL.md que implementa,
--     o la sección (§) si el dominio define el enum de forma explícita pero sin numerar
--     una regla RD puntual.
--   - Regla de interpretación para los enums: si DOMAIN_MODEL.md titula la sección
--     "Estados" / "Tipos" / "Clasificaciones" / "Prioridades" / "Escala" / "Etiquetas"
--     con una lista cerrada, se traduce a CHECK IN (...). Si el dominio usa el título
--     "Ejemplos" (lista abierta, no exhaustiva) o no define ninguna lista, la columna
--     queda como VARCHAR sin CHECK — inventar valores no documentados sería una decisión
--     de diseño no pedida, y se deja explícitamente señalada como ambigüedad abierta.

-- -----------------------------------------------------------------------------
-- Función compartida: mantiene updated_at en cada UPDATE
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

-- =============================================================================
-- 1. categorias_tarifarias  (Entidad: CategoriaTarifaria, DOMAIN_MODEL.md §7.3)
-- =============================================================================
-- Antes era un VARCHAR libre en suministros.categoria_tarifaria. Se promueve a
-- tabla de catálogo porque §7.3 la describe como clasificación reutilizable y
-- comparable ("la IA solo compara suministros de categorías equivalentes",
-- RD-009): un catálogo con FK, no una enumeración cerrada, permite agregar
-- categorías nuevas sin migración. Por eso NO lleva CHECK sobre "nombre":
-- el título de la sección es "Ejemplos", lista abierta.
CREATE TABLE categorias_tarifarias (
    id              uuid        NOT NULL DEFAULT gen_random_uuid(),
    nombre          varchar(50) NOT NULL,
    descripcion     text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz,
    deleted_at      timestamptz,
    created_by      uuid,
    updated_by      uuid,
    CONSTRAINT pk_categorias_tarifarias PRIMARY KEY (id)
);

CREATE UNIQUE INDEX uq_categorias_tarifarias_nombre
    ON categorias_tarifarias (nombre) WHERE deleted_at IS NULL;

CREATE TRIGGER trg_categorias_tarifarias_set_updated_at
    BEFORE UPDATE ON categorias_tarifarias
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE categorias_tarifarias IS 'Entidad de dominio: CategoriaTarifaria (DOMAIN_MODEL.md §7.3).';

-- =============================================================================
-- 2. clientes  (Entidad: Cliente, DOMAIN_MODEL.md §7.1)
-- =============================================================================
CREATE TABLE clientes (
    id              uuid        NOT NULL DEFAULT gen_random_uuid(),
    numero_cliente  varchar(30) NOT NULL,
    nombre          varchar(150) NOT NULL,
    documento       varchar(20),
    localidad       varchar(100),
    barrio          varchar(100),
    direccion       jsonb,
    estado          varchar(15) NOT NULL DEFAULT 'Activo',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz,
    deleted_at      timestamptz,
    created_by      uuid,
    updated_by      uuid,
    CONSTRAINT pk_clientes PRIMARY KEY (id),
    -- §7.1: la columna Descripción de la tabla de Atributos dice explícitamente "Activo / Inactivo".
    CONSTRAINT ck_clientes_estado CHECK (estado IN ('Activo', 'Inactivo'))
);

CREATE UNIQUE INDEX uq_clientes_numero_cliente
    ON clientes (numero_cliente) WHERE deleted_at IS NULL;

CREATE TRIGGER trg_clientes_set_updated_at
    BEFORE UPDATE ON clientes
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE clientes IS 'Entidad de dominio: Cliente (DOMAIN_MODEL.md §7.1).';

-- =============================================================================
-- 3. suministros  (Entidad: Suministro, DOMAIN_MODEL.md §7.2)
-- =============================================================================
CREATE TABLE suministros (
    id                      uuid        NOT NULL DEFAULT gen_random_uuid(),
    numero_suministro       varchar(30) NOT NULL,
    cliente_id              uuid        NOT NULL,
    categoria_tarifaria_id  uuid        NOT NULL,
    localidad               varchar(100),
    barrio                  varchar(100),
    -- §7.2 NO enumera valores para este campo (a diferencia de Cliente.estado, que
    -- sí los explicita); se deja abierto en lugar de inventar un enum no documentado.
    estado                  varchar(15) NOT NULL DEFAULT 'Activo',
    fecha_alta              date        NOT NULL,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz,
    deleted_at              timestamptz,
    created_by              uuid,
    updated_by              uuid,
    CONSTRAINT pk_suministros PRIMARY KEY (id),
    CONSTRAINT fk_suministros_cliente
        FOREIGN KEY (cliente_id) REFERENCES clientes (id), -- RD-002/RD-006
    CONSTRAINT fk_suministros_categoria_tarifaria
        FOREIGN KEY (categoria_tarifaria_id) REFERENCES categorias_tarifarias (id) -- RD-005/RD-008
);

CREATE UNIQUE INDEX uq_suministros_numero_suministro
    ON suministros (numero_suministro) WHERE deleted_at IS NULL;

CREATE TRIGGER trg_suministros_set_updated_at
    BEFORE UPDATE ON suministros
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE suministros IS 'Entidad de dominio: Suministro (DOMAIN_MODEL.md §7.2). Aggregate Root del contexto de Suministros.';

-- =============================================================================
-- 4. lotes  (Entidad: Lote de Facturación, DOMAIN_MODEL.md §7.4)
-- =============================================================================
CREATE TABLE lotes (
    id                  uuid        NOT NULL DEFAULT gen_random_uuid(),
    codigo_lote         varchar(50) NOT NULL,
    nombre              varchar(150),
    fecha_importacion   timestamptz NOT NULL DEFAULT now(),
    cantidad_registros  integer     NOT NULL DEFAULT 0,
    estado              varchar(15) NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz,
    deleted_at          timestamptz,
    created_by          uuid,
    updated_by          uuid,
    CONSTRAINT pk_lotes PRIMARY KEY (id),
    CONSTRAINT ck_lotes_cantidad_registros_no_negativa CHECK (cantidad_registros >= 0),
    -- §7.4 "Estados": Pendiente, Procesando, Procesado, Error.
    CONSTRAINT ck_lotes_estado CHECK (estado IN ('Pendiente', 'Procesando', 'Procesado', 'Error'))
);

-- RD-010: "Un lote no puede ejecutarse dos veces" -> clave natural idempotente para
-- reimportaciones (codigo_lote es el identificador de negocio del archivo/corrida).
CREATE UNIQUE INDEX uq_lotes_codigo_lote
    ON lotes (codigo_lote) WHERE deleted_at IS NULL;

CREATE TRIGGER trg_lotes_set_updated_at
    BEFORE UPDATE ON lotes
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE lotes IS 'Entidad de dominio: Lote de Facturación (DOMAIN_MODEL.md §7.4).';

-- =============================================================================
-- 5. modelos_ia  (Entidad: Modelo IA, DOMAIN_MODEL.md §8.6 + Versionado del Modelo §10.5 fusionado)
-- =============================================================================
-- DECISIÓN: §8.6 (Modelo IA) y §10.5 (Versionado del Modelo) describen, en la
-- práctica, la misma granularidad de dato: una fila = una versión publicada del
-- motor ("Representa cada versión publicada del Motor de IA"). Se fusionan en
-- una sola tabla en vez de crear "versionado_modelo" como tabla separada 1:1,
-- para no arrastrar una FK 1:1 redundante. El estado usa el enum de §10.5
-- (Activo/Obsoleto/Experimental/Retirado), más rico que el "estado VARCHAR"
-- genérico del borrador original. Las métricas (precision/recall/f1/rocAuc/
-- accuracy) se movieron a metricas_modelo (ver tabla siguiente) porque §10.4
-- las define como una entidad propia, separada de identidad/versionado.
CREATE TABLE modelos_ia (
    id                    uuid        NOT NULL DEFAULT gen_random_uuid(),
    nombre                varchar(100) NOT NULL,
    version               varchar(30) NOT NULL,
    -- §8.6 "Algoritmos soportados".
    algoritmo             varchar(30) NOT NULL,
    -- §10.5 "Estados".
    estado                varchar(15) NOT NULL,
    fecha_entrenamiento   timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz,
    deleted_at            timestamptz,
    created_by            uuid,
    updated_by            uuid,
    CONSTRAINT pk_modelos_ia PRIMARY KEY (id),
    CONSTRAINT ck_modelos_ia_algoritmo
        CHECK (algoritmo IN ('Isolation Forest', 'Local Outlier Factor', 'One Class SVM', 'Autoencoder')),
    CONSTRAINT ck_modelos_ia_estado
        CHECK (estado IN ('Activo', 'Obsoleto', 'Experimental', 'Retirado')),
    -- RD-048: "Toda versión debe conservarse" -> nombre+version identifica la versión de forma única.
    CONSTRAINT uq_modelos_ia_nombre_version UNIQUE (nombre, version)
);

CREATE TRIGGER trg_modelos_ia_set_updated_at
    BEFORE UPDATE ON modelos_ia
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE modelos_ia IS 'Entidades de dominio: Modelo IA (DOMAIN_MODEL.md §8.6) + Versionado del Modelo (§10.5, fusionada aquí). Aggregate Root del contexto de Aprendizaje Continuo.';

-- =============================================================================
-- 6. metricas_modelo  (Entidad: Métricas del Modelo, DOMAIN_MODEL.md §10.4)
-- =============================================================================
-- Tabla separada de modelos_ia (no columnas sueltas) porque un mismo modelo_ia
-- puede evaluarse más de una vez (validación durante reentrenamiento, reevaluación
-- periódica sobre nuevos datos etiquetados): guardar histórico de evaluaciones,
-- no solo la última, es lo que permite comparar versiones (§10.4 "Permite comparar
-- versiones y seleccionar la más adecuada").
CREATE TABLE metricas_modelo (
    id              uuid        NOT NULL DEFAULT gen_random_uuid(),
    modelo_ia_id    uuid        NOT NULL,
    precision       numeric(5,4),
    recall          numeric(5,4),
    f1_score        numeric(5,4),
    roc_auc         numeric(5,4),
    accuracy        numeric(5,4),
    fecha_calculo   timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz,
    deleted_at      timestamptz,
    created_by      uuid,
    updated_by      uuid,
    CONSTRAINT pk_metricas_modelo PRIMARY KEY (id),
    CONSTRAINT fk_metricas_modelo_modelo_ia
        FOREIGN KEY (modelo_ia_id) REFERENCES modelos_ia (id),
    -- Cota matemática estándar de métricas de clasificación (no hay RD específica,
    -- es una propiedad objetiva de precision/recall/F1/ROC-AUC/accuracy).
    CONSTRAINT ck_metricas_modelo_precision CHECK (precision BETWEEN 0 AND 1),
    CONSTRAINT ck_metricas_modelo_recall CHECK (recall BETWEEN 0 AND 1),
    CONSTRAINT ck_metricas_modelo_f1_score CHECK (f1_score BETWEEN 0 AND 1),
    CONSTRAINT ck_metricas_modelo_roc_auc CHECK (roc_auc BETWEEN 0 AND 1),
    CONSTRAINT ck_metricas_modelo_accuracy CHECK (accuracy BETWEEN 0 AND 1)
);

CREATE TRIGGER trg_metricas_modelo_set_updated_at
    BEFORE UPDATE ON metricas_modelo
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE metricas_modelo IS 'Entidad de dominio: Métricas del Modelo (DOMAIN_MODEL.md §10.4).';

-- =============================================================================
-- 7. reentrenamientos_modelo  (Entidad: Reentrenamiento del Modelo, DOMAIN_MODEL.md §10.3)
-- =============================================================================
CREATE TABLE reentrenamientos_modelo (
    id                  uuid        NOT NULL DEFAULT gen_random_uuid(),
    modelo_anterior_id  uuid,
    modelo_nuevo_id     uuid        NOT NULL,
    fecha_inicio        timestamptz NOT NULL DEFAULT now(),
    fecha_fin           timestamptz,
    estado              varchar(15) NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz,
    deleted_at          timestamptz,
    created_by          uuid,
    updated_by          uuid,
    CONSTRAINT pk_reentrenamientos_modelo PRIMARY KEY (id),
    CONSTRAINT fk_reentrenamientos_modelo_anterior
        FOREIGN KEY (modelo_anterior_id) REFERENCES modelos_ia (id),
    CONSTRAINT fk_reentrenamientos_modelo_nuevo
        FOREIGN KEY (modelo_nuevo_id) REFERENCES modelos_ia (id),
    -- §10.3 "Estados".
    CONSTRAINT ck_reentrenamientos_modelo_estado
        CHECK (estado IN ('Pendiente', 'Entrenando', 'Validando', 'Publicado', 'Cancelado', 'Error'))
);

CREATE TRIGGER trg_reentrenamientos_modelo_set_updated_at
    BEFORE UPDATE ON reentrenamientos_modelo
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE reentrenamientos_modelo IS 'Entidad de dominio: Reentrenamiento del Modelo (DOMAIN_MODEL.md §10.3).';

-- =============================================================================
-- 8. lecturas  (Entidad: Lectura, DOMAIN_MODEL.md §7.5)
-- =============================================================================
CREATE TABLE lecturas (
    id                  uuid        NOT NULL DEFAULT gen_random_uuid(),
    suministro_id       uuid        NOT NULL,
    fecha_lectura       date        NOT NULL,
    lectura_anterior    numeric(12,3) NOT NULL,
    lectura_actual      numeric(12,3) NOT NULL,
    dias_facturados     integer     NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz,
    deleted_at          timestamptz,
    created_by          uuid,
    updated_by          uuid,
    CONSTRAINT pk_lecturas PRIMARY KEY (id),
    CONSTRAINT fk_lecturas_suministro
        FOREIGN KEY (suministro_id) REFERENCES suministros (id), -- RD-015
    -- RD-013: "La lectura actual debe ser mayor o igual que la anterior".
    CONSTRAINT ck_lecturas_actual_mayor_igual_anterior CHECK (lectura_actual >= lectura_anterior),
    -- RD-014: "Los días facturados deben ser mayores que cero".
    CONSTRAINT ck_lecturas_dias_facturados_positivo CHECK (dias_facturados > 0)
);

-- Clave natural compuesta: una lectura por suministro y fecha (RD-015, "una lectura pertenece a
-- un único suministro"; §7.5 no admite dos lecturas del mismo suministro en la misma fecha).
-- Implementa la idempotencia de importación (US-003): sin este índice, reimportar el mismo
-- histórico duplicaría filas en lugar de actualizar/no-hacer-nada, como sí ocurre en
-- clientes/suministros vía uq_clientes_numero_cliente/uq_suministros_numero_suministro.
CREATE UNIQUE INDEX uq_lecturas_suministro_fecha
    ON lecturas (suministro_id, fecha_lectura) WHERE deleted_at IS NULL;

CREATE TRIGGER trg_lecturas_set_updated_at
    BEFORE UPDATE ON lecturas
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE lecturas IS 'Entidad de dominio: Lectura (DOMAIN_MODEL.md §7.5).';

-- =============================================================================
-- 9. consumos  (Entidad: Consumo, DOMAIN_MODEL.md §7.6) — PARTICIONADA POR RANGE
-- =============================================================================
-- Particionado por fecha_inicio (RNF-007: >500.000 suministros, ADR-004). Años
-- de datos históricos desconocidos hasta recibir el archivo a migrar -> se crean
-- particiones 2022-2026 + una partición DEFAULT que absorbe cualquier fecha fuera
-- de ese rango sin romper la carga (se resegmentará cuando se conozca el rango real).
--
-- TRADE-OFF DE CLAVE COMPUESTA (documentado en detalle en DATABASE_DESIGN.md):
-- PostgreSQL exige que todo índice único de una tabla particionada incluya la
-- columna de particionado. Por eso la PK no puede ser solo "id": es (id, fecha_inicio).
-- Hoy ninguna otra tabla referencia consumos.id via FK (resultados_ia y
-- feature_vectors enlazan por suministro_id + lote_id, no por consumo puntual),
-- así que esta clave compuesta no se propaga a ningún otro lado del grafo todavía.
-- Si en el futuro una tabla necesitara referenciar una fila puntual de consumos,
-- esa FK tendría que ser compuesta (consumo_id, fecha_inicio) por la misma regla.
CREATE TABLE consumos (
    id                          uuid        NOT NULL DEFAULT gen_random_uuid(),
    suministro_id               uuid        NOT NULL,
    lote_id                     uuid        NOT NULL,
    lectura_id                  uuid,
    fecha_inicio                date        NOT NULL,
    fecha_fin                   date        NOT NULL,
    dias_facturados             integer     NOT NULL,
    kwh                         numeric(12,3) NOT NULL,
    consumo_promedio_diario     numeric(12,3),
    created_at                  timestamptz NOT NULL DEFAULT now(),
    updated_at                  timestamptz,
    deleted_at                  timestamptz,
    created_by                  uuid,
    updated_by                  uuid,
    CONSTRAINT pk_consumos PRIMARY KEY (id, fecha_inicio),
    CONSTRAINT fk_consumos_suministro
        FOREIGN KEY (suministro_id) REFERENCES suministros (id), -- RD-004
    CONSTRAINT fk_consumos_lote
        FOREIGN KEY (lote_id) REFERENCES lotes (id), -- RD-012/RD-019
    -- RD-018: "Debe existir una lectura asociada". El dominio no lista lecturaId
    -- como atributo de Consumo (§7.6); se agrega esta FK para poder representar
    -- el invariante. Es NULL-able porque los archivos históricos a recibir pueden
    -- no traer el detalle de lectura por período (ver docker/postgres/init/03_staging.sql).
    CONSTRAINT fk_consumos_lectura
        FOREIGN KEY (lectura_id) REFERENCES lecturas (id),
    -- RD-016: "El consumo debe ser mayor o igual a cero".
    CONSTRAINT ck_consumos_kwh_no_negativo CHECK (kwh >= 0),
    -- Análogo a RD-014 (Lectura), aplicado al período de Consumo.
    CONSTRAINT ck_consumos_dias_facturados_positivo CHECK (dias_facturados > 0),
    -- Integridad temporal del período; no tiene un RD-xxx propio asociado.
    CONSTRAINT ck_consumos_periodo_valido CHECK (fecha_fin >= fecha_inicio)
) PARTITION BY RANGE (fecha_inicio);

CREATE TABLE consumos_2022 PARTITION OF consumos
    FOR VALUES FROM ('2022-01-01') TO ('2023-01-01');
CREATE TABLE consumos_2023 PARTITION OF consumos
    FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');
CREATE TABLE consumos_2024 PARTITION OF consumos
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE consumos_2025 PARTITION OF consumos
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE consumos_2026 PARTITION OF consumos
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
-- Partición de resguardo: cualquier fecha_inicio fuera de 2022-2026 (datos más
-- viejos que 2022, o de años futuros) cae acá en vez de rechazar el INSERT.
CREATE TABLE consumos_default PARTITION OF consumos DEFAULT;

-- Clave natural para cargas históricas idempotentes (RD-017: períodos no
-- deberían superponerse). Índice único PARCIAL (WHERE deleted_at IS NULL), no
-- una CONSTRAINT ... UNIQUE simple -- deuda #10 (PROJECT_MASTER_SPEC.md), pagada
-- antes de US-004: sin la cláusula parcial, reimportar el mismo
-- (suministro_id, fecha_inicio, fecha_fin) tras un soft-delete chocaba contra
-- la fila soft-deleted en lugar de crear una nueva, rompiendo la idempotencia
-- de importación que sí tienen clientes/suministros/lecturas
-- (uq_clientes_numero_cliente, uq_suministros_numero_suministro,
-- uq_lecturas_suministro_fecha son los tres precedentes, todos parciales).
-- `fecha_inicio` (la columna de particionado) va incluida en el índice porque
-- PostgreSQL lo exige para todo índice único de una tabla particionada.
-- Nota: este índice evita duplicar EXACTAMENTE el mismo período, pero no
-- impide solapamientos parciales entre períodos distintos para el mismo
-- suministro -- eso requeriría un EXCLUDE constraint con btree_gist, que no se
-- agrega hoy para no introducir una extensión no pedida por la misión (ver
-- DATABASE_DESIGN.md §6.4).
CREATE UNIQUE INDEX uq_consumos_suministro_periodo
    ON consumos (suministro_id, fecha_inicio, fecha_fin) WHERE deleted_at IS NULL;

-- Los triggers de fila definidos sobre la tabla particionada se clonan
-- automáticamente a cada partición (comportamiento estándar de PostgreSQL
-- 11+); no hace falta repetir el CREATE TRIGGER por partición.
CREATE TRIGGER trg_consumos_set_updated_at
    BEFORE UPDATE ON consumos
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE consumos IS 'Entidad de dominio: Consumo (DOMAIN_MODEL.md §7.6). Particionada por RANGE (fecha_inicio).';

-- =============================================================================
-- 10. predicciones  (Entidad: Predicción, DOMAIN_MODEL.md §8.7)
-- =============================================================================
CREATE TABLE predicciones (
    id                  uuid        NOT NULL DEFAULT gen_random_uuid(),
    modelo_ia_id        uuid        NOT NULL,
    suministro_id       uuid        NOT NULL,
    lote_id             uuid        NOT NULL,
    fecha_prediccion    timestamptz NOT NULL DEFAULT now(),
    score               numeric(8,4),
    clasificacion       varchar(20) NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz,
    deleted_at          timestamptz,
    created_by          uuid,
    updated_by          uuid,
    CONSTRAINT pk_predicciones PRIMARY KEY (id),
    CONSTRAINT fk_predicciones_modelo_ia
        FOREIGN KEY (modelo_ia_id) REFERENCES modelos_ia (id),
    CONSTRAINT fk_predicciones_suministro
        FOREIGN KEY (suministro_id) REFERENCES suministros (id),
    CONSTRAINT fk_predicciones_lote
        FOREIGN KEY (lote_id) REFERENCES lotes (id),
    -- Reutiliza las Clasificaciones de ResultadoIA (§8.1): misma escala conceptual.
    CONSTRAINT ck_predicciones_clasificacion
        CHECK (clasificacion IN ('Normal', 'Atención', 'Alto Riesgo', 'Crítico'))
);

CREATE TRIGGER trg_predicciones_set_updated_at
    BEFORE UPDATE ON predicciones
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE predicciones IS 'Entidad de dominio: Predicción (DOMAIN_MODEL.md §8.7).';

-- =============================================================================
-- 11. resultados_ia  (Entidad: ResultadoIA, DOMAIN_MODEL.md §8.1)
-- =============================================================================
CREATE TABLE resultados_ia (
    id                  uuid        NOT NULL DEFAULT gen_random_uuid(),
    suministro_id       uuid        NOT NULL,
    lote_id             uuid        NOT NULL,
    modelo_ia_id        uuid        NOT NULL,
    prediccion_id       uuid,
    -- Score crudo de Isolation Forest (decision_function): puede ser negativo,
    -- por eso NO lleva CHECK >= 0 (a diferencia de "probabilidad").
    score_anomalia      numeric(8,4),
    probabilidad        numeric(5,4),
    clasificacion       varchar(20) NOT NULL,
    observaciones       text,
    fecha_analisis      timestamptz NOT NULL DEFAULT now(),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz,
    deleted_at          timestamptz,
    created_by          uuid,
    updated_by          uuid,
    CONSTRAINT pk_resultados_ia PRIMARY KEY (id),
    CONSTRAINT fk_resultados_ia_suministro
        FOREIGN KEY (suministro_id) REFERENCES suministros (id), -- RD-020
    CONSTRAINT fk_resultados_ia_lote
        FOREIGN KEY (lote_id) REFERENCES lotes (id), -- RD-021
    CONSTRAINT fk_resultados_ia_modelo_ia
        FOREIGN KEY (modelo_ia_id) REFERENCES modelos_ia (id), -- RD-022
    CONSTRAINT fk_resultados_ia_prediccion
        FOREIGN KEY (prediccion_id) REFERENCES predicciones (id),
    -- §8.1 "Clasificaciones".
    CONSTRAINT ck_resultados_ia_clasificacion
        CHECK (clasificacion IN ('Normal', 'Atención', 'Alto Riesgo', 'Crítico')),
    CONSTRAINT ck_resultados_ia_probabilidad CHECK (probabilidad BETWEEN 0 AND 1),
    -- RD-023: "No puede existir más de un ResultadoIA por suministro y lote".
    CONSTRAINT uq_resultados_ia_suministro_lote UNIQUE (suministro_id, lote_id)
);

CREATE TRIGGER trg_resultados_ia_set_updated_at
    BEFORE UPDATE ON resultados_ia
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE resultados_ia IS 'Entidad de dominio: ResultadoIA (DOMAIN_MODEL.md §8.1). Aggregate Root del contexto de Inteligencia Artificial.';

-- =============================================================================
-- 12. feature_vectors  (Entidad: Feature Vector, DOMAIN_MODEL.md §8.5)
-- =============================================================================
CREATE TABLE feature_vectors (
    id                  uuid        NOT NULL DEFAULT gen_random_uuid(),
    suministro_id       uuid        NOT NULL,
    lote_id             uuid        NOT NULL,
    resultado_ia_id     uuid,
    version             varchar(20) NOT NULL,
    features            jsonb       NOT NULL,
    fecha_generacion    timestamptz NOT NULL DEFAULT now(),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz,
    deleted_at          timestamptz,
    created_by          uuid,
    updated_by          uuid,
    CONSTRAINT pk_feature_vectors PRIMARY KEY (id),
    CONSTRAINT fk_feature_vectors_suministro
        FOREIGN KEY (suministro_id) REFERENCES suministros (id),
    CONSTRAINT fk_feature_vectors_lote
        FOREIGN KEY (lote_id) REFERENCES lotes (id),
    CONSTRAINT fk_feature_vectors_resultado_ia
        FOREIGN KEY (resultado_ia_id) REFERENCES resultados_ia (id),
    CONSTRAINT uq_feature_vectors_suministro_lote_version UNIQUE (suministro_id, lote_id, version)
);

CREATE TRIGGER trg_feature_vectors_set_updated_at
    BEFORE UPDATE ON feature_vectors
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE feature_vectors IS 'Entidad de dominio: Feature Vector (DOMAIN_MODEL.md §8.5).';

-- =============================================================================
-- 13. anomalias  (Entidad: Anomalía, DOMAIN_MODEL.md §8.2)
-- =============================================================================
-- Fix del debt documental: la tabla del borrador se llamaba "anomalías" (con
-- tilde), un identificador no-ASCII que complica drivers/CLIs/ORMs. Se renombra
-- a "anomalias". El valor de dato en los CHECK sí conserva tilde donde corresponde.
CREATE TABLE anomalias (
    id                  uuid        NOT NULL DEFAULT gen_random_uuid(),
    resultado_ia_id     uuid        NOT NULL,
    tipo                varchar(30) NOT NULL,
    severidad           varchar(10) NOT NULL,
    descripcion         text,
    fecha_deteccion     timestamptz NOT NULL DEFAULT now(),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz,
    deleted_at          timestamptz,
    created_by          uuid,
    updated_by          uuid,
    CONSTRAINT pk_anomalias PRIMARY KEY (id),
    CONSTRAINT fk_anomalias_resultado_ia
        FOREIGN KEY (resultado_ia_id) REFERENCES resultados_ia (id), -- RD-024
    -- §8.2 "Tipos" (lista cerrada).
    CONSTRAINT ck_anomalias_tipo CHECK (tipo IN (
        'Consumo Muy Bajo', 'Consumo Muy Alto', 'Caída Brusca', 'Incremento Brusco',
        'Patrón Irregular', 'Persistencia Anómala', 'Desvío Estadístico'
    )),
    -- §8.2 "Severidad" (lista cerrada).
    CONSTRAINT ck_anomalias_severidad CHECK (severidad IN ('Baja', 'Media', 'Alta', 'Crítica'))
);

CREATE TRIGGER trg_anomalias_set_updated_at
    BEFORE UPDATE ON anomalias
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE anomalias IS 'Entidad de dominio: Anomalía (DOMAIN_MODEL.md §8.2). Nombre de tabla sin tilde (fix de deuda documental).';

-- =============================================================================
-- 14. ire  (Entidad: Índice de Riesgo Energético (IRE), DOMAIN_MODEL.md §8.3)
-- =============================================================================
CREATE TABLE ire (
    id                  uuid        NOT NULL DEFAULT gen_random_uuid(),
    resultado_ia_id     uuid        NOT NULL,
    valor               numeric(5,2) NOT NULL,
    -- Columna generada a partir de la "Escala" de §8.3 (0-20 Muy Bajo ... 81-100 Crítico).
    nivel               varchar(20) GENERATED ALWAYS AS (
        CASE
            WHEN valor <= 20 THEN 'Muy Bajo'
            WHEN valor <= 40 THEN 'Bajo'
            WHEN valor <= 60 THEN 'Medio'
            WHEN valor <= 80 THEN 'Alto'
            ELSE 'Crítico'
        END
    ) STORED,
    fecha_calculo       timestamptz NOT NULL DEFAULT now(),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz,
    deleted_at          timestamptz,
    created_by          uuid,
    updated_by          uuid,
    CONSTRAINT pk_ire PRIMARY KEY (id),
    -- Un ResultadoIA calcula un único IRE.
    CONSTRAINT fk_ire_resultado_ia
        FOREIGN KEY (resultado_ia_id) REFERENCES resultados_ia (id),
    CONSTRAINT uq_ire_resultado_ia UNIQUE (resultado_ia_id),
    -- Invariante global (DOMAIN_MODEL.md §14): "El IRE siempre debe estar entre 0 y 100".
    CONSTRAINT ck_ire_valor_rango CHECK (valor >= 0 AND valor <= 100)
);

CREATE TRIGGER trg_ire_set_updated_at
    BEFORE UPDATE ON ire
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE ire IS 'Entidad de dominio: Índice de Riesgo Energético / IRE (DOMAIN_MODEL.md §8.3).';

-- =============================================================================
-- 15. impacto_economico  (Entidad: Impacto Económico Estimado (IEE), DOMAIN_MODEL.md §8.4)
-- =============================================================================
CREATE TABLE impacto_economico (
    id                  uuid        NOT NULL DEFAULT gen_random_uuid(),
    resultado_ia_id     uuid        NOT NULL,
    monto_estimado      numeric(14,2) NOT NULL,
    moneda              varchar(3)  NOT NULL DEFAULT 'ARS',
    fecha_calculo       timestamptz NOT NULL DEFAULT now(),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz,
    deleted_at          timestamptz,
    created_by          uuid,
    updated_by          uuid,
    CONSTRAINT pk_impacto_economico PRIMARY KEY (id),
    CONSTRAINT fk_impacto_economico_resultado_ia
        FOREIGN KEY (resultado_ia_id) REFERENCES resultados_ia (id),
    CONSTRAINT uq_impacto_economico_resultado_ia UNIQUE (resultado_ia_id),
    -- RD-027: "El monto nunca puede ser negativo".
    CONSTRAINT ck_impacto_economico_monto_no_negativo CHECK (monto_estimado >= 0)
);

CREATE TRIGGER trg_impacto_economico_set_updated_at
    BEFORE UPDATE ON impacto_economico
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE impacto_economico IS 'Entidad de dominio: Impacto Económico Estimado / IEE (DOMAIN_MODEL.md §8.4).';

-- =============================================================================
-- 16. planes_inspeccion  (Entidad: Plan de Inspección, DOMAIN_MODEL.md §9.2)
-- =============================================================================
CREATE TABLE planes_inspeccion (
    id                  uuid        NOT NULL DEFAULT gen_random_uuid(),
    fecha               date        NOT NULL,
    localidad           varchar(100),
    barrio              varchar(100),
    cantidad_ordenes    integer     NOT NULL DEFAULT 0,
    -- §9.2 NO define una sección "Estados" para este campo (a diferencia de Lote,
    -- OrdenInspeccion, AsignacionInspector, etc.); se deja abierto sin CHECK.
    estado              varchar(30) NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz,
    deleted_at          timestamptz,
    created_by          uuid,
    updated_by          uuid,
    CONSTRAINT pk_planes_inspeccion PRIMARY KEY (id),
    CONSTRAINT ck_planes_inspeccion_cantidad_ordenes_no_negativa CHECK (cantidad_ordenes >= 0)
);

CREATE TRIGGER trg_planes_inspeccion_set_updated_at
    BEFORE UPDATE ON planes_inspeccion
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE planes_inspeccion IS 'Entidad de dominio: Plan de Inspección (DOMAIN_MODEL.md §9.2).';

-- =============================================================================
-- 17. ordenes_inspeccion  (Entidad: Orden de Inspección, DOMAIN_MODEL.md §9.1)
-- =============================================================================
CREATE TABLE ordenes_inspeccion (
    id                      uuid        NOT NULL DEFAULT gen_random_uuid(),
    numero_orden            varchar(30) NOT NULL,
    suministro_id           uuid        NOT NULL,
    resultado_ia_id         uuid        NOT NULL,
    plan_inspeccion_id      uuid,
    prioridad               varchar(15) NOT NULL,
    estado                  varchar(15) NOT NULL,
    fecha_generacion        timestamptz NOT NULL DEFAULT now(),
    fecha_programada        date,
    observaciones           text,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz,
    deleted_at              timestamptz,
    created_by              uuid,
    updated_by              uuid,
    CONSTRAINT pk_ordenes_inspeccion PRIMARY KEY (id),
    CONSTRAINT fk_ordenes_inspeccion_suministro
        FOREIGN KEY (suministro_id) REFERENCES suministros (id), -- RD-030
    CONSTRAINT fk_ordenes_inspeccion_resultado_ia
        FOREIGN KEY (resultado_ia_id) REFERENCES resultados_ia (id), -- RD-031
    CONSTRAINT fk_ordenes_inspeccion_plan
        FOREIGN KEY (plan_inspeccion_id) REFERENCES planes_inspeccion (id),
    -- §9.1 "Prioridades".
    CONSTRAINT ck_ordenes_inspeccion_prioridad
        CHECK (prioridad IN ('Muy Baja', 'Baja', 'Media', 'Alta', 'Crítica')),
    -- §9.1 "Estados".
    CONSTRAINT ck_ordenes_inspeccion_estado
        CHECK (estado IN ('Generada', 'Pendiente', 'Asignada', 'En Proceso', 'Finalizada', 'Cancelada'))
);

CREATE UNIQUE INDEX uq_ordenes_inspeccion_numero_orden
    ON ordenes_inspeccion (numero_orden) WHERE deleted_at IS NULL;

-- RD-033: "No pueden existir dos órdenes activas para el mismo suministro".
-- Índice único parcial: solo restringe mientras el estado no sea terminal.
CREATE UNIQUE INDEX uq_ordenes_inspeccion_activa_por_suministro
    ON ordenes_inspeccion (suministro_id)
    WHERE estado NOT IN ('Finalizada', 'Cancelada') AND deleted_at IS NULL;

CREATE TRIGGER trg_ordenes_inspeccion_set_updated_at
    BEFORE UPDATE ON ordenes_inspeccion
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE ordenes_inspeccion IS 'Entidad de dominio: Orden de Inspección (DOMAIN_MODEL.md §9.1). Aggregate Root del contexto de Gestión de Inspecciones.';

-- =============================================================================
-- 18. asignaciones_inspector  (Entidad: Asignación de Inspector, DOMAIN_MODEL.md §9.3)
-- =============================================================================
CREATE TABLE asignaciones_inspector (
    id                      uuid        NOT NULL DEFAULT gen_random_uuid(),
    orden_inspeccion_id     uuid        NOT NULL,
    -- Referencia externa al sistema de RRHH (§9.3: "se integrará con el sistema
    -- de Recursos Humanos"); sin FK local porque el inspector no vive en este esquema.
    inspector_id            uuid        NOT NULL,
    fecha_asignacion        timestamptz NOT NULL DEFAULT now(),
    estado                  varchar(15) NOT NULL,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz,
    deleted_at              timestamptz,
    created_by              uuid,
    updated_by              uuid,
    CONSTRAINT pk_asignaciones_inspector PRIMARY KEY (id),
    -- Sin UNIQUE en orden_inspeccion_id: PlanInspeccion.reasignar() (§9.2) implica
    -- que una orden puede tener más de una asignación a lo largo del tiempo
    -- (historial de reasignaciones), no solo la vigente.
    CONSTRAINT fk_asignaciones_inspector_orden
        FOREIGN KEY (orden_inspeccion_id) REFERENCES ordenes_inspeccion (id),
    -- §9.3 "Estados".
    CONSTRAINT ck_asignaciones_inspector_estado
        CHECK (estado IN ('Pendiente', 'Aceptada', 'Rechazada', 'Finalizada'))
);

CREATE TRIGGER trg_asignaciones_inspector_set_updated_at
    BEFORE UPDATE ON asignaciones_inspector
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE asignaciones_inspector IS 'Entidad de dominio: Asignación de Inspector (DOMAIN_MODEL.md §9.3).';

-- =============================================================================
-- 19. inspecciones  (Entidad: Resultado de Inspección, DOMAIN_MODEL.md §9.4)
-- =============================================================================
-- El dominio no define una entidad "Inspección" con tabla de atributos propia
-- separada de "Resultado de Inspección" (§9.4): esta tabla representa la
-- ejecución + el resultado juntos, que es exactamente lo que ya traía el
-- borrador original. Se quita la columna inspector_id que tenía el borrador
-- (5.11): ahora vive en asignaciones_inspector.inspector_id, evitando duplicar
-- el dato en dos tablas.
CREATE TABLE inspecciones (
    id              uuid        NOT NULL DEFAULT gen_random_uuid(),
    orden_id        uuid        NOT NULL,
    asignacion_id   uuid,
    fecha_inicio    timestamptz NOT NULL DEFAULT now(),
    fecha_fin       timestamptz,
    resultado       varchar(30) NOT NULL,
    observaciones   text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz,
    deleted_at      timestamptz,
    created_by      uuid,
    updated_by      uuid,
    CONSTRAINT pk_inspecciones PRIMARY KEY (id),
    CONSTRAINT fk_inspecciones_orden
        FOREIGN KEY (orden_id) REFERENCES ordenes_inspeccion (id), -- RD-039
    CONSTRAINT fk_inspecciones_asignacion
        FOREIGN KEY (asignacion_id) REFERENCES asignaciones_inspector (id),
    -- §9.4 "Resultados". RD-037/RD-038: el resultado es obligatorio (NOT NULL + CHECK).
    CONSTRAINT ck_inspecciones_resultado
        CHECK (resultado IN ('Sin Novedad', 'Error de Medición', 'Medidor Defectuoso',
                              'Conexión Irregular', 'Fraude Confirmado', 'Normalizado'))
);

CREATE TRIGGER trg_inspecciones_set_updated_at
    BEFORE UPDATE ON inspecciones
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE inspecciones IS 'Entidad de dominio: Resultado de Inspección (DOMAIN_MODEL.md §9.4).';

-- =============================================================================
-- 20. hallazgos  (Entidad: Hallazgo, DOMAIN_MODEL.md §9.5)
-- =============================================================================
CREATE TABLE hallazgos (
    id              uuid        NOT NULL DEFAULT gen_random_uuid(),
    inspeccion_id   uuid        NOT NULL,
    -- §9.5 titula la lista "Ejemplos" (no "Tipos"): es una lista abierta, no
    -- exhaustiva. A diferencia de anomalias.tipo, acá NO se agrega CHECK.
    tipo            varchar(50) NOT NULL,
    descripcion     text,
    -- Reutiliza la escala de Severidad de §8.2: es un concepto transversal del
    -- lenguaje ubicuo (Baja/Media/Alta/Crítica), no redefinido por cada entidad.
    severidad       varchar(10) NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz,
    deleted_at      timestamptz,
    created_by      uuid,
    updated_by      uuid,
    CONSTRAINT pk_hallazgos PRIMARY KEY (id),
    CONSTRAINT fk_hallazgos_inspeccion
        FOREIGN KEY (inspeccion_id) REFERENCES inspecciones (id),
    CONSTRAINT ck_hallazgos_severidad CHECK (severidad IN ('Baja', 'Media', 'Alta', 'Crítica'))
);

CREATE TRIGGER trg_hallazgos_set_updated_at
    BEFORE UPDATE ON hallazgos
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE hallazgos IS 'Entidad de dominio: Hallazgo (DOMAIN_MODEL.md §9.5).';

-- =============================================================================
-- 21. recuperos_economicos  (Entidad: Recupero Económico, DOMAIN_MODEL.md §9.6)
-- =============================================================================
CREATE TABLE recuperos_economicos (
    id                  uuid        NOT NULL DEFAULT gen_random_uuid(),
    inspeccion_id       uuid        NOT NULL,
    monto_recuperado    numeric(14,2) NOT NULL,
    moneda              varchar(3)  NOT NULL DEFAULT 'ARS',
    fecha               date        NOT NULL,
    observaciones       text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz,
    deleted_at          timestamptz,
    created_by          uuid,
    updated_by          uuid,
    CONSTRAINT pk_recuperos_economicos PRIMARY KEY (id),
    -- RD-041: "Solo puede registrarse si existe una inspección finalizada." La FK
    -- garantiza que exista la inspección; que esté "finalizada" depende del
    -- estado de otra fila en otro momento y no es expresable con un CHECK simple
    -- (CHECK no puede leer otras tablas) -- queda como regla de aplicación/trigger.
    CONSTRAINT fk_recuperos_economicos_inspeccion
        FOREIGN KEY (inspeccion_id) REFERENCES inspecciones (id),
    -- RD-040: "El recupero nunca puede ser negativo".
    CONSTRAINT ck_recuperos_economicos_monto_no_negativo CHECK (monto_recuperado >= 0)
);

CREATE TRIGGER trg_recuperos_economicos_set_updated_at
    BEFORE UPDATE ON recuperos_economicos
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE recuperos_economicos IS 'Entidad de dominio: Recupero Económico (DOMAIN_MODEL.md §9.6).';

-- =============================================================================
-- 22. tareas_rrhh  (Entidad: Tarea RRHH, DOMAIN_MODEL.md §9.7)
-- =============================================================================
-- §9.7 no incluye tabla de Atributos (a diferencia de casi todas las demás
-- entidades del documento): solo describe Responsabilidades (crear, consultar
-- estado, sincronizar) y Estados. Las columnas se infirieron de esas
-- responsabilidades; es la entidad con más ambigüedad de todo el modelo.
CREATE TABLE tareas_rrhh (
    id                          uuid        NOT NULL DEFAULT gen_random_uuid(),
    asignacion_inspector_id     uuid,
    orden_inspeccion_id         uuid        NOT NULL,
    referencia_externa          varchar(50),
    estado                       varchar(15) NOT NULL,
    fecha_creacion               timestamptz NOT NULL DEFAULT now(),
    fecha_sincronizacion          timestamptz,
    created_at                   timestamptz NOT NULL DEFAULT now(),
    updated_at                   timestamptz,
    deleted_at                   timestamptz,
    created_by                   uuid,
    updated_by                   uuid,
    CONSTRAINT pk_tareas_rrhh PRIMARY KEY (id),
    CONSTRAINT fk_tareas_rrhh_asignacion
        FOREIGN KEY (asignacion_inspector_id) REFERENCES asignaciones_inspector (id),
    CONSTRAINT fk_tareas_rrhh_orden
        FOREIGN KEY (orden_inspeccion_id) REFERENCES ordenes_inspeccion (id),
    -- §9.7 "Estados".
    CONSTRAINT ck_tareas_rrhh_estado
        CHECK (estado IN ('Pendiente', 'Asignada', 'En Curso', 'Finalizada', 'Cancelada'))
);

CREATE TRIGGER trg_tareas_rrhh_set_updated_at
    BEFORE UPDATE ON tareas_rrhh
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE tareas_rrhh IS 'Entidad de dominio: Tarea RRHH (DOMAIN_MODEL.md §9.7). Sin tabla de Atributos en el dominio: columnas inferidas de sus Responsabilidades.';

-- =============================================================================
-- 23. feedback_modelo  (Entidad: Feedback del Modelo, DOMAIN_MODEL.md §10.1)
-- =============================================================================
CREATE TABLE feedback_modelo (
    id                      uuid        NOT NULL DEFAULT gen_random_uuid(),
    resultado_ia_id         uuid        NOT NULL,
    inspeccion_id           uuid        NOT NULL,
    prediccion_original     varchar(20) NOT NULL,
    resultado_real          varchar(30) NOT NULL,
    coincidencia            boolean     NOT NULL,
    fecha_registro          timestamptz NOT NULL DEFAULT now(),
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz,
    deleted_at              timestamptz,
    created_by              uuid,
    updated_by              uuid,
    CONSTRAINT pk_feedback_modelo PRIMARY KEY (id),
    CONSTRAINT fk_feedback_modelo_resultado_ia
        FOREIGN KEY (resultado_ia_id) REFERENCES resultados_ia (id),
    -- RD-042: "Todo Feedback debe estar asociado a una inspección finalizada." La
    -- FK exige que la inspección exista; el estado "finalizada" no es expresable
    -- con CHECK (no puede leer otra tabla) -- regla de aplicación/trigger.
    CONSTRAINT fk_feedback_modelo_inspeccion
        FOREIGN KEY (inspeccion_id) REFERENCES inspecciones (id),
    -- Reutiliza Clasificaciones de ResultadoIA (§8.1).
    CONSTRAINT ck_feedback_modelo_prediccion_original
        CHECK (prediccion_original IN ('Normal', 'Atención', 'Alto Riesgo', 'Crítico')),
    -- Reutiliza Resultados de Resultado de Inspección (§9.4).
    CONSTRAINT ck_feedback_modelo_resultado_real
        CHECK (resultado_real IN ('Sin Novedad', 'Error de Medición', 'Medidor Defectuoso',
                                   'Conexión Irregular', 'Fraude Confirmado', 'Normalizado')),
    CONSTRAINT uq_feedback_modelo_resultado_inspeccion UNIQUE (resultado_ia_id, inspeccion_id)
);

CREATE TRIGGER trg_feedback_modelo_set_updated_at
    BEFORE UPDATE ON feedback_modelo
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE feedback_modelo IS 'Entidad de dominio: Feedback del Modelo (DOMAIN_MODEL.md §10.1).';

-- =============================================================================
-- 24. datasets_etiquetados  (Entidad: Dataset Etiquetado, DOMAIN_MODEL.md §10.2)
-- =============================================================================
CREATE TABLE datasets_etiquetados (
    id              uuid        NOT NULL DEFAULT gen_random_uuid(),
    suministro_id   uuid        NOT NULL,
    fecha           date        NOT NULL,
    etiqueta        varchar(30) NOT NULL,
    -- §10.2 no enumera valores para "origen" en ninguna sección del documento;
    -- se deja abierto sin CHECK, igual criterio que planes_inspeccion.estado.
    origen          varchar(30) NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz,
    deleted_at      timestamptz,
    created_by      uuid,
    updated_by      uuid,
    CONSTRAINT pk_datasets_etiquetados PRIMARY KEY (id),
    CONSTRAINT fk_datasets_etiquetados_suministro
        FOREIGN KEY (suministro_id) REFERENCES suministros (id),
    -- RD-045: "Solo pueden incorporarse datos provenientes de inspecciones
    -- finalizadas" -- no expresable con CHECK (no referencia una inspección
    -- puntual en los atributos de esta entidad según §10.2); regla de aplicación.
    -- §10.2 "Etiquetas".
    CONSTRAINT ck_datasets_etiquetados_etiqueta
        CHECK (etiqueta IN ('Normal', 'Anomalía Confirmada', 'Fraude Confirmado',
                             'Error Administrativo', 'Medidor Defectuoso', 'Lectura Incorrecta'))
);

CREATE TRIGGER trg_datasets_etiquetados_set_updated_at
    BEFORE UPDATE ON datasets_etiquetados
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

COMMENT ON TABLE datasets_etiquetados IS 'Entidad de dominio: Dataset Etiquetado (DOMAIN_MODEL.md §10.2).';
