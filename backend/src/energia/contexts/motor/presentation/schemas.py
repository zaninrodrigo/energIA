"""Pydantic response schemas for the `motor` HTTP API.

See docs/03-architecture/API_SPEC.md ("Contexto: Motor de Inteligencia Energética") for the full
contract.
"""

from datetime import date
from decimal import Decimal

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


class ProcesarLoteResponseSchema(BaseModel):
    estado_final: str
    informe: InformeValidacionSchema
    duplicidades: InformeDuplicidadesSchema
