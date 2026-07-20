import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { RankingSummary } from "./RankingSummary";
import type { ResumenRanking } from "../types";

const resumen: ResumenRanking = {
  total_resultados: 94,
  conteo_por_nivel: { "Muy Bajo": 70, Bajo: 17, Medio: 2, Alto: 5, Crítico: 0 },
  conteo_por_clasificacion: { Normal: 70, Atención: 17, "Alto Riesgo": 7, Crítico: 0 },
  con_anomalias: 9,
  suma_iee_kwh: 16181.36,
};

describe("RankingSummary", () => {
  it("renders the total analizados KPI", () => {
    render(<RankingSummary resumen={resumen} />);

    expect(screen.getByText("Total analizados")).toBeInTheDocument();
    expect(screen.getByText("94")).toBeInTheDocument();
  });

  it("renders one StatCard per nivel band, with the count and matching risk tone", () => {
    render(<RankingSummary resumen={resumen} />);

    expect(screen.getByText("Muy Bajo").closest("[data-tone]")).toHaveAttribute(
      "data-tone",
      "very-low",
    );
    expect(screen.getByText("Crítico").closest("[data-tone]")).toHaveAttribute(
      "data-tone",
      "critical",
    );
    // count "5" appears next to the "Alto" label specifically
    const altoCard = screen.getByText("Alto").closest("[data-tone]");
    expect(altoCard).not.toBeNull();
    expect(altoCard).toHaveTextContent("5");
  });

  it("renders the con_anomalias KPI", () => {
    render(<RankingSummary resumen={resumen} />);

    expect(screen.getByText("Con anomalías")).toBeInTheDocument();
    expect(screen.getByText("9")).toBeInTheDocument();
  });

  it("renders the suma_iee_kwh KPI in kWh with a plain-language label spelling out IEE", () => {
    render(<RankingSummary resumen={resumen} />);

    expect(screen.getByText("Energía no facturada")).toBeInTheDocument();
    expect(screen.getByText("16181 kWh")).toBeInTheDocument();
    expect(screen.getByText("Impacto Económico Estimado (IEE)")).toBeInTheDocument();
  });

  it("renders zero counts as literal 0, not a falsy dash/blank", () => {
    render(<RankingSummary resumen={resumen} />);

    const criticoCard = screen.getByText("Crítico").closest("[data-tone]");
    expect(criticoCard).toHaveTextContent("0");
  });
});
