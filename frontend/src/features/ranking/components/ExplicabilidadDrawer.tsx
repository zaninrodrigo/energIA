import { Badge } from "../../../shared/ui/Badge";
import { Drawer } from "../../../shared/ui/Drawer";
import { contributionBarWidth, factorLabel } from "../factors";
import { clasificacionToTone, nivelToTone, severidadToTone } from "../riskTone";
import type { ResultadoRankingItem } from "../types";

export interface ExplicabilidadDrawerProps {
  item: ResultadoRankingItem | null;
  onClose: () => void;
}

/** Pure/presentational: the row selected for explicability is entirely the container's state
 *  (`RankingPage`); this component only renders it. `isOpen` is derived from `item !== null` --
 *  there is deliberately no separate boolean to keep in sync with it. */
export function ExplicabilidadDrawer({ item, onClose }: ExplicabilidadDrawerProps) {
  return (
    <Drawer
      isOpen={item !== null}
      onClose={onClose}
      title={item ? `Explicabilidad — ${item.numero_suministro}` : undefined}
    >
      {item ? (
        <div className="flex flex-col gap-6">
          <div className="flex items-center gap-3">
            <span className="text-2xl font-semibold tabular-nums text-slate-900">
              {item.ire_valor}
            </span>
            <Badge tone={nivelToTone(item.ire_nivel)}>{item.ire_nivel}</Badge>
            <Badge tone={clasificacionToTone(item.clasificacion)}>{item.clasificacion}</Badge>
          </div>

          <section>
            <h3 className="mb-2 text-sm font-semibold text-slate-900">Factores del IRE</h3>
            {item.observaciones.length === 0 ? (
              <p className="text-sm text-slate-500">Sin factores registrados.</p>
            ) : (
              <ul className="flex flex-col gap-3">
                {item.observaciones.map((observacion) => {
                  const label = factorLabel(observacion.factor);
                  // Fixed 0-100 scale (never normalized to this item's own max contribution) --
                  // see `factors.ts`'s `contributionBarWidth` docstring for why.
                  const width = contributionBarWidth(observacion.contribution);
                  return (
                    <li key={observacion.factor}>
                      <div className="flex items-center justify-between text-sm">
                        <span className="font-medium text-slate-700">{label}</span>
                        <span className="tabular-nums text-slate-500">
                          {observacion.contribution.toFixed(2)}
                        </span>
                      </div>
                      <div
                        role="progressbar"
                        aria-label={label}
                        aria-valuenow={width}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        className="mt-1 h-2 w-full overflow-hidden rounded-full bg-slate-100"
                      >
                        <div
                          className="h-full rounded-full bg-brand"
                          style={{ width: `${width}%` }}
                        />
                      </div>
                      <p className="mt-1 text-xs text-slate-500">{observacion.reason}</p>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <section>
            <h3 className="mb-2 text-sm font-semibold text-slate-900">Anomalías detectadas</h3>
            {item.anomalias.length === 0 ? (
              <p className="text-sm text-slate-500">Sin anomalías registradas.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {item.anomalias.map((anomalia, index) => (
                  <li
                    key={`${anomalia.tipo}-${index}`}
                    className="flex flex-col gap-1 rounded-md border border-slate-200 p-2"
                  >
                    <div className="flex items-center gap-2">
                      <Badge tone={severidadToTone(anomalia.severidad)}>{anomalia.severidad}</Badge>
                      <span className="text-sm font-medium text-slate-700">{anomalia.tipo}</span>
                    </div>
                    {anomalia.descripcion ? (
                      <p className="text-xs text-slate-500">{anomalia.descripcion}</p>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      ) : null}
    </Drawer>
  );
}
