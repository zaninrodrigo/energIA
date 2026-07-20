import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "../../test/msw/server";
import { apiGet } from "./client";

describe("apiGet", () => {
  it("resolves with the parsed JSON body when the response is ok", async () => {
    server.use(
      http.get("http://localhost:8000/api/v1/ping", () => HttpResponse.json({ ok: true })),
    );

    const result = await apiGet<{ ok: boolean }>("/api/v1/ping");

    expect(result).toEqual({ ok: true });
  });

  it("serializes defined query params into the request URL, dropping undefined ones", async () => {
    let capturedUrl: URL | undefined;
    server.use(
      http.get("http://localhost:8000/api/v1/ping", ({ request }) => {
        capturedUrl = new URL(request.url);
        return HttpResponse.json({ ok: true });
      }),
    );

    await apiGet("/api/v1/ping", { params: { limit: 50, offset: 0, numero_cliente: undefined } });

    expect(capturedUrl?.searchParams.get("limit")).toBe("50");
    expect(capturedUrl?.searchParams.get("offset")).toBe("0");
    expect(capturedUrl?.searchParams.has("numero_cliente")).toBe(false);
  });

  it("throws an ApiError carrying status, statusText and the parsed body when the response is not ok", async () => {
    server.use(
      http.get("http://localhost:8000/api/v1/ping", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500, statusText: "Internal Server Error" }),
      ),
    );

    await expect(apiGet("/api/v1/ping")).rejects.toMatchObject({
      name: "ApiError",
      status: 500,
      body: { detail: "boom" },
    });
  });

  it("falls back to an undefined body when a non-ok response is not valid JSON", async () => {
    server.use(
      http.get("http://localhost:8000/api/v1/ping", () =>
        HttpResponse.text("not json", { status: 502, statusText: "Bad Gateway" }),
      ),
    );

    await expect(apiGet("/api/v1/ping")).rejects.toMatchObject({
      name: "ApiError",
      status: 502,
      body: undefined,
    });
  });

  it("rejects when the request fails at the network level (backend unreachable)", async () => {
    // A connection-level failure (backend down / DNS failure) surfaces as fetch's native
    // rejection, not an ApiError. This pins the contract the UI relies on: any rejection makes
    // TanStack Query's `isError` true, so `ErrorState` renders regardless of the error's type.
    server.use(http.get("http://localhost:8000/api/v1/ping", () => HttpResponse.error()));

    await expect(apiGet("/api/v1/ping")).rejects.toBeInstanceOf(Error);
  });
});
