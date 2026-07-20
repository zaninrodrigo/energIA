import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { barriosFixture } from "../../../test/fixtures";
import { BarriosTable } from "./BarriosTable";

describe("BarriosTable", () => {
  it("renders each barrio with its meter count and potential (IRE máximo + nivel)", () => {
    render(<BarriosTable barrios={barriosFixture.items} />);
    expect(screen.getByRole("columnheader", { name: "Potencial (IRE máx.)" })).toBeInTheDocument();
    expect(screen.getByText("Centro")).toBeInTheDocument();
    expect(screen.getByText("62")).toBeInTheDocument();
    // The "Alto" badge for the worst barrio.
    expect(screen.getByText("Alto")).toBeInTheDocument();
  });

  it("falls back to 'Sin barrio' for a null barrio name", () => {
    render(
      <BarriosTable
        barrios={[{ ...barriosFixture.items[0], barrio: null }]}
      />,
    );
    expect(screen.getByText("Sin barrio")).toBeInTheDocument();
  });
});
