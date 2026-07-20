import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Button } from "./Button";

describe("Button", () => {
  it("renders its children as the accessible name", () => {
    render(<Button>Aplicar filtro</Button>);

    expect(screen.getByRole("button", { name: "Aplicar filtro" })).toBeInTheDocument();
  });

  it("defaults to type=button so it never submits an enclosing form by accident", () => {
    render(<Button>Aplicar filtro</Button>);

    expect(screen.getByRole("button")).toHaveAttribute("type", "button");
  });

  it("calls onClick when clicked", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Aplicar filtro</Button>);

    await user.click(screen.getByRole("button"));

    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("does not call onClick when disabled", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(
      <Button onClick={onClick} disabled>
        Aplicar filtro
      </Button>,
    );

    await user.click(screen.getByRole("button"));

    expect(onClick).not.toHaveBeenCalled();
  });

  it("defaults to the primary variant and md size classes", () => {
    render(<Button>Aplicar filtro</Button>);

    const button = screen.getByRole("button");
    expect(button).toHaveClass("bg-brand");
    expect(button).toHaveClass("text-sm");
  });

  it("applies the secondary variant classes", () => {
    render(<Button variant="secondary">Cancelar</Button>);

    expect(screen.getByRole("button")).toHaveClass("bg-white");
  });

  it("applies the sm size classes", () => {
    render(<Button size="sm">Cancelar</Button>);

    expect(screen.getByRole("button")).toHaveClass("text-xs");
  });
});
