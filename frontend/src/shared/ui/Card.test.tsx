import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Card } from "./Card";

describe("Card", () => {
  it("renders its children", () => {
    render(
      <Card>
        <p>Contenido</p>
      </Card>,
    );

    expect(screen.getByText("Contenido")).toBeInTheDocument();
  });

  it("merges an extra className with its base styles", () => {
    render(
      <Card className="extra-class">
        <p>Contenido</p>
      </Card>,
    );

    expect(screen.getByText("Contenido").parentElement).toHaveClass("extra-class");
  });
});
