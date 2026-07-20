import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { renderWithProviders } from "../../../test/test-utils";
import { BarriosPage } from "./BarriosPage";

describe("BarriosPage", () => {
  it("defaults to the first localidad and shows its barrios on the map and table", async () => {
    renderWithProviders(<BarriosPage />);

    // Default localidad is the first the backend returns (El Colorado, worst-risk first).
    expect(await screen.findByDisplayValue("El Colorado")).toBeInTheDocument();
    expect(screen.getByRole("figure", { name: /mapa de barrios/i })).toBeInTheDocument();
    // A barrio of El Colorado is listed; Formosa's "Obrero" is not (filtered out).
    expect(screen.getByText("Centro")).toBeInTheDocument();
    expect(screen.queryByText("Obrero")).not.toBeInTheDocument();
  });

  it("switches the shown barrios when another localidad is selected", async () => {
    const user = userEvent.setup();
    renderWithProviders(<BarriosPage />);

    await screen.findByDisplayValue("El Colorado");
    await user.selectOptions(screen.getByRole("combobox", { name: "Localidad" }), "Formosa");

    await waitFor(() => expect(screen.getByText("Obrero")).toBeInTheDocument());
    expect(screen.queryByText("Villa Hermosa")).not.toBeInTheDocument();
  });
});
