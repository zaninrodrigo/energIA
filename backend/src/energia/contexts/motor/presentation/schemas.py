"""Pydantic response schemas for the `motor` HTTP API.

See docs/03-architecture/API_SPEC.md ("Contexto: Motor de Inteligencia Energética") for the full
contract.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel


class HallazgoSchema(BaseModel):
    check: str
    suministro_id: str
    consumo_id: str
    motivo: str


class ExclusionSchema(BaseModel):
    suministro_id: str
    motivos: list[str]


class InformeValidacionSchema(BaseModel):
    lote_id: str
    total_suministros: int
    suministros_excluidos: int
    fraccion_valida: Decimal
    umbral_cumplido: bool
    hallazgos: list[HallazgoSchema]
    exclusiones: list[ExclusionSchema]


class PeriodoConflictivoSchema(BaseModel):
    consumo_id: str
    fecha_inicio: date
    fecha_fin: date
    lote_id: str
    conflicto_con_consumo_id: str
    conflicto_con_fecha_inicio: date
    conflicto_con_fecha_fin: date
    conflicto_con_lote_id: str


class PeriodosConflictivosSuministroSchema(BaseModel):
    suministro_id: str
    periodos: list[PeriodoConflictivoSchema]


class LecturaNearDuplicateSchema(BaseModel):
    suministro_id: str
    lectura_id: str
    fecha_lectura: date
    lectura_actual: Decimal
    conflicto_con_lectura_id: str
    conflicto_con_fecha_lectura: date


class DriftLoteSchema(BaseModel):
    lote_id: str
    codigo_lote: str
    cantidad_registros: int
    consumos_activos: int
    diferencia: int


class InformeDuplicidadesSchema(BaseModel):
    lote_id: str
    periodos_conflictivos: list[PeriodosConflictivosSuministroSchema]
    lecturas_near_duplicate: list[LecturaNearDuplicateSchema]
    drift_lotes: list[DriftLoteSchema]


class IndicadoresResumenSchema(BaseModel):
    zscore_extremos: int
    iqr_outliers: int
    percentile_extremos: int


class ResumenFeaturesSchema(BaseModel):
    suministros_con_vector: int
    cold_starts: int
    con_periodos_conflictivos: int
    indicadores: IndicadoresResumenSchema


class ReglaHitSchema(BaseModel):
    regla: str
    tipo: str
    severidad: str
    descripcion: str


class ReglasSuministroSchema(BaseModel):
    suministro_id: str
    numero_suministro: str
    hits: list[ReglaHitSchema]


class ResumenReglasSchema(BaseModel):
    suministros_evaluados: int
    suministros_con_hits: int
    hits_por_regla: dict[str, int]


class InformeReglasSchema(BaseModel):
    lote_id: str
    resumen: ResumenReglasSchema
    suministros: list[ReglasSuministroSchema]


class ModeloEntrenadoSchema(BaseModel):
    scope: str
    modelo_ia_id: str
    version: str
    suministros_entrenados: int


class PrediccionResumenSchema(BaseModel):
    suministro_id: str
    numero_suministro: str
    ml_score_0_100: float
    clasificacion: str


class DistribucionScoreSchema(BaseModel):
    minimo: float
    p50: float
    p95: float
    maximo: float


class InformeMLSchema(BaseModel):
    lote_id: str
    modelos: list[ModeloEntrenadoSchema]
    suministros_scored: int
    distribucion: DistribucionScoreSchema | None
    top_10: list[PrediccionResumenSchema]


class DistribucionIRESchema(BaseModel):
    minimo: int
    p50: float
    p95: float
    maximo: int


class TopFactorSchema(BaseModel):
    factor: str
    contribution: float


class IreSuministroResumenSchema(BaseModel):
    suministro_id: str
    numero_suministro: str
    ire: int
    nivel: str
    clasificacion: str
    top_factores: list[TopFactorSchema]


class InformeIRESchema(BaseModel):
    lote_id: str
    suministros_evaluados: int
    distribucion: DistribucionIRESchema | None
    conteo_por_nivel: dict[str, int]
    top_10: list[IreSuministroResumenSchema]
    anomalias_persistidas_por_tipo: dict[str, int]


class IeeSuministroResumenSchema(BaseModel):
    suministro_id: str
    numero_suministro: str
    iee_kwh: float


class InformeIEESchema(BaseModel):
    lote_id: str
    total_kwh_estimado: float
    suministros_con_iee: int
    top_5: list[IeeSuministroResumenSchema]


class ProcesarLoteResponseSchema(BaseModel):
    estado_final: str
    informe: InformeValidacionSchema
    duplicidades: InformeDuplicidadesSchema
    features: ResumenFeaturesSchema
    reglas: InformeReglasSchema
    ml: InformeMLSchema
    ire: InformeIRESchema
    iee: InformeIEESchema


# ---------------------------------------------------------------------------------------------
# GET /api/v1/motor/lotes/{codigo_lote}/resultados -- the IRE ranking of a processed lote (the
# dashboard's data source, RN-009: inspection prioritization by IRE + Impacto Económico Estimado).
# ---------------------------------------------------------------------------------------------


class NivelFiltro(StrEnum):
    """Query-param validation for `?nivel=` -- mirrors `domain.ire.NIVELES_IRE` (the 5 bands of
    the generated `ire.nivel` column, `docker/postgres/init/01_schema.sql`). Kept as an explicit
    Enum (not derived dynamically from that tuple) so FastAPI/Pydantic auto-validates an unknown
    value as a 422 and documents the closed set in the generated OpenAPI schema -- the same manual
    sync discipline `domain.ire.TIPOS_ANOMALIA`'s own docstring already accepts for its mirrored
    literal list."""

    MUY_BAJO = "Muy Bajo"
    BAJO = "Bajo"
    MEDIO = "Medio"
    ALTO = "Alto"
    CRITICO = "Crítico"


class ClasificacionFiltro(StrEnum):
    """Query-param validation for `?clasificacion=` -- mirrors `domain.isolation_forest.
    CLASIFICACION_*` constants (the 4-band `ck_resultados_ia_clasificacion` CHECK). See
    `NivelFiltro`'s docstring for why this is an explicit Enum."""

    NORMAL = "Normal"
    ATENCION = "Atención"
    ALTO_RIESGO = "Alto Riesgo"
    CRITICO = "Crítico"


class AnomaliaRankingSchema(BaseModel):
    tipo: str
    severidad: str
    descripcion: str | None


class ObservacionSchema(BaseModel):
    """One entry of DEC-016's explicability breakdown -- parsed from `resultados_ia.observaciones`
    (stored as JSON text), never returned as raw text."""

    factor: str
    contribution: float
    reason: str


class ResultadoRankingItemSchema(BaseModel):
    suministro_id: str
    numero_suministro: str
    rutafolio: str | None
    latitud: float | None
    longitud: float | None
    ire_valor: int
    ire_nivel: str
    clasificacion: str
    score_anomalia: float | None
    probabilidad: float | None
    localidad: str | None
    categoria_tarifaria: str
    anomalias: list[AnomaliaRankingSchema]
    observaciones: list[ObservacionSchema]
    iee_kwh: float | None


class ResumenRankingSchema(BaseModel):
    """Whole-lote summary counts (dashboard summary cards) -- UNFILTERED by `?nivel=`/
    `?clasificacion=`, see `domain.resultados_ranking.ResumenRanking`'s docstring. `total_
    resultados` is deliberately a different field name than this response's own (filtered)
    `total`, to avoid confusing the two in the same JSON payload."""

    total_resultados: int
    conteo_por_nivel: dict[str, int]
    conteo_por_clasificacion: dict[str, int]
    con_anomalias: int
    suma_iee_kwh: float


class ResultadosRankingPageSchema(BaseModel):
    items: list[ResultadoRankingItemSchema]
    total: int
    limit: int
    offset: int
    resumen: ResumenRankingSchema


class BarrioRiesgoSchema(BaseModel):
    """A (localidad, barrio) aggregate for the barrio-level heat view (`GET .../barrios`)."""

    localidad: str | None
    barrio: str | None
    total_medidores: int
    ire_promedio: int
    ire_maximo: int
    nivel: str
    con_anomalias: int
    latitud: float | None
    longitud: float | None


class BarriosRiesgoPageSchema(BaseModel):
    """Every (localidad, barrio) of a processed lote, ordered by `ire_promedio` descending -- the
    frontend derives the localidad selector from these and filters client-side."""

    items: list[BarrioRiesgoSchema]
