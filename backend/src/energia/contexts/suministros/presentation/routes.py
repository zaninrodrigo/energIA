"""FastAPI router for the `suministros` context (US-002): import suministros, list suministros.

See docs/03-architecture/API_SPEC.md ("Contexto: Gestión de Suministros") for the full contract.
"""

import dataclasses
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.suministros.application.import_suministros import ImportSuministros
from energia.contexts.suministros.domain.ports import SuministroSourceRecord
from energia.contexts.suministros.infrastructure.categoria_tarifaria_directory import (
    SqlAlchemyCategoriaTarifariaDirectory,
)
from energia.contexts.suministros.infrastructure.cliente_directory import SqlDirectClienteDirectory
from energia.contexts.suministros.infrastructure.json_suministro_source import JsonSuministroSource
from energia.contexts.suministros.infrastructure.suministro_repository import (
    SqlAlchemySuministroRepository,
)
from energia.contexts.suministros.presentation.schemas import (
    ImportSummarySchema,
    RejectedRecordSchema,
    SuministroImportItem,
    SuministroSchema,
    SuministrosPageSchema,
)
from energia.shared.db import get_db_session

router = APIRouter(prefix="/api/v1/suministros", tags=["suministros"])

_DEFAULT_PAGE_LIMIT = 50
_MAX_PAGE_LIMIT = 200


def _structural_rejection_reasons(error: ValidationError) -> list[str]:
    """Turn a Pydantic `ValidationError` into concise, per-field reason strings, e.g.
    `"numero_suministro: Input should be a valid string"`."""
    return [f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in error.errors()]


@router.post("/import", response_model=ImportSummarySchema)
async def import_suministros(
    items: list[dict[str, Any]],
    session: AsyncSession = Depends(get_db_session),
) -> ImportSummarySchema:
    """Import suministros from a JSON array (today's `SuministroSource` adapter -- see
    `domain/ports.py`). Idempotent by `numero_suministro`: re-posting the same payload reports
    `updated`/`unchanged` instead of duplicating rows.

    Mirrors `contexts.clientes.presentation.routes.import_clientes` exactly: the body is bound
    as `list[dict[str, Any]]`, and each element is validated against `SuministroImportItem`
    *individually*, so one structurally broken record does not 422 the whole request -- it is
    rejected on its own, alongside domain rejections (missing/nonexistent `numero_cliente` or
    `categoria_tarifaria`, invalid `Suministro` fields), all reported in the same 200 response.

    The whole batch is one transaction: nothing commits until every record has been processed,
    committed once here, after `execute()` returns successfully.
    """
    records: list[SuministroSourceRecord] = []
    structural_rejections: list[RejectedRecordSchema] = []

    for raw_item in items:
        try:
            item = SuministroImportItem.model_validate(raw_item)
        except ValidationError as error:
            structural_rejections.append(
                RejectedRecordSchema(record=raw_item, reasons=_structural_rejection_reasons(error))
            )
            continue
        records.append(
            SuministroSourceRecord(
                numero_suministro=item.numero_suministro,
                numero_cliente=item.numero_cliente,
                categoria_tarifaria=item.categoria_tarifaria,
                localidad=item.localidad,
                barrio=item.barrio,
                estado=item.estado,
                fecha_alta=item.fecha_alta,
            )
        )

    use_case = ImportSuministros(
        repository=SqlAlchemySuministroRepository(session),
        cliente_directory=SqlDirectClienteDirectory(session),
        categoria_directory=SqlAlchemyCategoriaTarifariaDirectory(session),
    )
    summary = await use_case.execute(JsonSuministroSource(records))
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


@router.get("", response_model=SuministrosPageSchema)
async def list_suministros(
    limit: int = Query(default=_DEFAULT_PAGE_LIMIT, ge=1, le=_MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    numero_cliente: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> SuministrosPageSchema:
    """Paginated list of non-soft-deleted suministros, ordered by numero_suministro. Optionally
    filtered to a single cliente via `?numero_cliente=` (resolved to that cliente's `id` through
    the same `ClienteDirectory` port the import endpoint uses). A `numero_cliente` that does not
    resolve to any cliente returns an empty page (`total: 0`), not an error: it is a filter, not
    a lookup users depend on existing.
    """
    repository = SqlAlchemySuministroRepository(session)

    cliente_id: UUID | None = None
    if numero_cliente is not None:
        cliente_directory = SqlDirectClienteDirectory(session)
        cliente_id = await cliente_directory.resolve(numero_cliente)
        if cliente_id is None:
            return SuministrosPageSchema(items=[], total=0, limit=limit, offset=offset)

    suministros = await repository.list_active(limit=limit, offset=offset, cliente_id=cliente_id)
    total = await repository.count_active(cliente_id=cliente_id)

    return SuministrosPageSchema(
        items=[
            SuministroSchema(
                id=str(suministro.id),
                numero_suministro=suministro.numero_suministro,
                cliente_id=str(suministro.cliente_id),
                categoria_tarifaria_id=str(suministro.categoria_tarifaria_id),
                localidad=suministro.localidad,
                barrio=suministro.barrio,
                estado=suministro.estado,
                fecha_alta=suministro.fecha_alta,
            )
            for suministro in suministros
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
