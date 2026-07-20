import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getLotes, getRanking } from "./api";
import type { GetLotesParams, GetRankingParams } from "./api";

/** `placeholderData: keepPreviousData` (TanStack Query v5 idiom, same as `useSuministros`) keeps
 *  the previous page's data on screen while the next page is fetching. */
export function useLotes(params: GetLotesParams) {
  return useQuery({
    queryKey: ["lotes", params],
    queryFn: () => getLotes(params),
    placeholderData: keepPreviousData,
  });
}

/** `enabled: params.codigoLote !== ""` keeps this query idle (no request in flight) until the
 *  container has resolved a lote to show -- `""` is the container's own "nothing selected yet"
 *  sentinel (see `RankingPage.tsx`), never a value the backend would accept as a real
 *  `codigo_lote`. */
export function useRanking(params: GetRankingParams) {
  return useQuery({
    queryKey: ["ranking", params],
    queryFn: () => getRanking(params),
    placeholderData: keepPreviousData,
    enabled: params.codigoLote !== "",
  });
}
