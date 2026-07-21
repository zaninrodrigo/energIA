import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RankingTable } from "./RankingTable";
import type { ResultadoRankingItem } from "../types";

const items: ResultadoRankingItem[] = [
  {
    suministro_id: "1",
    numero_suministro: "SYN-S42-SUM-00070",
    medidor: "334604",
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
      { tipo: "Patrón Irregular", severidad: "Crítica", descripcion: null },
      { tipo: "Consumo Cero", severidad: "Media", descripcion: null },
    ],
    observaciones: [],
    iee_kwh: 1067.34,
  },
  {
    suministro_id: "2",
    numero_suministro: "SYN-S42-SUM-00071",
    medidor: "26185402",
    latitud: -26.3,
    longitud: -58.05,
    ire_valor: 10,
    ire_nivel: "Muy Bajo",
    clasificacion: "Normal",
    score_anomalia: null,
    probabilidad: null,
    localidad: null,
    categoria_tarifaria: "Residencial",
    anomalias: [],
    observaciones: [],
    iee_kwh: null,
  },
];

describe("RankingTable", () => {
  it("renders the expected column headers", () => {
    render(<RankingTable items={items} onSelect={vi.fn()} />);

    expect(screen.getByRole("columnheader", { name: "Ruta-folio (suministro)" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Medidor" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Localidad" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Categoría tarifaria" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "IRE" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Clasificación" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Anomalías" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "IEE (kWh)" })).toBeInTheDocument();
  });

  it("renders the anomalias count and the iee_kwh value (dash when null)", () => {
    render(<RankingTable items={items} onSelect={vi.fn()} />);

    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("1067.34")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
  });

  it("renders numero_suministro, localidad (dash when null) and categoria_tarifaria", () => {
    render(<RankingTable items={items} onSelect={vi.fn()} />);

    expect(screen.getByText("SYN-S42-SUM-00070")).toBeInTheDocument();
    expect(screen.getByText("334604")).toBeInTheDocument();
    expect(screen.getByText("Formosa")).toBeInTheDocument();
    expect(screen.getByText("Industrial")).toBeInTheDocument();
    // Two dashes: item 2's `localidad` and its `iee_kwh`, both null.
    expect(screen.getAllByText("—")).toHaveLength(2);
  });

  it("renders ire_valor next to a Badge carrying the ire_nivel's risk tone", () => {
    render(<RankingTable items={items} onSelect={vi.fn()} />);

    expect(screen.getByText("70")).toBeInTheDocument();
    expect(screen.getByText("Alto")).toHaveAttribute("data-tone", "high");
    expect(screen.getByText("Muy Bajo")).toHaveAttribute("data-tone", "very-low");
  });

  it("renders a Badge carrying the clasificacion's risk tone", () => {
    render(<RankingTable items={items} onSelect={vi.fn()} />);

    expect(screen.getByText("Alto Riesgo")).toHaveAttribute("data-tone", "high");
    expect(screen.getByText("Normal")).toHaveAttribute("data-tone", "very-low");
  });

  it("calls onSelect with the row's item when 'Ver detalle' is clicked", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<RankingTable items={items} onSelect={onSelect} />);

    await user.click(screen.getAllByRole("button", { name: "Ver detalle" })[0]);

    expect(onSelect).toHaveBeenCalledWith(items[0]);
  });
});
