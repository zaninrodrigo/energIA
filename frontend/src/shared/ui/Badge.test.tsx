import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Badge } from "./Badge";
import type { RiskTone } from "./Badge";

describe("Badge", () => {
  it("renders its label text", () => {
    render(<Badge tone="high">Alto</Badge>);

    expect(screen.getByText("Alto")).toBeInTheDocument();
  });

  it.each<[RiskTone, string, string]>([
    ["very-low", "bg-risk-very-low", "text-risk-very-low-fg"],
    ["low", "bg-risk-low", "text-risk-low-fg"],
    ["medium", "bg-risk-medium", "text-risk-medium-fg"],
    ["high", "bg-risk-high", "text-risk-high-fg"],
    ["critical", "bg-risk-critical", "text-risk-critical-fg"],
  ])("applies the %s risk tone's background and text classes", (tone, bgClass, fgClass) => {
    render(<Badge tone={tone}>Etiqueta</Badge>);

    const badge = screen.getByText("Etiqueta");
    expect(badge).toHaveClass(bgClass);
    expect(badge).toHaveClass(fgClass);
  });

  it("exposes the tone via a data attribute for styling/testing hooks", () => {
    render(<Badge tone="critical">Crítico</Badge>);

    expect(screen.getByText("Crítico")).toHaveAttribute("data-tone", "critical");
  });
});
