import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Pagination } from "./Pagination";

describe("Pagination", () => {
  it("shows the current range and total", () => {
    render(<Pagination total={120} limit={50} offset={50} onPrevious={vi.fn()} onNext={vi.fn()} />);

    expect(screen.getByText("Mostrando 51–100 de 120")).toBeInTheDocument();
  });

  it("shows a zero-range message when there are no results", () => {
    render(<Pagination total={0} limit={50} offset={0} onPrevious={vi.fn()} onNext={vi.fn()} />);

    expect(screen.getByText("Mostrando 0 de 0")).toBeInTheDocument();
  });

  it("disables 'Anterior' on the first page", () => {
    render(<Pagination total={120} limit={50} offset={0} onPrevious={vi.fn()} onNext={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Anterior" })).toBeDisabled();
  });

  it("calls onPrevious when 'Anterior' is clicked past the first page", async () => {
    const user = userEvent.setup();
    const onPrevious = vi.fn();
    render(
      <Pagination total={120} limit={50} offset={50} onPrevious={onPrevious} onNext={vi.fn()} />,
    );

    await user.click(screen.getByRole("button", { name: "Anterior" }));

    expect(onPrevious).toHaveBeenCalledTimes(1);
  });

  it("disables 'Siguiente' on the last page", () => {
    render(
      <Pagination total={120} limit={50} offset={100} onPrevious={vi.fn()} onNext={vi.fn()} />,
    );

    expect(screen.getByRole("button", { name: "Siguiente" })).toBeDisabled();
  });

  it("calls onNext when 'Siguiente' is clicked before the last page", async () => {
    const user = userEvent.setup();
    const onNext = vi.fn();
    render(<Pagination total={120} limit={50} offset={50} onPrevious={vi.fn()} onNext={onNext} />);

    await user.click(screen.getByRole("button", { name: "Siguiente" }));

    expect(onNext).toHaveBeenCalledTimes(1);
  });
});
