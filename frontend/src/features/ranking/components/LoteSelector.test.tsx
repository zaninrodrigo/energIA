import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LoteSelector } from "./LoteSelector";
import type { Lote } from "../types";

const lotes: Lote[] = [
  {
    id: "1",
    codigo_lote: "LOTE-2022-07",
    nombre: "Julio 2022",
    fecha_importacion: "2022-08-01T00:00:00Z",
    cantidad_registros: 94,
    estado: "Procesado",
  },
  {
    id: "2",
    codigo_lote: "LOTE-2022-06",
    nombre: null,
    fecha_importacion: "2022-07-01T00:00:00Z",
    cantidad_registros: 90,
    estado: "Procesado",
  },
];

describe("LoteSelector", () => {
  it("renders one option per lote, using nombre as the label when present", () => {
    render(<LoteSelector lotes={lotes} selected="LOTE-2022-07" onChange={vi.fn()} />);

    expect(screen.getByRole("option", { name: "Julio 2022" })).toBeInTheDocument();
  });

  it("falls back to codigo_lote as the option label when nombre is null", () => {
    render(<LoteSelector lotes={lotes} selected="LOTE-2022-07" onChange={vi.fn()} />);

    expect(screen.getByRole("option", { name: "LOTE-2022-06" })).toBeInTheDocument();
  });

  it("reflects the selected prop as the control's current value", () => {
    render(<LoteSelector lotes={lotes} selected="LOTE-2022-06" onChange={vi.fn()} />);

    expect(screen.getByRole("combobox", { name: /lote/i })).toHaveValue("LOTE-2022-06");
  });

  it("calls onChange with the newly picked codigo_lote", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<LoteSelector lotes={lotes} selected="LOTE-2022-07" onChange={onChange} />);

    await user.selectOptions(screen.getByRole("combobox", { name: /lote/i }), "LOTE-2022-06");

    expect(onChange).toHaveBeenCalledWith("LOTE-2022-06");
  });
});
