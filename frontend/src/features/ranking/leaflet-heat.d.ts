// Type augmentation for the `leaflet.heat` plugin, which ships no types. Declares the single
// `L.heatLayer` factory + the layer's `setLatLngs` we use to update points without re-creating it.
import "leaflet";

declare module "leaflet" {
  interface HeatLayer extends Layer {
    setLatLngs(latlngs: Array<[number, number, number]>): this;
  }

  function heatLayer(
    latlngs: Array<[number, number, number]>,
    options?: Record<string, unknown>,
  ): HeatLayer;
}
