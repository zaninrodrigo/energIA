import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "../../../test/msw/server";
import { suministrosFixture } from "../../../test/fixtures";
import { renderWithProviders } from "../../../test/test-utils";
import { SuministrosPage } from "./SuministrosPage";

describe("SuministrosPage", () => {
  it("shows a loading state before the query resolves", () => {
    renderWithProviders(<SuministrosPage />);

    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("renders the suministros table and pagination once the query resolves", async () => {
    renderWithProviders(<SuministrosPage />);

    await waitFor(() =>
      expect(screen.getByText(suministrosFixture.items[0].numero_suministro)).toBeInTheDocument(),
    );

    expect(
      screen.getByText(
        `Mostrando 1–${suministrosFixture.items.length} de ${suministrosFixture.total}`,
      ),
    ).toBeInTheDocument();
  });

  it("renders the empty state when the page has no items", async () => {
    server.use(
      http.get("http://localhost:8000/api/v1/suministros", () =>
        HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 }),
      ),
    );

    renderWithProviders(<SuministrosPage />);

    await waitFor(() =>
      expect(screen.getByText("No se encontraron suministros.")).toBeInTheDocument(),
    );
  });

  it("renders the error state when the request fails", async () => {
    server.use(
      http.get("http://localhost:8000/api/v1/suministros", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );

    renderWithProviders(<SuministrosPage />);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });

  it("moves to the next/previous page when the pagination buttons are clicked", async () => {
    server.use(
      http.get("http://localhost:8000/api/v1/suministros", ({ request }) => {
        const offset = Number(new URL(request.url).searchParams.get("offset") ?? 0);
        return HttpResponse.json({
          items: [{ ...suministrosFixture.items[0], numero_suministro: `SUM-${offset}` }],
          total: 120,
          limit: 50,
          offset,
        });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<SuministrosPage />);

    await waitFor(() => expect(screen.getByText("SUM-0")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Siguiente" }));
    await waitFor(() => expect(screen.getByText("SUM-50")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Anterior" }));
    await waitFor(() => expect(screen.getByText("SUM-0")).toBeInTheDocument());
  });
});
