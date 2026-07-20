import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ExplicabilidadDrawer } from "./ExplicabilidadDrawer";
import type { ResultadoRankingItem } from "../types";

const baseItem: ResultadoRankingItem = {
  suministro_id: "1",
  numero_suministro: "SYN-S42-SUM-00070",
  rutafolio: "90000000070",
  latitud: -26.1189,
  longitud: -58.1731,
  ire_valor: 70,
  ire_nivel: "Alto",
  clasificacion: "Alto Riesgo",
  score_anomalia: -0.034,
  probabilidad: 0.9696,
  localidad: "Formosa",
  categoria_tarifaria: "Industrial",
  anomalias: [
    { tipo: "Patrón Irregular", severidad: "Crítica", descripcion: "Score de anomalía: 97/100" },
  ],
  observaciones: [
    { factor: "score_ia", contribution: 31.6189, reason: "Score del modelo de IA: 97.0/100" },
    { factor: "historial_consumos", contribution: 4.2354, reason: "Desviación de -1.30 desvíos" },
  ],
  iee_kwh: 1067.34,
};

describe("ExplicabilidadDrawer", () => {
  it("renders nothing (closed) when item is null", () => {
    render(<ExplicabilidadDrawer item={null} onClose={vi.fn()} />);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens with a title naming the suministro, and an IRE/nivel/clasificacion summary", () => {
    render(<ExplicabilidadDrawer item={baseItem} onClose={vi.fn()} />);

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(/SYN-S42-SUM-00070/)).toBeInTheDocument();
    expect(screen.getByText("70")).toBeInTheDocument();
    expect(screen.getByText("Alto")).toHaveAttribute("data-tone", "high");
    expect(screen.getByText("Alto Riesgo")).toHaveAttribute("data-tone", "high");
  });

  it("labels the score_ia factor with the '(aproximación)' marker, and other factors without it", () => {
    render(<ExplicabilidadDrawer item={baseItem} onClose={vi.fn()} />);

    expect(screen.getByText("Score de IA (aproximación)")).toBeInTheDocument();
    expect(screen.getByText("Historial de consumos")).toBeInTheDocument();
  });

  it("renders each factor's reason text", () => {
    render(<ExplicabilidadDrawer item={baseItem} onClose={vi.fn()} />);

    expect(screen.getByText("Score del modelo de IA: 97.0/100")).toBeInTheDocument();
    expect(screen.getByText("Desviación de -1.30 desvíos")).toBeInTheDocument();
  });

  it("renders each factor's contribution bar on the FIXED 0-100 scale, not normalized to the largest factor in this item's own list", () => {
    render(<ExplicabilidadDrawer item={baseItem} onClose={vi.fn()} />);

    // If this were wrongly normalized to the local max (31.6189 -> 100%), historial_consumos
    // (4.2354) would be rescaled up too (~13.4%) instead of staying at its own absolute value.
    const scoreIaBar = screen.getByRole("progressbar", { name: /score de ia/i });
    const historialBar = screen.getByRole("progressbar", { name: /historial de consumos/i });

    expect(Number(scoreIaBar.getAttribute("aria-valuenow"))).toBeCloseTo(31.6189);
    expect(Number(historialBar.getAttribute("aria-valuenow"))).toBeCloseTo(4.2354);
  });

  it("renders the same absolute bar width for equal contributions across two different items (no per-item relative scaling)", () => {
    const lowIreItem: ResultadoRankingItem = {
      ...baseItem,
      suministro_id: "2",
      numero_suministro: "SYN-S42-SUM-00099",
      ire_valor: 8,
      observaciones: [{ factor: "impacto_economico", contribution: 5, reason: "Bajo impacto" }],
    };

    const { unmount } = render(<ExplicabilidadDrawer item={lowIreItem} onClose={vi.fn()} />);
    const lowItemBar = screen.getByRole("progressbar", { name: /impacto económico/i });
    expect(lowItemBar.getAttribute("aria-valuenow")).toBe("5");
    unmount();

    const highIreItemWithSameAbsoluteContribution: ResultadoRankingItem = {
      ...baseItem,
      observaciones: [
        { factor: "score_ia", contribution: 60, reason: "..." },
        { factor: "impacto_economico", contribution: 5, reason: "Mismo impacto absoluto" },
      ],
    };
    render(<ExplicabilidadDrawer item={highIreItemWithSameAbsoluteContribution} onClose={vi.fn()} />);
    const highItemBar = screen.getByRole("progressbar", { name: /impacto económico/i });
    expect(highItemBar.getAttribute("aria-valuenow")).toBe("5");
  });

  it("renders the anomalias list with a severity Badge and tipo/descripcion", () => {
    render(<ExplicabilidadDrawer item={baseItem} onClose={vi.fn()} />);

    expect(screen.getByText("Patrón Irregular")).toBeInTheDocument();
    expect(screen.getByText("Crítica")).toHaveAttribute("data-tone", "critical");
    expect(screen.getByText("Score de anomalía: 97/100")).toBeInTheDocument();
  });

  it("shows a placeholder message when there are no anomalias", () => {
    render(<ExplicabilidadDrawer item={{ ...baseItem, anomalias: [] }} onClose={vi.fn()} />);

    expect(screen.getByText("Sin anomalías registradas.")).toBeInTheDocument();
  });

  it("shows a placeholder message when there are no observaciones", () => {
    render(<ExplicabilidadDrawer item={{ ...baseItem, observaciones: [] }} onClose={vi.fn()} />);

    expect(screen.getByText("Sin factores registrados.")).toBeInTheDocument();
  });
});
