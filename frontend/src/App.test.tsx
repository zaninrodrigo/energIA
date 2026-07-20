import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import { createTestQueryClient } from "./test/test-utils";
import { suministrosFixture } from "./test/fixtures";
import App from "./App";

function renderApp() {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("App", () => {
  it("renders the page heading and, once resolved, the suministros screen", async () => {
    renderApp();

    expect(screen.getByRole("heading", { name: /suministros/i })).toBeInTheDocument();

    await waitFor(() =>
      expect(screen.getByText(suministrosFixture.items[0].numero_suministro)).toBeInTheDocument(),
    );
  });
});
