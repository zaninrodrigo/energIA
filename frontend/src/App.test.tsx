import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import { createTestQueryClient } from "./test/test-utils";
import { rankingFixture, suministrosFixture } from "./test/fixtures";
import App from "./App";

function renderApp(initialEntries: string[] = ["/"]) {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("App", () => {
  it("renders the EnergIA wordmark and the primary nav links", () => {
    renderApp();

    expect(screen.getByText("EnergIA")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: /navegación principal/i });
    expect(nav).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Suministros" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ranking de Riesgo" })).toBeInTheDocument();
  });

  it("redirects from / to the Ranking de Riesgo screen, marking it active in the nav", async () => {
    renderApp(["/"]);

    await waitFor(() =>
      expect(screen.getByText(rankingFixture.items[0].numero_suministro)).toBeInTheDocument(),
    );
    expect(screen.getByRole("link", { name: "Ranking de Riesgo" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("renders the Suministros screen at /suministros, marking it active in the nav", async () => {
    renderApp(["/suministros"]);

    await waitFor(() =>
      expect(screen.getByText(suministrosFixture.items[0].numero_suministro)).toBeInTheDocument(),
    );
    expect(screen.getByRole("link", { name: "Suministros" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});
