import { apiGet } from "../../shared/api/client";
import type { SuministrosPage } from "./types";

export interface GetSuministrosParams {
  limit: number;
  offset: number;
  numeroCliente?: string;
}

/** Typed call to `GET /api/v1/suministros`. `numeroCliente` maps to the backend's
 *  `numero_cliente` query param -- a client that does not resolve to any cliente returns an
 *  empty page (`total: 0`), not an error (verified against
 *  backend/src/energia/contexts/suministros/presentation/routes.py). */
export function getSuministros({
  limit,
  offset,
  numeroCliente,
}: GetSuministrosParams): Promise<SuministrosPage> {
  return apiGet<SuministrosPage>("/api/v1/suministros", {
    params: { limit, offset, numero_cliente: numeroCliente },
  });
}
