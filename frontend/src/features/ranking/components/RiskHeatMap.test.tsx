import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { rankingFixture } from "../../../test/fixtures";
import { RiskHeatMap } from "./RiskHeatMap";

// Leaflet itself is mocked globally in src/test/setup.ts (it needs a real browser layout jsdom
// can't provide). These tests verify the component mounts and exposes an accessible figure; the
// row→point math it feeds Leaflet is covered for real in heatPoints.test.ts.
describe("RiskHeatMap", () => {
  it("renders an accessible map figure", () => {
    render(<RiskHeatMap items={rankingFixture.items} />);
    expect(
      screen.getByRole("figure", { name: /mapa de calor de riesgo/i }),
    ).toBeInTheDocument();
  });

  it("mounts without crashing when there are no georeferenced rows", () => {
    render(<RiskHeatMap items={[]} />);
    expect(screen.getByRole("figure")).toBeInTheDocument();
  });
});
