import type { NivelIre } from "../ranking/types";

/**
 * Mirrors `BarrioRiesgoSchema` (backend/src/energia/contexts/motor/presentation/schemas.py). One
 * (localidad, barrio) aggregate for a processed lote. `nivel` bands `ire_maximo` (the WORST meter),
 * not the average -- see the backend's `BarrioRiesgoRow` docstring for why "potential for anomalous
 * consumption" keys off the worst meter, not a diluted mean.
 */
export interface BarrioRiesgo {
  localidad: string | null;
  barrio: string | null;
  total_medidores: number;
  ire_promedio: number;
  ire_maximo: number;
  nivel: NivelIre;
  con_anomalias: number;
  latitud: number | null;
  longitud: number | null;
}

/** Mirrors `BarriosRiesgoPageSchema` (`GET /api/v1/motor/lotes/{codigo_lote}/barrios`). */
export interface BarriosRiesgoPage {
  items: BarrioRiesgo[];
}
