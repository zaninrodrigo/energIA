-- =============================================================================
-- EnergIA — Datos semilla
-- =============================================================================
-- Categorías tarifarias tal como las enumera DOMAIN_MODEL.md §7.3 ("Ejemplos"):
-- Residencial, Comercial, Industrial, Grandes Demandas, Alumbrado Público.
-- Al ser una tabla de catálogo (no un CHECK cerrado), agregar una categoría
-- nueva en el futuro es un INSERT, no una migración de esquema.
INSERT INTO categorias_tarifarias (nombre, descripcion) VALUES
    ('Residencial', 'Suministros de uso particular en viviendas.'),
    ('Comercial', 'Suministros de comercios y actividades de servicios.'),
    ('Industrial', 'Suministros de plantas y procesos industriales.'),
    ('Grandes Demandas', 'Suministros con demanda de potencia elevada.'),
    ('Alumbrado Público', 'Suministros de alumbrado público municipal.')
ON CONFLICT DO NOTHING;
