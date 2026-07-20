import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getBarrios } from "./api";

/** `enabled: codigoLote !== ""` keeps the query idle until the container resolves a lote (same
 *  `""` "nothing selected yet" sentinel the ranking uses). */
export function useBarrios(codigoLote: string) {
  return useQuery({
    queryKey: ["barrios", codigoLote],
    queryFn: () => getBarrios(codigoLote),
    placeholderData: keepPreviousData,
    enabled: codigoLote !== "",
  });
}
