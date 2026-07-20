import { describe, expect, it } from "vitest";
import { barriosDe, localidadesDe } from "./localidades";
import type { BarrioRiesgo } from "./types";

function barrio(overrides: Partial<BarrioRiesgo>): BarrioRiesgo {
  return {
    localidad: "Formosa",
    barrio: "Centro",
    total_medidores: 3,
    ire_promedio: 20,
    ire_maximo: 40,
    nivel: "Bajo",
    con_anomalias: 0,
    latitud: -26.18,
    longitud: -58.17,
    ...overrides,
  };
}

describe("localidadesDe", () => {
  it("returns distinct localidades in first-seen order", () => {
    const barrios = [
      barrio({ localidad: "El Colorado" }),
      barrio({ localidad: "El Colorado" }),
      barrio({ localidad: "Formosa" }),
    ];
    expect(localidadesDe(barrios)).toEqual(["El Colorado", "Formosa"]);
  });

  it("buckets a null localidad as 'Sin localidad' instead of hiding it", () => {
    expect(localidadesDe([barrio({ localidad: null })])).toEqual(["Sin localidad"]);
  });
});

describe("barriosDe", () => {
  it("keeps only the barrios of the given localidad", () => {
    const barrios = [
      barrio({ localidad: "Formosa", barrio: "Centro" }),
      barrio({ localidad: "El Colorado", barrio: "Villa Hermosa" }),
    ];
    expect(barriosDe(barrios, "Formosa").map((b) => b.barrio)).toEqual(["Centro"]);
  });

  it("matches 'Sin localidad' against null-localidad rows", () => {
    const barrios = [barrio({ localidad: null, barrio: "X" })];
    expect(barriosDe(barrios, "Sin localidad")).toHaveLength(1);
  });
});
