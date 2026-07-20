import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { EmptyState } from "./EmptyState";

describe("EmptyState", () => {
  it("shows a default Spanish empty message when no message is provided", () => {
    render(<EmptyState />);

    expect(screen.getByText("No se encontraron suministros.")).toBeInTheDocument();
  });

  it("shows a custom message when one is provided", () => {
    render(<EmptyState message="Nada por aquí." />);

    expect(screen.getByText("Nada por aquí.")).toBeInTheDocument();
  });
});
