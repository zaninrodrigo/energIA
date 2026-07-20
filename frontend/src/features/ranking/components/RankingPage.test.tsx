import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "../../../test/msw/server";
import { lotesFixture, rankingFixture } from "../../../test/fixtures";
import { renderWithProviders } from "../../../test/test-utils";
import { RankingPage } from "./RankingPage";

describe("RankingPage", () => {
  it("shows a loading state before the lotes query resolves", () => {
    renderWithProviders(<RankingPage />);

    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("shows the error state when the lotes query fails", async () => {
    server.use(
      http.get("http://localhost:8000/api/v1/lotes", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );

    renderWithProviders(<RankingPage />);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });

  it("shows an empty state when there are no Procesado lotes", async () => {
    server.use(
      http.get("http://localhost:8000/api/v1/lotes", () =>
        HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 }),
      ),
    );

    renderWithProviders(<RankingPage />);

    await waitFor(() =>
      expect(screen.getByText("No hay lotes procesados todavía.")).toBeInTheDocument(),
    );
  });

  it("defaults the lote selector to the most recent Procesado lote and loads its ranking", async () => {
    renderWithProviders(<RankingPage />);

    await waitFor(() =>
      expect(screen.getByText(rankingFixture.items[0].numero_suministro)).toBeInTheDocument(),
    );

    expect(screen.getByRole("combobox", { name: /lote/i })).toHaveValue(
      lotesFixture.items[0].codigo_lote,
    );
  });

  it("requests the ranking for whichever lote is selected", async () => {
    let requestedCodigoLote: string | undefined;
    server.use(
      http.get("http://localhost:8000/api/v1/motor/lotes/:codigoLote/resultados", ({ params }) => {
        requestedCodigoLote = params.codigoLote as string;
        return HttpResponse.json(rankingFixture);
      }),
    );

    renderWithProviders(<RankingPage />);

    await waitFor(() => expect(requestedCodigoLote).toBe(lotesFixture.items[0].codigo_lote));
  });

  it("renders the KPI summary row from resumen", async () => {
    renderWithProviders(<RankingPage />);

    await waitFor(() => expect(screen.getByText("Total analizados")).toBeInTheDocument());
    expect(screen.getByText(String(rankingFixture.resumen.total_resultados))).toBeInTheDocument();
  });

  it("keeps the KPI summary row unchanged when the nivel filter is applied -- resumen is always unfiltered", async () => {
    let lastRequestedNivel: string | null = null;
    server.use(
      http.get("http://localhost:8000/api/v1/motor/lotes/:codigoLote/resultados", ({ request }) => {
        lastRequestedNivel = new URL(request.url).searchParams.get("nivel");
        return HttpResponse.json({
          ...rankingFixture,
          items: lastRequestedNivel === "Crítico" ? [] : rankingFixture.items,
        });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<RankingPage />);

    await waitFor(() => expect(screen.getByText("Total analizados")).toBeInTheDocument());
    expect(screen.getByText(String(rankingFixture.resumen.total_resultados))).toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: /nivel/i }), "Crítico");

    await waitFor(() => expect(lastRequestedNivel).toBe("Crítico"));
    expect(screen.getByText(String(rankingFixture.resumen.total_resultados))).toBeInTheDocument();
  });

  it("sends the exact backend enum string for the nivel filter, URL-encoded", async () => {
    let capturedUrl: URL | undefined;
    server.use(
      http.get("http://localhost:8000/api/v1/motor/lotes/:codigoLote/resultados", ({ request }) => {
        capturedUrl = new URL(request.url);
        return HttpResponse.json(rankingFixture);
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<RankingPage />);

    await waitFor(() =>
      expect(screen.getByText(rankingFixture.items[0].numero_suministro)).toBeInTheDocument(),
    );

    await user.selectOptions(screen.getByRole("combobox", { name: /nivel/i }), "Crítico");

    await waitFor(() => expect(capturedUrl?.searchParams.get("nivel")).toBe("Crítico"));
    expect(capturedUrl?.search).toContain("nivel=Cr%C3%ADtico");
  });

  it("opens the explicability drawer with the selected item's data when 'Ver detalle' is clicked", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RankingPage />);

    await waitFor(() =>
      expect(screen.getByText(rankingFixture.items[0].numero_suministro)).toBeInTheDocument(),
    );

    await user.click(screen.getByRole("button", { name: "Ver detalle" }));

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(within(screen.getByRole("dialog")).getByText(/SYN-S42-SUM-00070/)).toBeInTheDocument();
  });

  it("closes the drawer when its close button is clicked", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RankingPage />);

    await waitFor(() =>
      expect(screen.getByText(rankingFixture.items[0].numero_suministro)).toBeInTheDocument(),
    );
    await user.click(screen.getByRole("button", { name: "Ver detalle" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /cerrar/i }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows the ranking's own error state when the ranking request fails, while keeping the lote selector usable", async () => {
    server.use(
      http.get("http://localhost:8000/api/v1/motor/lotes/:codigoLote/resultados", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );

    renderWithProviders(<RankingPage />);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("combobox", { name: /lote/i })).toBeInTheDocument();
  });

  it("shows the ranking's own empty state when the selected lote has no items for the current filter", async () => {
    server.use(
      http.get("http://localhost:8000/api/v1/motor/lotes/:codigoLote/resultados", () =>
        HttpResponse.json({ ...rankingFixture, items: [], total: 0 }),
      ),
    );

    renderWithProviders(<RankingPage />);

    await waitFor(() =>
      expect(screen.getByText("No se encontraron resultados.")).toBeInTheDocument(),
    );
  });

  it("moves to the next/previous page when the pagination buttons are clicked", async () => {
    server.use(
      http.get("http://localhost:8000/api/v1/motor/lotes/:codigoLote/resultados", ({ request }) => {
        const offset = Number(new URL(request.url).searchParams.get("offset") ?? 0);
        return HttpResponse.json({
          ...rankingFixture,
          items: [{ ...rankingFixture.items[0], numero_suministro: `SUM-${offset}` }],
          total: 120,
          limit: 50,
          offset,
        });
      }),
    );

    const user = userEvent.setup();
    renderWithProviders(<RankingPage />);

    await waitFor(() => expect(screen.getByText("SUM-0")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Siguiente" }));
    await waitFor(() => expect(screen.getByText("SUM-50")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Anterior" }));
    await waitFor(() => expect(screen.getByText("SUM-0")).toBeInTheDocument());
  });
});
