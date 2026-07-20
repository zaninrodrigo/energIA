import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "leaflet.heat";
import { toHeatPoints } from "../heatPoints";
import type { ResultadoRankingItem } from "../types";

// Formosa capital -- the demo dataset's meters are georeferenced around it (backfilled synthetic
// coordinates; real relevamiento data will replace them). Centering here keeps the initial view on
// the population the ranking covers.
const FORMOSA_CENTER: [number, number] = [-26.1849, -58.1731];
const INITIAL_ZOOM = 11;

export interface RiskHeatMapProps {
  items: ResultadoRankingItem[];
}

/**
 * Leaflet heat map of the ranking's meters, weighted by IRE (hotter = higher risk) so an operator
 * sees WHERE the risk concentrates, not just a table of ids. Presentational: it renders whatever
 * `items` it is given (the container owns the query/pagination). Leaflet's imperative API lives
 * inside effects; the pure row→point transform is `toHeatPoints` (separately unit-tested).
 */
export function RiskHeatMap({ items }: RiskHeatMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const heatRef = useRef<L.HeatLayer | null>(null);

  // Create the map once, on mount; tear it down on unmount. Kept separate from the points effect
  // so switching lotes/pages updates the heat layer without rebuilding the whole map.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) {
      return;
    }
    const map = L.map(containerRef.current).setView(FORMOSA_CENTER, INITIAL_ZOOM);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap",
      maxZoom: 19,
    }).addTo(map);
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      heatRef.current = null;
    };
  }, []);

  // Re-point the heat layer whenever the visible rows change.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) {
      return;
    }
    const points = toHeatPoints(items);
    if (heatRef.current) {
      heatRef.current.setLatLngs(points);
    } else {
      heatRef.current = L.heatLayer(points, { radius: 28, blur: 20, maxZoom: 17 }).addTo(map);
    }
  }, [items]);

  return (
    <div
      ref={containerRef}
      role="figure"
      aria-label="Mapa de calor de riesgo por ubicación de los medidores"
      className="h-96 w-full overflow-hidden rounded-lg border border-slate-200 shadow-sm"
    />
  );
}
