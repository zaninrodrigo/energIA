import type { Page } from "../../shared/api/types";

/**
 * Mirrors `SuministroSchema` in
 * backend/src/energia/contexts/suministros/presentation/schemas.py exactly (hand-written, no
 * codegen -- see README.md).
 *
 * `cliente_id` and `categoria_tarifaria_id` are raw UUIDs: there is no name-resolution endpoint
 * yet (see README.md, "Deuda conocida", and PROJECT_MASTER_SPEC.md).
 */
export interface Suministro {
  id: string;
  numero_suministro: string;
  cliente_id: string;
  categoria_tarifaria_id: string;
  localidad: string | null;
  barrio: string | null;
  estado: string;
  fecha_alta: string;
}

export type SuministrosPage = Page<Suministro>;
