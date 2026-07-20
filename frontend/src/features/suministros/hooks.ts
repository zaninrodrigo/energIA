import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getSuministros } from "./api";
import type { GetSuministrosParams } from "./api";

/** `placeholderData: keepPreviousData` (TanStack Query v5 idiom) keeps the previous page's data
 *  on screen while the next page is fetching, instead of flashing a loading state on every
 *  pagination click. */
export function useSuministros(params: GetSuministrosParams) {
  return useQuery({
    queryKey: ["suministros", params],
    queryFn: () => getSuministros(params),
    placeholderData: keepPreviousData,
  });
}
