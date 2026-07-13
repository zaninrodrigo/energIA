"""Pydantic request/response schemas for the `lecturas` HTTP API.

See docs/03-architecture/API_SPEC.md ("Contexto: Gestión de Consumos") for the full contract.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, field_validator


class LecturaImportItem(BaseModel):
    """One entry of the `POST /api/v1/lecturas/import` JSON array request body.

    Fields are optional at the schema level on purpose, mirroring `contexts.suministros.
    presentation.schemas.SuministroImportItem`: a missing/invalid value is a *domain* rejection
    (reported per-record in `ImportSummarySchema.rejected`, HTTP 200), not a structural one.
    `numero_suministro` is a natural key (not a UUID) -- the use case resolves it, rejecting the
    record if the suministro does not exist.
    """

    numero_suministro: str | None = None
    fecha_lectura: str | None = None
    lectura_anterior: float | None = None
    lectura_actual: float | None = None
    dias_facturados: int | None = None

    @field_validator("lectura_anterior", "lectura_actual", "dias_facturados", mode="before")
    @classmethod
    def _reject_bool(cls, value: object) -> object:
        """`bool` is a subclass of `int` in Python -- left unguarded, Pydantic's default (lax)
        coercion would silently turn a JSON `true`/`false` into `1`/`0` (or `1.0`/`0.0`) before
        these fields' own type ever gets a chance to object, defeating `Lectura.create()`'s own
        bool guard (`domain/lectura.py`) for `dias_facturados` and letting one through entirely
        for `lectura_anterior`/`lectura_actual` (which have no such guard on the domain side,
        since a `bool` cleanly satisfies `float`). Raising here fails schema validation, which
        `routes.import_lecturas` already turns into a per-record structural rejection instead of
        422ing the whole batch.
        """
        if isinstance(value, bool):
            raise ValueError("no puede ser un booleano")
        return value


class RejectedRecordSchema(BaseModel):
    """`record` is the raw submitted object (not `LecturaImportItem`): a structurally broken
    record may not even fit that model's types, so the exact payload as received is echoed back
    instead of a best-effort, possibly lossy, coercion."""

    record: dict[str, Any]
    reasons: list[str]


class ImportSummarySchema(BaseModel):
    created: int
    updated: int
    unchanged: int
    rejected: list[RejectedRecordSchema]


class LecturaSchema(BaseModel):
    id: str
    suministro_id: str
    fecha_lectura: date
    lectura_anterior: Decimal
    lectura_actual: Decimal
    dias_facturados: int


class LecturasPageSchema(BaseModel):
    items: list[LecturaSchema]
    total: int
    limit: int
    offset: int
