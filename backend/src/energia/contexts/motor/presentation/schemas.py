"""Pydantic response schemas for the `motor` HTTP API.

See docs/03-architecture/API_SPEC.md ("Contexto: Motor de Inteligencia Energética") for the full
contract.
"""

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


class ProcesarLoteResponseSchema(BaseModel):
    estado_final: str
    informe: InformeValidacionSchema
