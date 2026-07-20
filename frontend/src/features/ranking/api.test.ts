import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "../../test/msw/server";
import { lotesFixture, rankingFixture } from "../../test/fixtures";
import { getLotes, getRanking } from "./api";

describe("getLotes", () => {
  it("returns the page from the API for the given limit/offset/estado", async () => {
    const result = await getLotes({ limit: 50, offset: 0, estado: "Procesado" });

    expect(result).toEqual(lotesFixture);
  });

  it("sends estado as a query param when provided", async () => {
    let capturedUrl: URL | undefined;
    server.use(
      http.get("http://localhost:8000/api/v1/lotes", ({ request }) => {
        capturedUrl = new URL(request.url);
        return HttpResponse.json(lotesFixture);
      }),
    );

    await getLotes({ limit: 50, offset: 0, estado: "Procesado" });

    expect(capturedUrl?.searchParams.get("estado")).toBe("Procesado");
  });

  it("omits estado from the query when not provided", async () => {
    let capturedUrl: URL | undefined;
    server.use(
      http.get("http://localhost:8000/api/v1/lotes", ({ request }) => {
        capturedUrl = new URL(request.url);
        return HttpResponse.json(lotesFixture);
      }),
    );

    await getLotes({ limit: 50, offset: 0 });

    expect(capturedUrl?.searchParams.has("estado")).toBe(false);
  });
});

describe("getRanking", () => {
  it("returns the page (items + resumen) from the API for the given lote/limit/offset", async () => {
    const result = await getRanking({ codigoLote: "LOTE-SYN-S42-2022-07", limit: 1, offset: 0 });

    expect(result).toEqual(rankingFixture);
  });

  it("interpolates codigoLote into the path", async () => {
    let capturedPath: string | undefined;
    server.use(
      http.get("http://localhost:8000/api/v1/motor/lotes/:codigoLote/resultados", ({ request }) => {
        capturedPath = new URL(request.url).pathname;
        return HttpResponse.json(rankingFixture);
      }),
    );

    await getRanking({ codigoLote: "LOTE-SYN-S42-2022-07", limit: 50, offset: 0 });

    expect(capturedPath).toBe("/api/v1/motor/lotes/LOTE-SYN-S42-2022-07/resultados");
  });

  it("sends nivel as a query param, URL-encoded, when provided -- including accented values like 'Crítico'", async () => {
    let capturedUrl: URL | undefined;
    server.use(
      http.get("http://localhost:8000/api/v1/motor/lotes/:codigoLote/resultados", ({ request }) => {
        capturedUrl = new URL(request.url);
        return HttpResponse.json(rankingFixture);
      }),
    );

    await getRanking({ codigoLote: "LOTE-SYN-S42-2022-07", limit: 50, offset: 0, nivel: "Crítico" });

    // URLSearchParams.get() decodes back to the original string -- this asserts the exact
    // backend enum literal was sent (not, e.g., a stripped-accent "Critico" typo), and that the
    // raw request line was actually percent-encoded (never a literal "í" byte on the wire).
    expect(capturedUrl?.searchParams.get("nivel")).toBe("Crítico");
    expect(capturedUrl?.search).toContain("nivel=Cr%C3%ADtico");
  });

  it("omits nivel from the query when not provided", async () => {
    let capturedUrl: URL | undefined;
    server.use(
      http.get("http://localhost:8000/api/v1/motor/lotes/:codigoLote/resultados", ({ request }) => {
        capturedUrl = new URL(request.url);
        return HttpResponse.json(rankingFixture);
      }),
    );

    await getRanking({ codigoLote: "LOTE-SYN-S42-2022-07", limit: 50, offset: 0 });

    expect(capturedUrl?.searchParams.has("nivel")).toBe(false);
  });
});
