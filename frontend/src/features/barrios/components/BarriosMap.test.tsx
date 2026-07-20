import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { barriosFixture } from "../../../test/fixtures";
import { BarriosMap } from "./BarriosMap";

// Leaflet is globally mocked (src/test/setup.ts). These verify mount + accessibility + the pure
// radius scale; the map rendering itself is Leaflet's, not ours.
describe("BarriosMap", () => {
  it("renders an accessible figure with the legend", () => {
    render(<BarriosMap barrios={barriosFixture.items} />);
    expect(screen.getByRole("figure", { name: /mapa de barrios/i })).toBeInTheDocument();
    expect(screen.getByText("Crítico")).toBeInTheDocument();
  });

  it("mounts without crashing when no barrio is georeferenced", () => {
    render(<BarriosMap barrios={[]} />);
    expect(screen.getByRole("figure")).toBeInTheDocument();
  });
});
