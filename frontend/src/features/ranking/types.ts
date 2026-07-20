import type { Page } from "../../shared/api/types";

/**
 * Mirrors `NivelFiltro` / the 5 bands of the generated `ire.nivel` column
 * (backend/src/energia/contexts/motor/presentation/schemas.py, docker/postgres/init/01_schema.sql).
 */
export type NivelIre = "Muy Bajo" | "Bajo" | "Medio" | "Alto" | "Crítico";

export const NIVELES_IRE: readonly NivelIre[] = ["Muy Bajo", "Bajo", "Medio", "Alto", "Crítico"];

/** Mirrors `ClasificacionFiltro` / DEC-015's 4 bands (`ck_resultados_ia_clasificacion`). */
export type Clasificacion = "Normal" | "Atención" | "Alto Riesgo" | "Crítico";

/** Mirrors `ck_anomalias_severidad` (docker/postgres/init/01_schema.sql). */
export type Severidad = "Baja" | "Media" | "Alta" | "Crítica";

/**
 * Mirrors `AnomaliaRankingSchema` in
 * backend/src/energia/contexts/motor/presentation/schemas.py exactly (hand-written, no codegen --
 * same convention as `features/suministros/types.ts`).
 */
export interface AnomaliaRanking {
  tipo: string;
  severidad: Severidad;
  descripcion: string | null;
}

/**
 * One entry of DEC-016's explicability breakdown -- mirrors `ObservacionSchema`. `contribution`
 * is already on the fixed 0-100 scale that composes `ire_valor` (the 7 possible factors' non-null
 * contributions sum to `ire_valor`, never to some other item-local total) -- render bars against
 * that fixed scale, never normalized to the max contribution within a single item's own list (that
 * would make a low-IRE suministro's biggest factor look just as "full" as a high-IRE one's).
 */
export interface Observacion {
  factor: string;
  contribution: number;
  reason: string;
}

/**
 * Mirrors `ResultadoRankingItemSchema`. One row of a processed lote's IRE ranking.
 */
export interface ResultadoRankingItem {
  suministro_id: string;
  numero_suministro: string;
  ire_valor: number;
  ire_nivel: NivelIre;
  clasificacion: Clasificacion;
  score_anomalia: number | null;
  probabilidad: number | null;
  localidad: string | null;
  categoria_tarifaria: string;
  anomalias: AnomaliaRanking[];
  observaciones: Observacion[];
  iee_kwh: number | null;
}

/**
 * Mirrors `ResumenRankingSchema`. Whole-lote summary counts for the dashboard's KPI row --
 * ALWAYS unfiltered by `?nivel=` (see `ResultadosRankingPage.resumen`'s docstring below): the
 * backend computes this ignoring the ranking's own `nivel` query param, so the KPI row must never
 * be fed a *different*, filter-scoped request than the one the table itself receives -- always
 * read `resumen` off the SAME response the (possibly filtered) `items` came from.
 */
export interface ResumenRanking {
  total_resultados: number;
  conteo_por_nivel: Record<NivelIre, number>;
  conteo_por_clasificacion: Record<Clasificacion, number>;
  con_anomalias: number;
  suma_iee_kwh: number;
}

/**
 * Mirrors `ResultadosRankingPageSchema` (`GET /api/v1/motor/lotes/{codigo_lote}/resultados`).
 * `resumen` is the whole lote's UNFILTERED summary -- see `ResumenRanking`'s docstring. `total`
 * (from the generic `Page<T>`) IS filtered by `?nivel=`, same convention as every other list
 * endpoint in this app -- deliberately a different field than `resumen.total_resultados`.
 */
export interface ResultadosRankingPage extends Page<ResultadoRankingItem> {
  resumen: ResumenRanking;
}

/**
 * Mirrors `LoteSchema` (backend/src/energia/contexts/consumos/presentation/schemas.py) -- a
 * different bounded context (Gestión de Consumos) than the ranking itself (Motor), reused here
 * only to populate the lote selector (`GET /api/v1/lotes?estado=Procesado`).
 */
export interface Lote {
  id: string;
  codigo_lote: string;
  nombre: string | null;
  fecha_importacion: string;
  cantidad_registros: number;
  estado: string;
}

export type LotesPage = Page<Lote>;
