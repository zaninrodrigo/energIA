export interface LocalidadSelectorProps {
  localidades: string[];
  selected: string | undefined;
  onChange: (localidad: string) => void;
}

/** Pure/presentational dropdown of localidades (same style as the ranking's `LoteSelector`); the
 *  container owns which is selected. */
export function LocalidadSelector({ localidades, selected, onChange }: LocalidadSelectorProps) {
  return (
    <label className="flex flex-col gap-1 text-sm text-slate-600">
      Localidad
      <select
        value={selected ?? ""}
        onChange={(event) => onChange(event.target.value)}
        className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-900"
      >
        {localidades.map((localidad) => (
          <option key={localidad} value={localidad}>
            {localidad}
          </option>
        ))}
      </select>
    </label>
  );
}
