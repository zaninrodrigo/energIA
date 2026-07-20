import { describe, expect, it } from "vitest";
import { selectDefaultLote } from "./loteSelection";
import type { Lote } from "./types";

function buildLote(overrides: Partial<Lote>): Lote {
  return {
    id: "id-1",
    codigo_lote: "LOTE-1",
    nombre: null,
    fecha_importacion: "2026-07-01T00:00:00Z",
    cantidad_registros: 10,
    estado: "Procesado",
    ...overrides,
  };
}

describe("selectDefaultLote", () => {
  it("returns undefined when there are no lotes to select from, regardless of prior selection", () => {
    expect(selectDefaultLote([], undefined)).toBeUndefined();
    expect(selectDefaultLote([], "LOTE-STALE")).toBeUndefined();
  });

  it("defaults to the first lote (most recent Procesado -- the API already sorts desc by fecha_importacion) when nothing is selected yet", () => {
    const lotes = [buildLote({ codigo_lote: "LOTE-MOST-RECENT" }), buildLote({ codigo_lote: "LOTE-OLDER" })];

    expect(selectDefaultLote(lotes, undefined)).toBe("LOTE-MOST-RECENT");
  });

  it("keeps the user's current selection when it is still present in the list", () => {
    const lotes = [buildLote({ codigo_lote: "LOTE-MOST-RECENT" }), buildLote({ codigo_lote: "LOTE-OLDER" })];

    expect(selectDefaultLote(lotes, "LOTE-OLDER")).toBe("LOTE-OLDER");
  });

  it("falls back to the most recent lote when the current selection no longer appears in the list (e.g. a refetch dropped it)", () => {
    const lotes = [buildLote({ codigo_lote: "LOTE-MOST-RECENT" }), buildLote({ codigo_lote: "LOTE-OLDER" })];

    expect(selectDefaultLote(lotes, "LOTE-GONE")).toBe("LOTE-MOST-RECENT");
  });
});
