import { describe, expect, it } from "vitest";
import { nivelToMapColor, nivelToMarkerRadius } from "./mapColors";
import { NIVELES_IRE } from "./types";

describe("mapColors", () => {
  it("gives every nivel a distinct color", () => {
    const colors = NIVELES_IRE.map(nivelToMapColor);
    expect(new Set(colors).size).toBe(NIVELES_IRE.length);
  });

  it("grows the marker radius monotonically with risk", () => {
    const radii = NIVELES_IRE.map(nivelToMarkerRadius);
    for (let i = 1; i < radii.length; i += 1) {
      expect(radii[i]).toBeGreaterThan(radii[i - 1]);
    }
  });

  it("paints the highest risk red and the lowest green", () => {
    expect(nivelToMapColor("Crítico")).toBe("#dc2626");
    expect(nivelToMapColor("Muy Bajo")).toBe("#16a34a");
  });
});
