/** Circle radius for a barrio marker: grows with its meter count (bigger neighbourhood = bigger
 *  circle), clamped so a huge barrio can't swallow the map. Pure, so it is unit-tested on its own
 *  and kept out of the component file (react-refresh wants component files to export only
 *  components). */
export function barrioRadius(totalMedidores: number): number {
  return Math.min(10 + Math.sqrt(totalMedidores) * 4, 40);
}
