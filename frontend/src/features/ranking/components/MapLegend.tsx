import { nivelToMapColor } from "../mapColors";
import { NIVELES_IRE } from "../types";

/** Legend for the risk map's marker colors -- one swatch per IRE nivel, in ascending risk order,
 *  so the colors are self-explanatory without prior knowledge of the scale. Pure/presentational. */
export function MapLegend() {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-600">
      {NIVELES_IRE.map((nivel) => (
        <li key={nivel} className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="inline-block h-3 w-3 rounded-full"
            style={{ backgroundColor: nivelToMapColor(nivel) }}
          />
          {nivel}
        </li>
      ))}
    </ul>
  );
}
