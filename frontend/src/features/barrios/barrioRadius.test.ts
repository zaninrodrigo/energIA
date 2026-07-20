import { describe, expect, it } from "vitest";
import { barrioRadius } from "./barrioRadius";

describe("barrioRadius", () => {
  it("grows with the meter count", () => {
    expect(barrioRadius(1)).toBeLessThan(barrioRadius(20));
  });

  it("is clamped so a huge barrio can't swallow the map", () => {
    expect(barrioRadius(100000)).toBe(40);
  });
});
