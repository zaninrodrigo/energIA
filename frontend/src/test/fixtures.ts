import type { SuministrosPage } from "../features/suministros/types";
import type { LotesPage, ResultadosRankingPage } from "../features/ranking/types";
import type { BarriosRiesgoPage } from "../features/barrios/types";

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

/**
 * `GET /api/v1/lotes?estado=Procesado` mock payload -- ordered by `fecha_importacion desc`
 * (`consumos/presentation/routes.py`'s `list_lotes`), so `items[0]` is the most recent one, the
 * lote selector's default (`features/ranking/loteSelection.ts`).
 */
export const lotesFixture: LotesPage = {
  items: [
    {
      id: "c2f8c2d0-1111-4a11-8a11-000000000002",
      codigo_lote: "LOTE-SYN-S42-2022-07",
      nombre: "Julio 2022",
      fecha_importacion: "2022-08-01T00:00:00Z",
      cantidad_registros: 94,
      estado: "Procesado",
    },
    {
      id: "c2f8c2d0-1111-4a11-8a11-000000000001",
      codigo_lote: "LOTE-SYN-S42-2022-06",
      nombre: "Junio 2022",
      fecha_importacion: "2022-07-01T00:00:00Z",
      cantidad_registros: 90,
      estado: "Procesado",
    },
  ],
  total: 2,
  limit: 50,
  offset: 0,
};

/**
 * `GET /api/v1/motor/lotes/{codigo_lote}/resultados` mock payload -- values taken from the real
 * example in `docs/03-architecture/API_SPEC.md` (`LOTE-SYN-S42-2022-07`, `?limit=1`) so fixtures
 * and documented contract never drift apart.
 */
export const rankingFixture: ResultadosRankingPage = {
  items: [
    {
      suministro_id: "a1efd6d0-1111-4a11-8a11-000000000070",
      numero_suministro: "SYN-S42-SUM-00070",
      rutafolio: "90000000070",
      latitud: -26.1189,
      longitud: -58.1731,
      ire_valor: 70,
      ire_nivel: "Alto",
      clasificacion: "Alto Riesgo",
      score_anomalia: -0.034,
      probabilidad: 0.9696,
      localidad: "Formosa",
      categoria_tarifaria: "Industrial",
      anomalias: [
        {
          tipo: "Patrón Irregular",
          severidad: "Crítica",
          descripcion: "Score de anomalía del modelo global: 97/100 (aproximación por atribución de features)",
        },
      ],
      observaciones: [
        {
          factor: "score_ia",
          contribution: 31.6189,
          reason: "Score del modelo de IA: 97.0/100 (aproximación por atribución de features)",
        },
        {
          factor: "historial_consumos",
          contribution: 4.2354,
          reason: "Desviación de -1.30 desvíos estándar respecto de la línea base histórica",
        },
        {
          factor: "persistencia_anomalias",
          contribution: 16.3043,
          reason: "5 anomalía(s) previa(s) registrada(s) y racha actual de 0 período(s) consecutivos en cero consumo",
        },
        {
          factor: "variacion_porcentual",
          contribution: 0.172,
          reason: "Variación de -3% respecto de el período anterior",
        },
        {
          factor: "impacto_economico",
          contribution: 10.8696,
          reason: "Impacto económico estimado: 1067.3 kWh no facturados",
        },
        {
          factor: "consumo_promedio",
          contribution: 4.1818,
          reason: "Consumo promedio de 8886.4 kWh (percentil 95 respecto del lote)",
        },
        {
          factor: "categoria_tarifaria",
          contribution: 2.9348,
          reason: "Categoría tarifaria Industrial (criticidad 90/100)",
        },
      ],
      iee_kwh: 1067.34,
    },
  ],
  total: 94,
  limit: 1,
  offset: 0,
  resumen: {
    total_resultados: 94,
    conteo_por_nivel: { "Muy Bajo": 70, Bajo: 17, Medio: 2, Alto: 5, Crítico: 0 },
    conteo_por_clasificacion: { Normal: 70, Atención: 17, "Alto Riesgo": 7, Crítico: 0 },
    con_anomalias: 9,
    suma_iee_kwh: 16181.36,
  },
};

/**
 * `GET /api/v1/motor/lotes/{codigo_lote}/barrios` mock payload -- two localidades so the localidad
 * selector has something to switch between; barrios ordered worst-IRE-max first, matching the
 * backend contract.
 */
export const barriosFixture: BarriosRiesgoPage = {
  items: [
    {
      localidad: "El Colorado",
      barrio: "Centro",
      total_medidores: 10,
      ire_promedio: 24,
      ire_maximo: 62,
      nivel: "Alto",
      con_anomalias: 1,
      latitud: -26.28,
      longitud: -59.36,
    },
    {
      localidad: "El Colorado",
      barrio: "Villa Hermosa",
      total_medidores: 5,
      ire_promedio: 15,
      ire_maximo: 33,
      nivel: "Bajo",
      con_anomalias: 0,
      latitud: -26.33,
      longitud: -59.34,
    },
    {
      localidad: "Formosa",
      barrio: "Obrero",
      total_medidores: 2,
      ire_promedio: 29,
      ire_maximo: 42,
      nivel: "Medio",
      con_anomalias: 1,
      latitud: -26.17,
      longitud: -58.19,
    },
  ],
};
