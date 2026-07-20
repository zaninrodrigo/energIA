import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { MapLegend } from "../../ranking/components/MapLegend";
import { nivelToMapColor } from "../../ranking/mapColors";
import { barrioRadius } from "../barrioRadius";
import type { BarrioRiesgo } from "../types";

const FORMOSA_CENTER: [number, number] = [-26.1849, -58.1731];
const INITIAL_ZOOM = 12;

export interface BarriosMapProps {
  barrios: BarrioRiesgo[];
}

function popupHtml(b: BarrioRiesgo): string {
  return `
    <div style="font-size:12px;line-height:1.5">
      <strong>${b.barrio ?? "Sin barrio"}</strong><br/>
      Medidores: ${b.total_medidores}<br/>
      IRE máximo: <strong>${b.ire_maximo}</strong> (${b.nivel})<br/>
      IRE promedio: ${b.ire_promedio}<br/>
      Con anomalías: ${b.con_anomalias}
    </div>`;
}

/**
 * Map of a localidad's barrios, each a circle colored by its potential nivel (its WORST meter's IRE
 * band) and sized by how many meters it has -- so a supervisor sees at a glance which barrios to
 * send a crew to. Presentational: renders whatever `barrios` it is given. Leaflet's imperative API
 * lives in effects (it is globally mocked in tests; jsdom can't render it).
 */
export function BarriosMap({ barrios }: BarriosMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) {
      return;
    }
    const map = L.map(containerRef.current).setView(FORMOSA_CENTER, INITIAL_ZOOM);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap",
      maxZoom: 19,
    }).addTo(map);
    layerRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const layer = layerRef.current;
    if (!map || !layer) {
      return;
    }
    layer.clearLayers();
    const latlngs: Array<[number, number]> = [];
    for (const b of barrios) {
      if (b.latitud === null || b.longitud === null) {
        continue;
      }
      latlngs.push([b.latitud, b.longitud]);
      const marker = L.circleMarker([b.latitud, b.longitud], {
        radius: barrioRadius(b.total_medidores),
        color: "#ffffff",
        weight: 1.5,
        fillColor: nivelToMapColor(b.nivel),
        fillOpacity: 0.75,
      });
      marker.bindPopup(popupHtml(b));
      marker.addTo(layer);
    }
    if (latlngs.length > 0) {
      map.fitBounds(L.latLngBounds(latlngs), { padding: [40, 40], maxZoom: 14 });
    }
  }, [barrios]);

  return (
    <div className="flex flex-col gap-2">
      <div
        ref={containerRef}
        role="figure"
        aria-label="Mapa de barrios por potencial de riesgo"
        className="h-96 w-full overflow-hidden rounded-lg border border-slate-200 shadow-sm"
      />
      <MapLegend />
    </div>
  );
}
