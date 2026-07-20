import { StatCard } from "../../../shared/ui/StatCard";
import { nivelToTone } from "../riskTone";
import { NIVELES_IRE } from "../types";
import type { ResumenRanking } from "../types";

export interface RankingSummaryProps {
  resumen: ResumenRanking;
}

/** Pure/presentational KPI row, driven entirely by `resumen` -- the whole lote's UNFILTERED
 *  summary (see `types.ts`'s `ResumenRanking` docstring). The container (`RankingPage`) must
 *  always pass the `resumen` that came back alongside the CURRENT `items` request, never a
 *  separately-fetched or filter-scoped one -- this component has no way to detect that mistake
 *  on its own, since a `resumen` object is a `resumen` object either way. */
export function RankingSummary({ resumen }: RankingSummaryProps) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      <StatCard label="Total analizados" value={resumen.total_resultados} />
      {NIVELES_IRE.map((nivel) => (
        <StatCard
          key={nivel}
          label={nivel}
          value={resumen.conteo_por_nivel[nivel]}
          tone={nivelToTone(nivel)}
        />
      ))}
      <StatCard label="Con anomalías" value={resumen.con_anomalias} />
      <StatCard label="IEE total" value={`${resumen.suma_iee_kwh.toFixed(2)} kWh`} />
    </div>
  );
}
