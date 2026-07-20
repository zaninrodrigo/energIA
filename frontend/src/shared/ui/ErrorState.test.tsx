import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ErrorState } from "./ErrorState";

describe("ErrorState", () => {
  it("shows a default Spanish error message when no message is provided", () => {
    render(<ErrorState />);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "No se pudieron cargar los suministros. Vuelva a intentarlo.",
    );
  });

  it("shows a custom message when one is provided", () => {
    render(<ErrorState message="Error personalizado." />);

    expect(screen.getByRole("alert")).toHaveTextContent("Error personalizado.");
  });
});
