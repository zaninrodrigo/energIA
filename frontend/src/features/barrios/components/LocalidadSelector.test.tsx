import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { LocalidadSelector } from "./LocalidadSelector";

describe("LocalidadSelector", () => {
  it("renders the localidades and reports a pick", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <LocalidadSelector
        localidades={["El Colorado", "Formosa"]}
        selected="El Colorado"
        onChange={onChange}
      />,
    );

    await user.selectOptions(screen.getByRole("combobox", { name: "Localidad" }), "Formosa");

    expect(onChange).toHaveBeenCalledWith("Formosa");
  });
});
