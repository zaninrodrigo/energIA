"""Pydantic request/response schemas for the `lecturas` and `lotes` HTTP APIs.

See docs/03-architecture/API_SPEC.md ("Contexto: Gestión de Consumos") for the full contract.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


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


class LoteImportItem(BaseModel):
    """One entry of the `POST /api/v1/lotes/import` JSON array request body.

    Fields are optional at the schema level on purpose, mirroring `LecturaImportItem`: a
    missing/invalid value is a *domain* rejection (reported per-record in
    `ImportSummarySchema.rejected`, HTTP 200), not a structural one.

    There is deliberately no `estado` field here -- see `domain/lote.py`'s module docstring
    (RD-010's intent). `model_config = ConfigDict(extra="forbid")` is what actually enforces
    that at the HTTP boundary: an unrecognized key (`estado`, or a typo like
    `canditad_registros`) is rejected as a structural violation (per-record, HTTP 200, naming
    the offending key in the reason), not silently dropped. This used to default to Pydantic's
    `extra="ignore"` (undeclared fields dropped with no trace at all) -- which meant a caller
    sending `estado` had no effect, but also meant a *typo'd* field name (e.g.
    `canditad_registros` instead of `cantidad_registros`) vanished exactly the same invisible
    way, silently defaulting the real field to its own default instead of surfacing any error.
    `clientes`/`suministros`/`lecturas`' own import DTOs still use `extra="ignore"` -- aligning
    them the same way is a separate, not-yet-made decision (see `PROJECT_MASTER_SPEC.md`,
    pending items).
    """

    model_config = ConfigDict(extra="forbid")

    codigo_lote: str | None = None
    nombre: str | None = None
    cantidad_registros: int | None = None

    @field_validator("cantidad_registros", mode="before")
    @classmethod
    def _reject_bool(cls, value: object) -> object:
        """Same guard as `LecturaImportItem.dias_facturados`: `bool` is a subclass of `int` in
        Python, so an unguarded `true`/`false` would lax-coerce to `1`/`0`. No NaN/Infinity guard
        is needed here (unlike `lectura_anterior`/`lectura_actual`): `cantidad_registros` is an
        integer count, not a float.
        """
        if isinstance(value, bool):
            raise ValueError("no puede ser un booleano")
        return value


class LoteSchema(BaseModel):
    id: str
    codigo_lote: str
    nombre: str | None
    fecha_importacion: datetime
    cantidad_registros: int
    estado: str


class LotesPageSchema(BaseModel):
    items: list[LoteSchema]
    total: int
    limit: int
    offset: int


class ConsumoImportItem(BaseModel):
    """One entry of the `POST /api/v1/consumos/import` JSON array request body.

    Fields are optional at the schema level, mirroring `LecturaImportItem`/`LoteImportItem`: a
    missing/invalid value is a *domain* rejection (reported per-record in
    `ImportSummarySchema.rejected`, HTTP 200), not a structural one -- except an unrecognized key,
    which `model_config = ConfigDict(extra="forbid")` (the `Lote` standard -- see that schema's
    docstring) does reject structurally, naming the offending key.

    `numero_suministro`/`codigo_lote` are natural keys, not UUIDs: `ImportConsumos` resolves both,
    rejecting the record if either does not exist. `consumo_promedio_diario`/`fecha_lectura` are
    the two `UNSET`-aware optional fields (see `domain/ports.py`'s `ConsumoSourceRecord`
    docstring): `routes.import_consumos` checks `model_fields_set` (not these attributes
    themselves) to tell "genuinely absent" apart from "explicitly sent as null" before building
    the `ConsumoSourceRecord`, the same way `LoteImportItem`'s `nombre`/`cantidad_registros`
    already do (FIX 1).
    """

    model_config = ConfigDict(extra="forbid")

    numero_suministro: str | None = None
    codigo_lote: str | None = None
    fecha_inicio: str | None = None
    fecha_fin: str | None = None
    dias_facturados: int | None = None
    kwh: float | None = None
    consumo_promedio_diario: float | None = None
    fecha_lectura: str | None = None

    @field_validator("dias_facturados", "kwh", "consumo_promedio_diario", mode="before")
    @classmethod
    def _reject_bool(cls, value: object) -> object:
        """`bool` is a subclass of `int` in Python -- would otherwise lax-coerce a JSON
        `true`/`false` into `1`/`0` (or `1.0`/`0.0`) before these fields' own validation gets a
        chance to object. Mirrors `LecturaImportItem`'s identical guard."""
        if isinstance(value, bool):
            raise ValueError("no puede ser un booleano")
        return value


class ConsumoSchema(BaseModel):
    id: str
    suministro_id: str
    lote_id: str
    lectura_id: str | None
    fecha_inicio: date
    fecha_fin: date
    dias_facturados: int
    kwh: Decimal
    consumo_promedio_diario: Decimal | None


class ConsumosPageSchema(BaseModel):
    items: list[ConsumoSchema]
    total: int
    limit: int
    offset: int
