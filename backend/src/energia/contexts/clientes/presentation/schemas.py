"""Pydantic request/response schemas for the `clientes` HTTP API.

See docs/03-architecture/API_SPEC.md ("Contexto: Gestión de Clientes") for the full contract.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict


class ClienteImportItem(BaseModel):
    """One entry of the `POST /api/v1/clientes/import` JSON array request body.

    Fields are optional at the schema level on purpose: a missing `numero_cliente` or `nombre`
    is a *domain* rejection (reported per-record in `ImportSummarySchema.rejected`, HTTP 200),
    not a structural one.

    The endpoint (`presentation/routes.py`) binds the request body as `list[dict[str, Any]]`
    and validates each raw object against this model individually — a structurally broken
    *record* (e.g. `numero_cliente` sent as a number) is a per-record rejection (HTTP 200), not
    a whole-request 422, precisely because each element is checked on its own instead of the
    whole array being bound to `list[ClienteImportItem]` in one shot. FastAPI/Pydantic still
    return 422 for a malformed *body* (not a JSON array at all).

    `model_config = ConfigDict(extra="forbid")` (DECISION #11, aligning with `LoteImportItem`/
    `ConsumoImportItem` -- see `contexts.consumos.presentation.schemas`): an unrecognized key (a
    typo like `numero_clientee`, or any other undeclared field) is rejected as a structural
    violation (per-record, HTTP 200, naming the offending key in the reason), not silently
    dropped. This used to default to Pydantic's `extra="ignore"` (undeclared fields dropped with
    no trace at all).
    """

    model_config = ConfigDict(extra="forbid")

    numero_cliente: str | None = None
    nombre: str | None = None
    estado: str | None = None
    documento: str | None = None
    localidad: str | None = None
    barrio: str | None = None
    direccion: dict[str, Any] | None = None


class RejectedRecordSchema(BaseModel):
    """`record` is the raw submitted object (not `ClienteImportItem`): a structurally broken
    record (e.g. `numero_cliente: 123`) may not even fit that model's types, so the exact
    payload as received is echoed back instead of a best-effort, possibly lossy, coercion."""

    record: dict[str, Any]
    reasons: list[str]


class ImportSummarySchema(BaseModel):
    created: int
    updated: int
    unchanged: int
    restored: int
    rejected: list[RejectedRecordSchema]


class ClienteSchema(BaseModel):
    id: str
    numero_cliente: str
    nombre: str
    estado: str
    documento: str | None
    localidad: str | None
    barrio: str | None
    direccion: dict[str, Any] | None


class ClientesPageSchema(BaseModel):
    items: list[ClienteSchema]
    total: int
    limit: int
    offset: int
