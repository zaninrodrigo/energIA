-- =============================================================================
-- EnergIA — Esquema de staging
-- =============================================================================
-- Se crea el schema "staging" como destino de aterrizaje para los archivos de
-- consumos históricos que se recibirán de forma manual (sin acceso a Oracle,
-- ver ADR-004): alguien entregará archivos y todavía no se conoce su formato
-- (CSV, Excel, extracto de otro sistema, etc.).
--
-- A propósito, este script NO define ninguna tabla de staging todavía. Diseñar
-- esas tablas ahora sería inventar una estructura sobre datos que no existen:
-- se diseñarán cuando se conozca el formato real de los archivos a recibir
-- (columnas, tipos, calidad de datos, encoding). Ver PROJECT_MASTER_SPEC.md,
-- deuda "Diseño de tablas staging y proceso de carga pendiente de conocer el
-- formato de los datos históricos a recibir".
CREATE SCHEMA IF NOT EXISTS staging;

COMMENT ON SCHEMA staging IS
    'Destino de aterrizaje para los archivos de consumos históricos a recibir. '
    'Las tablas de staging se diseñarán cuando se conozca el formato real de esos '
    'archivos (ver PROJECT_MASTER_SPEC.md, deuda documental correspondiente). '
    'Este schema existe para reservar el espacio de nombres, no para almacenar '
    'datos todavía.';
