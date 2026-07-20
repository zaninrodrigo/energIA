import { useState } from "react";
import { EmptyState } from "../../../shared/ui/EmptyState";
import { ErrorState } from "../../../shared/ui/ErrorState";
import { Spinner } from "../../../shared/ui/Spinner";
import { LoteSelector } from "../../ranking/components/LoteSelector";
import { useLotes } from "../../ranking/hooks";
import { selectDefaultLote } from "../../ranking/loteSelection";
import { useBarrios } from "../hooks";
import { barriosDe, localidadesDe } from "../localidades";
import { BarriosMap } from "./BarriosMap";
import { BarriosTable } from "./BarriosTable";
import { LocalidadSelector } from "./LocalidadSelector";

const LOTE_SELECTOR_LIMIT = 100;

/**
 * Container: picks a lote and a localidad, then shows that localidad's barrios on a map (colored by
 * potential = worst meter's IRE band) and in a table (worst-first). Everything it renders below is
 * presentational. The localidad in effect is DERIVED from the fetched barrios + the user's pick
 * while rendering (never synced via effect+state), same pattern as `RankingPage`'s lote default.
 */
export function BarriosPage() {
  const [codigoLoteElegido, setCodigoLoteElegido] = useState<string | undefined>(undefined);
  const [localidadElegida, setLocalidadElegida] = useState<string | undefined>(undefined);

  const lotesQuery = useLotes({ limit: LOTE_SELECTOR_LIMIT, offset: 0, estado: "Procesado" });
  const selectedCodigoLote = selectDefaultLote(lotesQuery.data?.items ?? [], codigoLoteElegido);
  const barriosQuery = useBarrios(selectedCodigoLote ?? "");

  if (lotesQuery.isPending) {
    return <Spinner />;
  }
  if (lotesQuery.isError) {
    return <ErrorState message="No se pudieron cargar los lotes. Vuelva a intentarlo." />;
  }
  if (lotesQuery.data.items.length === 0) {
    return <EmptyState message="No hay lotes procesados todavía." />;
  }

  const barrios = barriosQuery.data?.items ?? [];
  const localidades = localidadesDe(barrios);
  // Derive the effective localidad: the user's pick if it's still present, else the first (worst-
  // risk) localidad the backend returned.
  const localidadEfectiva =
    localidadElegida && localidades.includes(localidadElegida)
      ? localidadElegida
      : localidades[0];
  const barriosVisibles = localidadEfectiva ? barriosDe(barrios, localidadEfectiva) : [];

  return (
    <section className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold text-slate-900">Riesgo por Barrio</h1>
        <p className="max-w-3xl text-sm text-slate-500">
          Elija una localidad para ver qué barrios concentran el potencial de consumos anómalos.
          Cada barrio se colorea según su medidor de mayor riesgo (IRE máximo): así un solo medidor
          crítico no queda diluido en el promedio del barrio.
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-4">
        <LoteSelector
          lotes={lotesQuery.data.items}
          selected={selectedCodigoLote}
          onChange={setCodigoLoteElegido}
        />
        {localidades.length > 0 ? (
          <LocalidadSelector
            localidades={localidades}
            selected={localidadEfectiva}
            onChange={setLocalidadElegida}
          />
        ) : null}
      </div>

      {barriosQuery.isPending ? <Spinner /> : null}
      {barriosQuery.isError ? (
        <ErrorState message="No se pudieron cargar los barrios. Vuelva a intentarlo." />
      ) : null}
      {barriosQuery.isSuccess && barrios.length === 0 ? (
        <EmptyState message="Este lote no tiene resultados analizados todavía." />
      ) : null}
      {barriosVisibles.length > 0 ? (
        <>
          <BarriosMap barrios={barriosVisibles} />
          <BarriosTable barrios={barriosVisibles} />
        </>
      ) : null}
    </section>
  );
}
