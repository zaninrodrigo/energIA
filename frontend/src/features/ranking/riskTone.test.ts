import { describe, expect, it } from "vitest";
import { clasificacionToTone, nivelToTone, severidadToTone } from "./riskTone";
import type { Clasificacion, NivelIre, Severidad } from "./types";

describe("nivelToTone", () => {
  it.each<[NivelIre, string]>([
    ["Muy Bajo", "very-low"],
    ["Bajo", "low"],
    ["Medio", "medium"],
    ["Alto", "high"],
    ["Crítico", "critical"],
  ])("maps ire_nivel %s to the %s risk tone", (nivel, tone) => {
    expect(nivelToTone(nivel)).toBe(tone);
  });
});

describe("clasificacionToTone", () => {
  it.each<[Clasificacion, string]>([
    ["Normal", "very-low"],
    ["Atención", "medium"],
    ["Alto Riesgo", "high"],
    ["Crítico", "critical"],
  ])("maps clasificacion %s to the %s risk tone", (clasificacion, tone) => {
    expect(clasificacionToTone(clasificacion)).toBe(tone);
  });
});

describe("severidadToTone", () => {
  it.each<[Severidad, string]>([
    ["Baja", "low"],
    ["Media", "medium"],
    ["Alta", "high"],
    ["Crítica", "critical"],
  ])("maps anomalia severidad %s to the %s risk tone", (severidad, tone) => {
    expect(severidadToTone(severidad)).toBe(tone);
  });
});
