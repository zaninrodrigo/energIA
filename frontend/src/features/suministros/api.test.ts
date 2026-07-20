import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "../../test/msw/server";
import { suministrosFixture } from "../../test/fixtures";
import { getSuministros } from "./api";

describe("getSuministros", () => {
  it("returns the page from the API for the given limit/offset", async () => {
    const result = await getSuministros({ limit: 50, offset: 0 });

    expect(result).toEqual(suministrosFixture);
  });

  it("sends numero_cliente as a query param when provided", async () => {
    let capturedUrl: URL | undefined;
    server.use(
      http.get("http://localhost:8000/api/v1/suministros", ({ request }) => {
        capturedUrl = new URL(request.url);
        return HttpResponse.json(suministrosFixture);
      }),
    );

    await getSuministros({ limit: 50, offset: 0, numeroCliente: "CLI-001" });

    expect(capturedUrl?.searchParams.get("numero_cliente")).toBe("CLI-001");
  });

  it("omits numero_cliente from the query when not provided", async () => {
    let capturedUrl: URL | undefined;
    server.use(
      http.get("http://localhost:8000/api/v1/suministros", ({ request }) => {
        capturedUrl = new URL(request.url);
        return HttpResponse.json(suministrosFixture);
      }),
    );

    await getSuministros({ limit: 50, offset: 0 });

    expect(capturedUrl?.searchParams.has("numero_cliente")).toBe(false);
  });
});
