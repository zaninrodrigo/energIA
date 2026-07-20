import type { NivelIre } from "./types";

/**
 * On-map marker styling for each IRE nivel. Uses a MORE SATURATED variant of the same green→red
 * hue scale the badges use (`styles/index.css`'s `-risk-*` tokens): the badge tokens are muted
 * "soft badge" inks tuned for text-on-tint contrast, which read as muddy browns as solid dots on a
 * map. These vivid hues keep the exact same ordering/meaning while staying distinguishable at
 * marker size. Radius grows with risk so the few high-risk meters visually dominate the many calm
 * ones (the whole point of the map: find WHERE the risk concentrates).
 */
const NIVEL_MAP_COLOR: Record<NivelIre, string> = {
  "Muy Bajo": "#16a34a",
  Bajo: "#65a30d",
  Medio: "#f59e0b",
  Alto: "#ea580c",
  Crítico: "#dc2626",
};

const NIVEL_MARKER_RADIUS: Record<NivelIre, number> = {
  "Muy Bajo": 5,
  Bajo: 6,
  Medio: 8,
  Alto: 11,
  Crítico: 14,
};

export function nivelToMapColor(nivel: NivelIre): string {
  return NIVEL_MAP_COLOR[nivel];
}

export function nivelToMarkerRadius(nivel: NivelIre): number {
  return NIVEL_MARKER_RADIUS[nivel];
}
