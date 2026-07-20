import type { Lote } from "./types";

/**
 * Resolves which `codigo_lote` the lote selector should show as selected.
 *
 * `GET /api/v1/lotes?estado=Procesado` (see `api.ts`) already returns lotes ordered by
 * `fecha_importacion desc` (most recently imported first, `consumos/presentation/routes.py`'s
 * `list_lotes` docstring) -- so "default to the most recent Procesado lote" is simply "the first
 * item of that already-sorted list", never a client-side re-sort.
 *
 * Edge cases this guards against:
 *  - No lotes at all (still loading, or genuinely zero Procesado lotes): no valid selection
 *    exists, regardless of what was selected before -- returns `undefined`.
 *  - A selection already made by the user: preserved as-is, so a manual pick is never silently
 *    overridden by "most recent" on every re-render/refetch.
 *  - A selection that no longer appears in the (possibly refetched) list -- e.g. the previously
 *    selected lote got soft-deleted between polls: falls back to the current most recent one
 *    instead of leaving the selector pointed at a lote no longer being offered.
 */
export function selectDefaultLote(
  lotes: Lote[],
  currentSelection: string | undefined,
): string | undefined {
  if (lotes.length === 0) {
    return undefined;
  }

  if (currentSelection !== undefined && lotes.some((lote) => lote.codigo_lote === currentSelection)) {
    return currentSelection;
  }

  return lotes[0].codigo_lote;
}
