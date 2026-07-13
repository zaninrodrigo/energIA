"""Pydantic request/response schemas for the `suministros` HTTP API.

See docs/03-architecture/API_SPEC.md ("Contexto: Gestión de Suministros") for the full contract.
"""

from datetime import date
from typing import Any

from pydantic import BaseModel


class SuministroImportItem(BaseModel):
    """One entry of the `POST /api/v1/suministros/import` JSON array request body.

    Fields are optional at the schema level on purpose, mirroring `contexts.clientes.
    presentation.schemas.ClienteImportItem`: a missing/invalid value is a *domain* rejection
    (reported per-record in `ImportSummarySchema.rejected`, HTTP 200), not a structural one.
    `numero_cliente` and `categoria_tarifaria` are natural keys (not UUIDs) -- the use case
    resolves them, rejecting the record if either does not exist.
    """

    numero_suministro: str | None = None
    numero_cliente: str | None = None
    categoria_tarifaria: str | None = None
    localidad: str | None = None
    barrio: str | None = None
    estado: str | None = None
    fecha_alta: str | None = None


class RejectedRecordSchema(BaseModel):
    """`record` is the raw submitted object (not `SuministroImportItem`): a structurally broken
    record may not even fit that model's types, so the exact payload as received is echoed back
    instead of a best-effort, possibly lossy, coercion."""

    record: dict[str, Any]
    reasons: list[str]


class ImportSummarySchema(BaseModel):
    created: int
    updated: int
    unchanged: int
    rejected: list[RejectedRecordSchema]


class SuministroSchema(BaseModel):
    id: str
    numero_suministro: str
    cliente_id: str
    categoria_tarifaria_id: str
    localidad: str | None
    barrio: str | None
    estado: str
    fecha_alta: date


class SuministrosPageSchema(BaseModel):
    items: list[SuministroSchema]
    total: int
    limit: int
    offset: int
