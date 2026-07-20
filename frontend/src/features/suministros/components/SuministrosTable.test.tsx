import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { SuministrosTable } from "./SuministrosTable";
import type { Suministro } from "../types";

const items: Suministro[] = [
  {
    id: "1",
    numero_suministro: "SYN-S42-SUM-00001",
    cliente_id: "c1",
    categoria_tarifaria_id: "cat-1",
    localidad: "Formosa",
    barrio: "Centro",
    estado: "Activo",
    fecha_alta: "2024-01-15",
  },
  {
    id: "2",
    numero_suministro: "SYN-S42-SUM-00002",
    cliente_id: "c2",
    categoria_tarifaria_id: "cat-2",
    localidad: null,
    barrio: null,
    estado: "Activo",
    fecha_alta: "2024-02-10",
  },
];

describe("SuministrosTable", () => {
  it("renders the expected column headers", () => {
    render(<SuministrosTable items={items} />);

    expect(screen.getByRole("columnheader", { name: "Número de suministro" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Categoría" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Localidad" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Estado" })).toBeInTheDocument();
  });

  it("renders numero_suministro and the raw categoria_tarifaria_id (no name-resolution endpoint yet)", () => {
    render(<SuministrosTable items={items} />);

    expect(screen.getByText("SYN-S42-SUM-00001")).toBeInTheDocument();
    expect(screen.getByText("cat-1")).toBeInTheDocument();
  });

  it("renders a dash placeholder when localidad is null", () => {
    render(<SuministrosTable items={items} />);

    expect(screen.getAllByText("—")).toHaveLength(1);
  });
});
