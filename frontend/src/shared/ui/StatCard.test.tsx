import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatCard } from "./StatCard";
import type { StatCardTone } from "./StatCard";

describe("StatCard", () => {
  it("renders the label and the value", () => {
    render(<StatCard label="Total analizados" value={94} />);

    expect(screen.getByText("Total analizados")).toBeInTheDocument();
    expect(screen.getByText("94")).toBeInTheDocument();
  });

  it("renders an optional icon", () => {
    render(<StatCard label="Con anomalías" value={9} icon={<span data-testid="icon">!</span>} />);

    expect(screen.getByTestId("icon")).toBeInTheDocument();
  });

  it("defaults to a neutral accent when no tone is given", () => {
    render(<StatCard label="IEE total" value="16181.36 kWh" />);

    expect(screen.getByText("IEE total").closest("[data-tone]")).toHaveAttribute(
      "data-tone",
      "neutral",
    );
  });

  it.each<[StatCardTone, string]>([
    ["neutral", "border-l-slate-300"],
    ["very-low", "border-l-risk-very-low-fg"],
    ["low", "border-l-risk-low-fg"],
    ["medium", "border-l-risk-medium-fg"],
    ["high", "border-l-risk-high-fg"],
    ["critical", "border-l-risk-critical-fg"],
  ])("applies the %s tone's accent border", (tone, expectedClass) => {
    render(<StatCard label="Nivel" value={5} tone={tone} />);

    expect(screen.getByText("Nivel").closest("[data-tone]")).toHaveClass(expectedClass);
  });
});
