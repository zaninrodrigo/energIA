import type { Lote } from "../types";

export interface LoteSelectorProps {
  lotes: Lote[];
  selected: string | undefined;
  onChange: (codigoLote: string) => void;
}

/** Pure/presentational: which lote is selected and how it changes is entirely the container's
 *  business (`RankingPage`, including the "default to most recent Procesado" rule in
 *  `loteSelection.ts`) -- this component just renders the given `lotes` and reports picks. */
export function LoteSelector({ lotes, selected, onChange }: LoteSelectorProps) {
  return (
    <label className="flex flex-col gap-1 text-sm text-slate-600">
      Lote
      <select
        value={selected ?? ""}
        onChange={(event) => onChange(event.target.value)}
        className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-900"
      >
        {lotes.map((lote) => (
          <option key={lote.codigo_lote} value={lote.codigo_lote}>
            {lote.nombre ?? lote.codigo_lote}
          </option>
        ))}
      </select>
    </label>
  );
}
