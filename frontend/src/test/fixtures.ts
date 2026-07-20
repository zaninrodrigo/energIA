import type { SuministrosPage } from "../features/suministros/types";

/**
 * Single source of truth for the `GET /api/v1/suministros` mock payload, shared by:
 *  - `test/msw/handlers.ts` (Node-side MSW handlers used by Vitest component/hook tests).
 *  - `e2e/smoke.spec.ts` (Playwright's own `page.route()` interception for the E2E smoke).
 *
 * Kept dependency-free (no `msw` import) so both consumers can import it cheaply.
 */
export const suministrosFixture: SuministrosPage = {
  items: [
    {
      id: "b1f8c2d0-1111-4a11-8a11-000000000001",
      numero_suministro: "SYN-S42-SUM-00001",
      cliente_id: "b1f8c2d0-2222-4a11-8a11-000000000001",
      categoria_tarifaria_id: "b1f8c2d0-3333-4a11-8a11-000000000001",
      localidad: "Formosa",
      barrio: "Centro",
      estado: "Activo",
      fecha_alta: "2024-01-15",
    },
    {
      id: "b1f8c2d0-1111-4a11-8a11-000000000002",
      numero_suministro: "SYN-S42-SUM-00002",
      cliente_id: "b1f8c2d0-2222-4a11-8a11-000000000002",
      categoria_tarifaria_id: "b1f8c2d0-3333-4a11-8a11-000000000002",
      localidad: null,
      barrio: null,
      estado: "Activo",
      fecha_alta: "2024-02-10",
    },
  ],
  total: 2,
  limit: 50,
  offset: 0,
};
