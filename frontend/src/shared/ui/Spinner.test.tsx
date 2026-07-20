import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Spinner } from "./Spinner";

describe("Spinner", () => {
  it("announces a loading status accessible to assistive technology", () => {
    render(<Spinner />);

    expect(screen.getByRole("status")).toHaveTextContent("Cargando…");
  });
});
