import type { ResultadoRankingItem } from "./types";

/** A Leaflet.heat point: `[lat, lng, intensity]` with intensity on a 0..1 scale. */
export type HeatPoint = [number, number, number];

/**
 * Turn ranking rows into heat-map points, weighting each by its IRE (0-100 → 0..1) so higher-risk
 * meters burn hotter. Rows without coordinates (legacy meters not yet georeferenced) are dropped
 * rather than plotted at a fake location.
 */
export function toHeatPoints(items: ResultadoRankingItem[]): HeatPoint[] {
  return items
    .filter((item) => item.latitud !== null && item.longitud !== null)
    .map((item) => [
      item.latitud as number,
      item.longitud as number,
      Math.min(Math.max(item.ire_valor / 100, 0), 1),
    ]);
}
