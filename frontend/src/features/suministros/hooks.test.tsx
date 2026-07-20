import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "../../test/msw/server";
import { suministrosFixture } from "../../test/fixtures";
import { createTestQueryClient } from "../../test/test-utils";
import { useSuministros } from "./hooks";

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = createTestQueryClient();
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe("useSuministros", () => {
  it("resolves with the suministros page for the given params", async () => {
    const { result } = renderHook(() => useSuministros({ limit: 50, offset: 0 }), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(suministrosFixture);
  });

  it("surfaces a failed request as an error state", async () => {
    server.use(
      http.get("http://localhost:8000/api/v1/suministros", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );

    const { result } = renderHook(() => useSuministros({ limit: 50, offset: 0 }), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
