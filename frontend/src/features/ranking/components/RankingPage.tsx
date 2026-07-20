import { useState } from "react";
import { EmptyState } from "../../../shared/ui/EmptyState";
import { ErrorState } from "../../../shared/ui/ErrorState";
import { Pagination } from "../../../shared/ui/Pagination";
import { Spinner } from "../../../shared/ui/Spinner";
import { useLotes, useRanking } from "../hooks";
import { selectDefaultLote } from "../loteSelection";
import type { NivelIre, ResultadoRankingItem } from "../types";
import { ExplicabilidadDrawer } from "./ExplicabilidadDrawer";
import { LoteSelector } from "./LoteSelector";
import { NivelFilter } from "./NivelFilter";
import { RankingSummary } from "./RankingSummary";
import { RankingTable } from "./RankingTable";
import { RiskHeatMap } from "./RiskHeatMap";

const RANKING_PAGE_LIMIT = 50;
// Generous enough to list every Procesado lote a single operator deals with day to day without
// its own pagination UI -- if that stops being true, this selector is the first place that needs
// a "load more"/search box, not a silent truncation.
const LOTE_SELECTOR_LIMIT = 100;

/**
 * Container: owns the lote selection, nivel filter and pagination state, plus the row currently
 * open in the explicability drawer. Every other piece here (`LoteSelector`, `NivelFilter`,
 * `RankingSummary`, `RankingTable`, `ExplicabilidadDrawer`) stays presentational, receiving only
 * resolved data -- same strict container/presentational split as `SuministrosPage`.
 */
export function RankingPage() {
  const [offset, setOffset] = useState(0);
  const [nivel, setNivel] = useState<NivelIre | undefined>(undefined);
  // The user's own explicit pick, if any -- `undefined` until they choose one. The lote actually
  // in effect (`selectedCodigoLote` below) is a value DERIVED from this plus the lotes list on
  // every render (never synchronized via a `useEffect` + `setState`, an anti-pattern for state
  // that can simply be computed while rendering -- see `selectDefaultLote`'s docstring for the
  // "most recent Procesado" default rule this derives).
  const [codigoLoteElegido, setCodigoLoteElegido] = useState<string | undefined>(undefined);
  const [selectedItem, setSelectedItem] = useState<ResultadoRankingItem | null>(null);

  const lotesQuery = useLotes({ limit: LOTE_SELECTOR_LIMIT, offset: 0, estado: "Procesado" });
  const selectedCodigoLote = selectDefaultLote(lotesQuery.data?.items ?? [], codigoLoteElegido);

  const rankingQuery = useRanking({
    codigoLote: selectedCodigoLote ?? "",
    limit: RANKING_PAGE_LIMIT,
    offset,
    nivel,
  });

  function handleSelectLote(next: string) {
    setCodigoLoteElegido(next);
    setOffset(0);
    // `nivel` is deliberately kept across a lote switch: a user comparing "Crítico" suministros
    // across lotes almost certainly wants to keep that filter, not have it silently cleared.
  }

  function handleNivelChange(next: NivelIre | undefined) {
    setNivel(next);
    setOffset(0);
  }

  if (lotesQuery.isPending) {
    return <Spinner />;
  }

  if (lotesQuery.isError) {
    return <ErrorState message="No se pudieron cargar los lotes. Vuelva a intentarlo." />;
  }

  const lotes = lotesQuery.data.items;

  if (lotes.length === 0) {
    return <EmptyState message="No hay lotes procesados todavía." />;
  }

  return (
    <section className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold text-slate-900">Ranking de Riesgo</h1>
        <p className="max-w-3xl text-sm text-slate-500">
          Suministros ordenados por su Índice de Riesgo Energético (IRE), un puntaje de 0 a 100 que
          prioriza qué medidores inspeccionar primero. Cuanto más alto el IRE, mayor la probabilidad
          de un consumo anómalo.
        </p>
      </header>
      <div className="flex flex-wrap items-end gap-4">
        <LoteSelector lotes={lotes} selected={selectedCodigoLote} onChange={handleSelectLote} />
        <NivelFilter selected={nivel} onChange={handleNivelChange} />
      </div>

      {rankingQuery.isPending ? <Spinner /> : null}
      {rankingQuery.isError ? (
        <ErrorState message="No se pudo cargar el ranking. Vuelva a intentarlo." />
      ) : null}
      {rankingQuery.isSuccess ? (
        <>
          <RankingSummary resumen={rankingQuery.data.resumen} />
          {rankingQuery.data.items.length === 0 ? (
            <EmptyState message="No se encontraron resultados." />
          ) : (
            <>
              <div className="flex flex-col gap-2">
                <h2 className="text-sm font-semibold text-slate-700">
                  Ubicación de los medidores (mapa de calor por riesgo)
                </h2>
                <RiskHeatMap items={rankingQuery.data.items} />
              </div>
              <RankingTable items={rankingQuery.data.items} onSelect={setSelectedItem} />
            </>
          )}
          <Pagination
            total={rankingQuery.data.total}
            limit={rankingQuery.data.limit}
            offset={rankingQuery.data.offset}
            onPrevious={() => setOffset((current) => Math.max(0, current - RANKING_PAGE_LIMIT))}
            onNext={() => setOffset((current) => current + RANKING_PAGE_LIMIT)}
          />
        </>
      ) : null}

      <ExplicabilidadDrawer item={selectedItem} onClose={() => setSelectedItem(null)} />
    </section>
  );
}
