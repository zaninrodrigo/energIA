import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Drawer } from "./Drawer";

describe("Drawer", () => {
  it("renders nothing when isOpen is false", () => {
    render(
      <Drawer isOpen={false} onClose={vi.fn()} title="Detalle">
        <p>Contenido</p>
      </Drawer>,
    );

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders the title and children when isOpen is true", () => {
    render(
      <Drawer isOpen onClose={vi.fn()} title="Detalle del suministro">
        <p>Contenido del panel</p>
      </Drawer>,
    );

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Detalle del suministro")).toBeInTheDocument();
    expect(screen.getByText("Contenido del panel")).toBeInTheDocument();
  });

  it("calls onClose when Escape is pressed", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Drawer isOpen onClose={onClose} title="Detalle">
        <p>Contenido</p>
      </Drawer>,
    );

    await user.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when the overlay is clicked, but not when the panel itself is clicked", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Drawer isOpen onClose={onClose} title="Detalle">
        <p>Contenido</p>
      </Drawer>,
    );

    await user.click(screen.getByText("Contenido"));
    expect(onClose).not.toHaveBeenCalled();

    await user.click(screen.getByTestId("drawer-overlay"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when the close button is clicked", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Drawer isOpen onClose={onClose} title="Detalle">
        <p>Contenido</p>
      </Drawer>,
    );

    await user.click(screen.getByRole("button", { name: /cerrar/i }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("moves focus into the dialog when it opens", () => {
    render(
      <Drawer isOpen onClose={vi.fn()} title="Detalle">
        <p>Contenido</p>
      </Drawer>,
    );

    expect(screen.getByRole("dialog")).toHaveFocus();
  });

  it("renders without a heading when no title is given, but keeps the dialog usable", () => {
    render(
      <Drawer isOpen onClose={vi.fn()}>
        <p>Contenido</p>
      </Drawer>,
    );

    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("wraps focus from the last focusable element back to the first when Tab is pressed", async () => {
    const user = userEvent.setup();
    render(
      <Drawer isOpen onClose={vi.fn()} title="Detalle">
        <button type="button">Acción</button>
      </Drawer>,
    );
    const closeButton = screen.getByRole("button", { name: /cerrar/i });
    const actionButton = screen.getByRole("button", { name: "Acción" });
    actionButton.focus();
    expect(actionButton).toHaveFocus();

    await user.tab();

    expect(closeButton).toHaveFocus();
  });

  it("wraps focus from the first focusable element back to the last when Shift+Tab is pressed", async () => {
    const user = userEvent.setup();
    render(
      <Drawer isOpen onClose={vi.fn()} title="Detalle">
        <button type="button">Acción</button>
      </Drawer>,
    );
    const closeButton = screen.getByRole("button", { name: /cerrar/i });
    const actionButton = screen.getByRole("button", { name: "Acción" });
    closeButton.focus();
    expect(closeButton).toHaveFocus();

    await user.tab({ shift: true });

    expect(actionButton).toHaveFocus();
  });

  it("returns focus to the previously focused element when it closes", async () => {
    const user = userEvent.setup();

    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <div>
          <button type="button" onClick={() => setOpen(true)}>
            Ver detalle
          </button>
          <Drawer isOpen={open} onClose={() => setOpen(false)} title="Detalle">
            <p>Contenido</p>
          </Drawer>
        </div>
      );
    }

    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "Ver detalle" });

    await user.click(trigger);
    expect(screen.getByRole("dialog")).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(trigger).toHaveFocus();
  });
});
