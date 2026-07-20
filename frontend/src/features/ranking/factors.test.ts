import { describe, expect, it } from "vitest";
import { contributionBarWidth, factorLabel } from "./factors";

describe("factorLabel", () => {
  it("maps known DEC-016 factor keys to a human-readable Spanish label", () => {
    expect(factorLabel("historial_consumos")).toBe("Historial de consumos");
    expect(factorLabel("persistencia_anomalias")).toBe("Persistencia de anomalías");
    expect(factorLabel("variacion_porcentual")).toBe("Variación porcentual");
    expect(factorLabel("impacto_economico")).toBe("Impacto económico");
    expect(factorLabel("consumo_promedio")).toBe("Consumo promedio");
    expect(factorLabel("categoria_tarifaria")).toBe("Categoría tarifaria");
  });

  it("appends an '(aproximación)' marker only to the score_ia factor -- Isolation Forest has no exact per-feature attribution", () => {
    expect(factorLabel("score_ia")).toBe("Score de IA (aproximación)");
    expect(factorLabel("impacto_economico")).not.toContain("aproximación");
  });

  it("falls back to the raw factor key for an unmapped/future factor, never throwing", () => {
    expect(factorLabel("factor_nuevo_no_mapeado")).toBe("factor_nuevo_no_mapeado");
  });
});

describe("contributionBarWidth", () => {
  it("renders the bar width as the contribution value itself on the fixed 0-100 scale", () => {
    expect(contributionBarWidth(31.6189)).toBeCloseTo(31.6189);
    expect(contributionBarWidth(4.2354)).toBeCloseTo(4.2354);
  });

  it("does NOT normalize against a list's own max contribution -- the same absolute value always produces the same width, whether it is the biggest factor in a low-IRE item or a small one in a high-IRE item", () => {
    // A suministro whose only factor contributes 5 out of a possible 100 must show a SMALL bar,
    // even though 5 happens to be the max within its own (short) list -- never stretched to
    // "100% full" just because nothing else in that item's list is bigger.
    const onlyFactorInLowIreItem = 5;
    const oneOfManyFactorsInHighIreItem = 5;

    expect(contributionBarWidth(onlyFactorInLowIreItem)).toBe(
      contributionBarWidth(oneOfManyFactorsInHighIreItem),
    );
    expect(contributionBarWidth(onlyFactorInLowIreItem)).toBe(5);
  });

  it("clamps defensively to [0, 100] even though contribution should never fall outside that range in practice", () => {
    expect(contributionBarWidth(-1)).toBe(0);
    expect(contributionBarWidth(150)).toBe(100);
  });
});
