import { describe, expect, it } from "vitest";
import { toHeatPoints } from "./heatPoints";
import type { ResultadoRankingItem } from "./types";

function item(overrides: Partial<ResultadoRankingItem>): ResultadoRankingItem {
  return {
    suministro_id: "s1",
    numero_suministro: "SYN-S42-SUM-00001",
    rutafolio: "90000000001",
    latitud: -26.18,
    longitud: -58.17,
    ire_valor: 50,
    ire_nivel: "Medio",
    clasificacion: "Alto Riesgo",
    score_anomalia: null,
    probabilidad: null,
    localidad: "Formosa",
    categoria_tarifaria: "Residencial",
    anomalias: [],
    observaciones: [],
    iee_kwh: null,
    ...overrides,
  };
}

describe("toHeatPoints", () => {
  it("weights each point's intensity by IRE on a 0..1 scale", () => {
    expect(toHeatPoints([item({ ire_valor: 80, latitud: -26.1, longitud: -58.2 })])).toEqual([
      [-26.1, -58.2, 0.8],
    ]);
  });

  it("drops rows without coordinates instead of plotting them at a fake location", () => {
    const points = toHeatPoints([
      item({ latitud: null, longitud: null }),
      item({ latitud: -26.3, longitud: -58.05, ire_valor: 20 }),
    ]);
    expect(points).toEqual([[-26.3, -58.05, 0.2]]);
  });

  it("clamps intensity into [0, 1] for out-of-range IRE values", () => {
    expect(toHeatPoints([item({ ire_valor: 0 })])[0][2]).toBe(0);
    expect(toHeatPoints([item({ ire_valor: 100 })])[0][2]).toBe(1);
  });
});
