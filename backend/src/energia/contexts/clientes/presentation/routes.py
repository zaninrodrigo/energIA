"""FastAPI router for the `clientes` context (US-001): import clients, list clients.

See docs/03-architecture/API_SPEC.md ("Contexto: Gestión de Clientes") for the full contract.
"""

import dataclasses
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.clientes.application.import_clientes import ImportClientes
from energia.contexts.clientes.domain.ports import ClienteSourceRecord
from energia.contexts.clientes.infrastructure.cliente_repository import SqlAlchemyClienteRepository
from energia.contexts.clientes.infrastructure.json_cliente_source import JsonClienteSource
from energia.contexts.clientes.presentation.schemas import (
    ClienteImportItem,
    ClienteSchema,
    ClientesPageSchema,
    ImportSummarySchema,
    RejectedRecordSchema,
)
from energia.shared.db import get_db_session

router = APIRouter(prefix="/api/v1/clientes", tags=["clientes"])

_DEFAULT_PAGE_LIMIT = 50
_MAX_PAGE_LIMIT = 200


def _structural_rejection_reasons(error: ValidationError) -> list[str]:
    """Turn a Pydantic `ValidationError` into concise, per-field reason strings, e.g.
    `"numero_cliente: Input should be a valid string"`."""
    return [f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in error.errors()]


@router.post("/import", response_model=ImportSummarySchema)
async def import_clientes(
    items: list[dict[str, Any]],
    session: AsyncSession = Depends(get_db_session),
) -> ImportSummarySchema:
    """Import clients from a JSON array (today's `ClienteSource` adapter — see
    `domain/ports.py`). Idempotent by `numero_cliente`: re-posting the same payload reports
    `updated`/`unchanged` instead of duplicating rows.

    The body is bound as `list[dict[str, Any]]` (a raw JSON array of objects), not
    `list[ClienteImportItem]`: each element is instead validated against `ClienteImportItem`
    *individually*, right below. That is what keeps one structurally broken record (e.g.
    `numero_cliente` sent as a number instead of a string) from 422-ing the whole request — it
    is rejected on its own, the same way a domain-invalid record already was, and the rest of
    the batch still goes through. The body itself must still be a JSON array of objects, or
    FastAPI/Pydantic reject the whole request with 422 before this function even runs.

    The whole batch is one transaction: nothing commits until every record has been processed
    (see `ImportClientes.execute()` and `SqlAlchemyClienteRepository.save()` for why neither of
    them commits), committed once here, after `execute()` returns successfully.
    """
    records: list[ClienteSourceRecord] = []
    structural_rejections: list[RejectedRecordSchema] = []

    for raw_item in items:
        try:
            item = ClienteImportItem.model_validate(raw_item)
        except ValidationError as error:
            structural_rejections.append(
                RejectedRecordSchema(record=raw_item, reasons=_structural_rejection_reasons(error))
            )
            continue
        records.append(
            ClienteSourceRecord(
                numero_cliente=item.numero_cliente,
                nombre=item.nombre,
                estado=item.estado,
                documento=item.documento,
                localidad=item.localidad,
                barrio=item.barrio,
                direccion=item.direccion,
            )
        )

    use_case = ImportClientes(SqlAlchemyClienteRepository(session))
    summary = await use_case.execute(JsonClienteSource(records))
    await session.commit()

    return ImportSummarySchema(
        created=summary.created,
        updated=summary.updated,
        unchanged=summary.unchanged,
        rejected=[
            *structural_rejections,
            *[
                RejectedRecordSchema(
                    record=dataclasses.asdict(rejected.record),
                    reasons=rejected.reasons,
                )
                for rejected in summary.rejected
            ],
        ],
    )


@router.get("", response_model=ClientesPageSchema)
async def list_clientes(
    limit: int = Query(default=_DEFAULT_PAGE_LIMIT, ge=1, le=_MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> ClientesPageSchema:
    """Paginated list of non-soft-deleted clients, ordered by numero_cliente."""
    repository = SqlAlchemyClienteRepository(session)
    clientes = await repository.list_active(limit=limit, offset=offset)
    total = await repository.count_active()

    return ClientesPageSchema(
        items=[
            ClienteSchema(
                id=str(cliente.id),
                numero_cliente=cliente.numero_cliente,
                nombre=cliente.nombre,
                estado=cliente.estado.value,
                documento=cliente.documento,
                localidad=cliente.localidad,
                barrio=cliente.barrio,
                direccion=cliente.direccion,
            )
            for cliente in clientes
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
