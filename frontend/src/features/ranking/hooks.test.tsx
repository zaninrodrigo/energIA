import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../../test/msw/server";
import { lotesFixture, rankingFixture } from "../../test/fixtures";
import { createTestQueryClient } from "../../test/test-utils";
import { useLotes, useRanking } from "./hooks";

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = createTestQueryClient();
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe("useLotes", () => {
  it("resolves with the lotes page for the given params", async () => {
    const { result } = renderHook(() => useLotes({ limit: 50, offset: 0, estado: "Procesado" }), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(lotesFixture);
  });

  it("surfaces a failed request as an error state", async () => {
    server.use(
      http.get("http://localhost:8000/api/v1/lotes", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );

    const { result } = renderHook(() => useLotes({ limit: 50, offset: 0 }), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

describe("useRanking", () => {
  it("resolves with the ranking page (items + resumen) for a selected lote", async () => {
    const { result } = renderHook(
      () => useRanking({ codigoLote: "LOTE-SYN-S42-2022-07", limit: 50, offset: 0 }),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(rankingFixture);
  });

  it("surfaces a failed request as an error state", async () => {
    server.use(
      http.get("http://localhost:8000/api/v1/motor/lotes/:codigoLote/resultados", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );

    const { result } = renderHook(
      () => useRanking({ codigoLote: "LOTE-SYN-S42-2022-07", limit: 50, offset: 0 }),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  it("stays disabled (never fetches) when codigoLote is the empty string -- no lote selected yet", async () => {
    let requestCount = 0;
    server.use(
      http.get("http://localhost:8000/api/v1/motor/lotes/:codigoLote/resultados", () => {
        requestCount += 1;
        return HttpResponse.json(rankingFixture);
      }),
    );

    const { result } = renderHook(() => useRanking({ codigoLote: "", limit: 50, offset: 0 }), {
      wrapper,
    });

    expect(result.current.isPending).toBe(true);
    expect(result.current.fetchStatus).toBe("idle");
    expect(requestCount).toBe(0);
  });
});
