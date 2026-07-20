import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, vi } from "vitest";
import { server } from "./msw/server";

// Leaflet needs a real browser layout/canvas it can't get in jsdom, so it is mocked globally: any
// component that renders the map (RiskHeatMap, and RankingPage which embeds it) exercises its
// wiring against these stubs instead of crashing. The pure row→point transform (`toHeatPoints`)
// is tested for real, separately, with no mock. `leaflet.heat` (a plugin that augments the real L)
// is stubbed too so importing it doesn't run against the mocked L.
vi.mock("leaflet.heat", () => ({}));
vi.mock("leaflet", () => {
  const heat = { addTo: () => heat, setLatLngs: () => heat };
  const group = { addTo: () => group, clearLayers: () => group, addLayer: () => group };
  const marker = { bindPopup: () => marker, on: () => marker, addTo: () => marker };
  const map = { setView: () => map, fitBounds: () => map, remove: () => undefined };
  const tile = { addTo: () => tile };
  return {
    default: {
      map: () => map,
      tileLayer: () => tile,
      layerGroup: () => group,
      circleMarker: () => marker,
      heatLayer: () => heat,
      latLngBounds: () => ({}),
    },
  };
});

// MSW runs in Node mode (msw/node) for component/hook tests -- no browser service worker
// involved. The E2E smoke test uses Playwright's own page.route() instead (see e2e/), a
// separate, network-level mocking mechanism documented in README.md.
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// `globals: false` in vite.config.ts keeps test files explicit about their imports, so
// Testing Library's DOM cleanup (unmounting components between tests) must be wired manually.
afterEach(cleanup);
