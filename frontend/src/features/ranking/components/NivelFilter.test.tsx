import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NivelFilter } from "./NivelFilter";

describe("NivelFilter", () => {
  it("renders an 'Todos' option plus the 5 ire_nivel bands", () => {
    render(<NivelFilter selected={undefined} onChange={vi.fn()} />);

    expect(screen.getByRole("option", { name: "Todos" })).toBeInTheDocument();
    for (const nivel of ["Muy Bajo", "Bajo", "Medio", "Alto", "Crítico"]) {
      expect(screen.getByRole("option", { name: nivel })).toBeInTheDocument();
    }
  });

  it("defaults to 'Todos' selected when selected is undefined", () => {
    render(<NivelFilter selected={undefined} onChange={vi.fn()} />);

    expect(screen.getByRole("combobox", { name: /nivel/i })).toHaveValue("");
  });

  it("reflects a given nivel as the control's current value", () => {
    render(<NivelFilter selected="Crítico" onChange={vi.fn()} />);

    expect(screen.getByRole("combobox", { name: /nivel/i })).toHaveValue("Crítico");
  });

  it("calls onChange with the exact backend enum string when a nivel is picked", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<NivelFilter selected={undefined} onChange={onChange} />);

    await user.selectOptions(screen.getByRole("combobox", { name: /nivel/i }), "Crítico");

    expect(onChange).toHaveBeenCalledWith("Crítico");
  });

  it("calls onChange with undefined when 'Todos' is picked again", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<NivelFilter selected="Alto" onChange={onChange} />);

    await user.selectOptions(screen.getByRole("combobox", { name: /nivel/i }), "Todos");

    expect(onChange).toHaveBeenCalledWith(undefined);
  });
});
