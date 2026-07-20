import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "leaflet.heat";
import { toHeatPoints } from "../heatPoints";
import { nivelToMapColor, nivelToMarkerRadius } from "../mapColors";
import type { ResultadoRankingItem } from "../types";
import { MapLegend } from "./MapLegend";

// Formosa capital -- the demo dataset's meters are georeferenced around it (backfilled synthetic
// coordinates; real relevamiento data will replace them). Centering here keeps the initial view on
// the population the ranking covers.
const FORMOSA_CENTER: [number, number] = [-26.1849, -58.1731];
const INITIAL_ZOOM = 11;

export interface RiskHeatMapProps {
  items: ResultadoRankingItem[];
  /** Called when a meter's marker is clicked -- lets the map open the same explicability drawer the
   *  table rows do, so an operator can go straight from a hot spot on the map to "why". */
  onSelect?: (item: ResultadoRankingItem) => void;
}

function popupHtml(item: ResultadoRankingItem): string {
  const rutafolio = item.rutafolio ?? "—";
  return `
    <div style="font-size:12px;line-height:1.4">
      <strong>${item.numero_suministro}</strong><br/>
      Rutafolio: ${rutafolio}<br/>
      IRE: <strong>${item.ire_valor}</strong> (${item.ire_nivel})
    </div>`;
}

/**
 * Map of the ranking's meters. Each georeferenced meter is a circle marker colored AND sized by its
 * IRE nivel (bigger + redder = higher risk), so the few high-risk supplies stand out among the many
 * calm ones -- clicking one opens its explicability drawer (via `onSelect`). A subtle heat layer
 * underneath conveys overall density. Presentational: it renders whatever `items` it is given; the
 * pure row→point transform is `toHeatPoints`, the pure color/size scales are `mapColors` (both
 * unit-tested). Leaflet's imperative API lives inside effects.
 */
export function RiskHeatMap({ items, onSelect }: RiskHeatMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const heatRef = useRef<L.HeatLayer | null>(null);
  const markersRef = useRef<L.LayerGroup | null>(null);
  // Kept in a ref so the markers effect always calls the latest handler without re-running on every
  // render just because the parent passed a new function identity. Updated in an effect (never
  // during render) so it stays compatible with concurrent rendering.
  const onSelectRef = useRef(onSelect);
  useEffect(() => {
    onSelectRef.current = onSelect;
  });

  // Create the map once, on mount; tear it down on unmount.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) {
      return;
    }
    const map = L.map(containerRef.current).setView(FORMOSA_CENTER, INITIAL_ZOOM);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap",
      maxZoom: 19,
    }).addTo(map);
    markersRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      heatRef.current = null;
      markersRef.current = null;
    };
  }, []);

  // Re-draw heat + markers whenever the visible rows change.
  useEffect(() => {
    const map = mapRef.current;
    const markers = markersRef.current;
    if (!map || !markers) {
      return;
    }

    const points = toHeatPoints(items);
    if (heatRef.current) {
      heatRef.current.setLatLngs(points);
    } else {
      heatRef.current = L.heatLayer(points, { radius: 22, blur: 24, maxZoom: 17 }).addTo(map);
    }

    markers.clearLayers();
    for (const item of items) {
      if (item.latitud === null || item.longitud === null) {
        continue;
      }
      const marker = L.circleMarker([item.latitud, item.longitud], {
        radius: nivelToMarkerRadius(item.ire_nivel),
        color: "#ffffff",
        weight: 1.5,
        fillColor: nivelToMapColor(item.ire_nivel),
        fillOpacity: 0.9,
      });
      marker.bindPopup(popupHtml(item));
      marker.on("click", () => onSelectRef.current?.(item));
      marker.addTo(markers);
    }
  }, [items]);

  return (
    <div className="flex flex-col gap-2">
      <div
        ref={containerRef}
        role="figure"
        aria-label="Mapa de riesgo por ubicación de los medidores"
        className="h-96 w-full overflow-hidden rounded-lg border border-slate-200 shadow-sm"
      />
      <MapLegend />
    </div>
  );
}
