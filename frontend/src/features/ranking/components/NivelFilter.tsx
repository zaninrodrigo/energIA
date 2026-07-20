import { NIVELES_IRE } from "../types";
import type { NivelIre } from "../types";

export interface NivelFilterProps {
  selected: NivelIre | undefined;
  onChange: (nivel: NivelIre | undefined) => void;
}

/** Pure/presentational nivel filter. The empty string is only this control's own "Todos" sentinel
 *  -- `onChange` always reports either `undefined` (no filter, matching `GetRankingParams.nivel`
 *  being omitted) or one of the 5 exact backend enum literals `NivelIre` declares, never `""`
 *  itself, so callers never have to special-case an empty string alongside `undefined`. */
export function NivelFilter({ selected, onChange }: NivelFilterProps) {
  return (
    <label className="flex flex-col gap-1 text-sm text-slate-600">
      Nivel
      <select
        value={selected ?? ""}
        onChange={(event) => {
          const { value } = event.target;
          onChange(value === "" ? undefined : (value as NivelIre));
        }}
        className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-900"
      >
        <option value="">Todos</option>
        {NIVELES_IRE.map((nivel) => (
          <option key={nivel} value={nivel}>
            {nivel}
          </option>
        ))}
      </select>
    </label>
  );
}
