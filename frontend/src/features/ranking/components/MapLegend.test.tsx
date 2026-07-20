import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MapLegend } from "./MapLegend";
import { NIVELES_IRE } from "../types";

describe("MapLegend", () => {
  it("lists every IRE nivel as a labelled swatch", () => {
    render(<MapLegend />);
    for (const nivel of NIVELES_IRE) {
      expect(screen.getByText(nivel)).toBeInTheDocument();
    }
  });
});
