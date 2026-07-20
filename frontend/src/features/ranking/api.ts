import { apiGet } from "../../shared/api/client";
import type { LotesPage, NivelIre, ResultadosRankingPage } from "./types";

export interface GetLotesParams {
  limit: number;
  offset: number;
  estado?: string;
}

/** Typed call to `GET /api/v1/lotes`, used to populate the lote selector (see
 *  `loteSelection.ts`). Callers pass `estado: "Procesado"` to only offer lotes that actually have
 *  a ranking to show. */
export function getLotes({ limit, offset, estado }: GetLotesParams): Promise<LotesPage> {
  return apiGet<LotesPage>("/api/v1/lotes", { params: { limit, offset, estado } });
}

export interface GetRankingParams {
  codigoLote: string;
  limit: number;
  offset: number;
  nivel?: NivelIre;
}

/** Typed call to `GET /api/v1/motor/lotes/{codigo_lote}/resultados`. `codigoLote` is
 *  `encodeURIComponent`-escaped into the path (it is a business identifier, not a UUID -- see
 *  `docs/03-architecture/API_SPEC.md`). `nivel` goes through `apiGet`'s own `URLSearchParams`
 *  based query-string builder (`shared/api/client.ts`), which correctly percent-encodes accented
 *  values like `"Crítico"` -- never string-concatenated by hand. */
export function getRanking({
  codigoLote,
  limit,
  offset,
  nivel,
}: GetRankingParams): Promise<ResultadosRankingPage> {
  return apiGet<ResultadosRankingPage>(
    `/api/v1/motor/lotes/${encodeURIComponent(codigoLote)}/resultados`,
    { params: { limit, offset, nivel } },
  );
}
