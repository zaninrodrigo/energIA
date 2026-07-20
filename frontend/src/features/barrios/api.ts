import { apiGet } from "../../shared/api/client";
import type { BarriosRiesgoPage } from "./types";

/** Typed call to `GET /api/v1/motor/lotes/{codigo_lote}/barrios` -- every (localidad, barrio)
 *  aggregate of a processed lote. `codigoLote` is percent-escaped into the path (a business id, not
 *  a UUID). */
export function getBarrios(codigoLote: string): Promise<BarriosRiesgoPage> {
  return apiGet<BarriosRiesgoPage>(
    `/api/v1/motor/lotes/${encodeURIComponent(codigoLote)}/barrios`,
  );
}
