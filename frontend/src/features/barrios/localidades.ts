import type { BarrioRiesgo } from "./types";

/** Distinct localidades present in the barrio aggregates, in first-seen order (which the backend
 *  already returns worst-risk-first). A meter with no localidad relevada contributes a "Sin
 *  localidad" bucket so it is still selectable rather than silently hidden. */
export function localidadesDe(barrios: BarrioRiesgo[]): string[] {
  const vistas = new Set<string>();
  const orden: string[] = [];
  for (const b of barrios) {
    const nombre = b.localidad ?? "Sin localidad";
    if (!vistas.has(nombre)) {
      vistas.add(nombre);
      orden.push(nombre);
    }
  }
  return orden;
}

/** The barrios of a single localidad ("Sin localidad" matches rows whose `localidad` is null). */
export function barriosDe(barrios: BarrioRiesgo[], localidad: string): BarrioRiesgo[] {
  return barrios.filter((b) => (b.localidad ?? "Sin localidad") === localidad);
}
