"""FastAPI router for the `consumos` context (US-003): import lecturas, list lecturas.

See docs/03-architecture/API_SPEC.md ("Contexto: Gestión de Consumos") for the full contract.
"""

import dataclasses
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.consumos.application.import_lecturas import ImportLecturas
from energia.contexts.consumos.domain.ports import LecturaSourceRecord
from energia.contexts.consumos.infrastructure.json_lectura_source import JsonLecturaSource
from energia.contexts.consumos.infrastructure.lectura_repository import SqlAlchemyLecturaRepository
from energia.contexts.consumos.infrastructure.suministro_directory import (
    SqlDirectSuministroDirectory,
)
from energia.contexts.consumos.presentation.schemas import (
    ImportSummarySchema,
    LecturaImportItem,
    LecturaSchema,
    LecturasPageSchema,
    RejectedRecordSchema,
)
from energia.shared.db import get_db_session

router = APIRouter(prefix="/api/v1/lecturas", tags=["lecturas"])

_DEFAULT_PAGE_LIMIT = 50
_MAX_PAGE_LIMIT = 200


def _structural_rejection_reasons(error: ValidationError) -> list[str]:
    """Turn a Pydantic `ValidationError` into concise, per-field reason strings, e.g.
    `"lectura_anterior: Input should be a valid number"`."""
    return [f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in error.errors()]


@router.post("/import", response_model=ImportSummarySchema)
async def import_lecturas(
    items: list[dict[str, Any]],
    session: AsyncSession = Depends(get_db_session),
) -> ImportSummarySchema:
    """Import lecturas from a JSON array (today's `LecturaSource` adapter -- see
    `domain/ports.py`). Idempotent by `(numero_suministro, fecha_lectura)`: re-posting the same
    payload reports `updated`/`unchanged` instead of duplicating rows.

    Mirrors `contexts.suministros.presentation.routes.import_suministros` exactly: the body is
    bound as `list[dict[str, Any]]`, and each element is validated against `LecturaImportItem`
    *individually*, so one structurally broken record does not 422 the whole request -- it is
    rejected on its own, alongside domain rejections (missing/nonexistent `numero_suministro`,
    invalid `Lectura` fields), all reported in the same 200 response.

    The whole batch is one transaction: nothing commits until every record has been processed,
    committed once here, after `execute()` returns successfully.
    """
    records: list[LecturaSourceRecord] = []
    structural_rejections: list[RejectedRecordSchema] = []

    for raw_item in items:
        try:
            item = LecturaImportItem.model_validate(raw_item)
        except ValidationError as error:
            structural_rejections.append(
                RejectedRecordSchema(record=raw_item, reasons=_structural_rejection_reasons(error))
            )
            continue
        records.append(
            LecturaSourceRecord(
                numero_suministro=item.numero_suministro,
                fecha_lectura=item.fecha_lectura,
                lectura_anterior=item.lectura_anterior,
                lectura_actual=item.lectura_actual,
                dias_facturados=item.dias_facturados,
            )
        )

    use_case = ImportLecturas(
        repository=SqlAlchemyLecturaRepository(session),
        suministro_directory=SqlDirectSuministroDirectory(session),
    )
    summary = await use_case.execute(JsonLecturaSource(records))
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


@router.get("", response_model=LecturasPageSchema)
async def list_lecturas(
    limit: int = Query(default=_DEFAULT_PAGE_LIMIT, ge=1, le=_MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    numero_suministro: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> LecturasPageSchema:
    """Paginated list of non-soft-deleted lecturas, ordered by `(fecha_lectura, id)` -- see
    `SqlAlchemyLecturaRepository.list_active` for why that ordering (not the composite natural
    key) was chosen. Optionally filtered to a single suministro via `?numero_suministro=`
    (resolved to that suministro's `id` through the same `SuministroDirectory` port the import
    endpoint uses). A `numero_suministro` that does not resolve to any suministro returns an
    empty page (`total: 0`), not an error: it is a filter, not a lookup users depend on existing.
    """
    repository = SqlAlchemyLecturaRepository(session)

    suministro_id: UUID | None = None
    if numero_suministro is not None:
        suministro_directory = SqlDirectSuministroDirectory(session)
        suministro_id = await suministro_directory.resolve(numero_suministro)
        if suministro_id is None:
            return LecturasPageSchema(items=[], total=0, limit=limit, offset=offset)

    lecturas = await repository.list_active(limit=limit, offset=offset, suministro_id=suministro_id)
    total = await repository.count_active(suministro_id=suministro_id)

    return LecturasPageSchema(
        items=[
            LecturaSchema(
                id=str(lectura.id),
                suministro_id=str(lectura.suministro_id),
                fecha_lectura=lectura.fecha_lectura,
                lectura_anterior=lectura.lectura_anterior,
                lectura_actual=lectura.lectura_actual,
                dias_facturados=lectura.dias_facturados,
            )
            for lectura in lecturas
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
