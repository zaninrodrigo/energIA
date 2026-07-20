/**
 * Human-readable labels for DEC-016's explicability factors (`Observacion.factor`,
 * `docs/03-architecture/API_SPEC.md` §"GET .../resultados"). Not every possible factor is
 * guaranteed to appear (only the non-null ones do, up to 7 -- `inspecciones_anteriores` never
 * appears in v1), so `factorLabel` falls back to the raw key for anything unmapped instead of
 * throwing -- a forward-compatible default if a new factor is added on the backend before this
 * map is updated.
 */
const FACTOR_LABELS: Record<string, string> = {
  score_ia: "Score de IA",
  historial_consumos: "Historial de consumos",
  persistencia_anomalias: "Persistencia de anomalías",
  variacion_porcentual: "Variación porcentual",
  impacto_economico: "Impacto económico",
  consumo_promedio: "Consumo promedio",
  categoria_tarifaria: "Categoría tarifaria",
};

/** `score_ia` is the one DEC-016 factor Isolation Forest cannot attribute exactly (there is no
 *  clean per-feature breakdown of a `decision_function` score) -- its `reason` text always says
 *  so ("... (aproximación por atribución de features)"). Surfacing that same caveat as a short
 *  marker next to the label (rather than only inside the longer `reason` sentence) makes it
 *  visible even before a user reads the full explanation. */
export function factorLabel(factor: string): string {
  const base = FACTOR_LABELS[factor] ?? factor;
  return factor === "score_ia" ? `${base} (aproximación)` : base;
}

/**
 * Bar width (a percentage, 0-100) for one factor's contribution.
 *
 * `contribution` is ALREADY on the fixed 0-100 scale that composes `ire_valor` -- the non-null
 * factors of a single item sum to that item's own `ire_valor` (see `types.ts`'s `Observacion`
 * docstring). This function deliberately takes a single number, not the item's whole
 * `observaciones` list: normalizing against a list-local maximum (e.g. `contribution / max(...) *
 * 100`) would make a small contribution look "full" whenever every other factor in that same
 * item is also small (a low-IRE item), which misrepresents how much that factor actually
 * matters. Two factors with the same `contribution` value must always render the same bar width,
 * regardless of what else is in their respective items' lists.
 */
export function contributionBarWidth(contribution: number): number {
  return Math.min(100, Math.max(0, contribution));
}
