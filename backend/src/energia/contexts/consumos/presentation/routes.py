"""FastAPI routers for the `consumos` context: import/list lecturas (US-003), import/list lotes
de facturación (US-005).

See docs/03-architecture/API_SPEC.md ("Contexto: Gestión de Consumos") for the full contract.
"""

import dataclasses
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.consumos.application.import_lecturas import ImportLecturas
from energia.contexts.consumos.application.import_lotes import ImportLotes
from energia.contexts.consumos.domain.lote import EstadoLote
from energia.contexts.consumos.domain.ports import (
    UNSET,
    LecturaSourceRecord,
    LoteSourceRecord,
    UnsetType,
)
from energia.contexts.consumos.infrastructure.json_lectura_source import JsonLecturaSource
from energia.contexts.consumos.infrastructure.json_lote_source import JsonLoteSource
from energia.contexts.consumos.infrastructure.lectura_repository import SqlAlchemyLecturaRepository
from energia.contexts.consumos.infrastructure.lote_repository import SqlAlchemyLoteRepository
from energia.contexts.consumos.infrastructure.suministro_directory import (
    SqlDirectSuministroDirectory,
)
from energia.contexts.consumos.presentation.schemas import (
    ImportSummarySchema,
    LecturaImportItem,
    LecturaSchema,
    LecturasPageSchema,
    LoteImportItem,
    LoteSchema,
    LotesPageSchema,
    RejectedRecordSchema,
)
from energia.shared.db import get_db_session

lecturas_router = APIRouter(prefix="/api/v1/lecturas", tags=["lecturas"])
lotes_router = APIRouter(prefix="/api/v1/lotes", tags=["lotes"])

_DEFAULT_PAGE_LIMIT = 50
_MAX_PAGE_LIMIT = 200


def _structural_rejection_reasons(error: ValidationError) -> list[str]:
    """Turn a Pydantic `ValidationError` into concise, per-field reason strings, e.g.
    `"lectura_anterior: Input should be a valid number"`."""
    return [f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in error.errors()]


@lecturas_router.post("/import", response_model=ImportSummarySchema)
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


@lecturas_router.get("", response_model=LecturasPageSchema)
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


@lotes_router.post("/import", response_model=ImportSummarySchema)
async def import_lotes(
    items: list[dict[str, Any]],
    session: AsyncSession = Depends(get_db_session),
) -> ImportSummarySchema:
    """Import lotes de facturación from a JSON array (today's `LoteSource` adapter -- see
    `domain/ports.py`). Idempotent by `codigo_lote`: re-posting the same payload reports
    `updated`/`unchanged` instead of duplicating rows, and never resets an existing lote's
    `estado` -- see `application/import_lotes.py`'s docstring.

    Mirrors `import_lecturas` exactly at the HTTP boundary: the body is bound as
    `list[dict[str, Any]]`, and each element is validated against `LoteImportItem`
    *individually*, so one structurally broken record does not 422 the whole request -- it is
    rejected on its own, alongside domain rejections (e.g. a blank `codigo_lote` or a negative
    `cantidad_registros`), all reported in the same 200 response. There is no `estado` field on
    `LoteImportItem` at all -- an imported lote is always born `Pendiente` (RD-010's intent, see
    `domain/lote.py`); if a caller sends one anyway, `LoteImportItem`'s `extra="forbid"` rejects
    it as a structural violation instead of silently dropping it (see that schema's docstring).

    **FIX 1**: `nombre`/`cantidad_registros` genuinely absent from `raw_item` (checked via
    `item.model_fields_set`, not `item.nombre`/`item.cantidad_registros` themselves -- Pydantic
    cannot otherwise tell "omitted" apart from "explicitly sent as `null`") are passed down as
    `UNSET`, not `None` -- `ImportLotes` treats the two very differently on an update (preserve
    the existing stored value vs. overwrite it) -- see that module's docstring.

    The whole batch is one transaction: nothing commits until every record has been processed,
    committed once here, after `execute()` returns successfully.
    """
    records: list[LoteSourceRecord] = []
    structural_rejections: list[RejectedRecordSchema] = []

    for raw_item in items:
        try:
            item = LoteImportItem.model_validate(raw_item)
        except ValidationError as error:
            structural_rejections.append(
                RejectedRecordSchema(record=raw_item, reasons=_structural_rejection_reasons(error))
            )
            continue
        fields_set = item.model_fields_set
        records.append(
            LoteSourceRecord(
                codigo_lote=item.codigo_lote,
                nombre=item.nombre if "nombre" in fields_set else UNSET,
                cantidad_registros=(
                    item.cantidad_registros if "cantidad_registros" in fields_set else UNSET
                ),
            )
        )

    use_case = ImportLotes(repository=SqlAlchemyLoteRepository(session))
    summary = await use_case.execute(JsonLoteSource(records))
    await session.commit()

    return ImportSummarySchema(
        created=summary.created,
        updated=summary.updated,
        unchanged=summary.unchanged,
        rejected=[
            *structural_rejections,
            *[
                RejectedRecordSchema(
                    # `UNSET` fields are dropped, not echoed back as some JSON-serializable
                    # stand-in: they were never in the original payload, so omitting the key
                    # from the echoed `record` is the accurate representation (FIX 1).
                    record={
                        key: value
                        for key, value in dataclasses.asdict(rejected.record).items()
                        if not isinstance(value, UnsetType)
                    },
                    reasons=rejected.reasons,
                )
                for rejected in summary.rejected
            ],
        ],
    )


@lotes_router.get("", response_model=LotesPageSchema)
async def list_lotes(
    limit: int = Query(default=_DEFAULT_PAGE_LIMIT, ge=1, le=_MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    estado: EstadoLote | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> LotesPageSchema:
    """Paginated list of non-soft-deleted lotes, ordered by `(fecha_importacion desc, id)` --
    most recently imported batch first. Unlike `codigo_lote` (a business identifier with no
    inherent chronological meaning -- it need not even sort the way `numero_cliente`/
    `numero_suministro` do), `fecha_importacion` is exactly what "procesar automáticamente cada
    período" (US-005) cares about: which batches arrived, and in what order, so the newest ones
    surface first without the caller having to know to sort client-side. `id` is only the
    tiebreaker for a fully deterministic pagination order when two lotes share the same
    `fecha_importacion`.

    `?estado=` filters to a single `EstadoLote` value; FastAPI/Pydantic validate it against that
    enum automatically -- an unrecognized value (e.g. `?estado=Foo`) returns 422, not an empty
    page, since it is a typo/contract violation, not a legitimate filter that simply matches
    nothing.
    """
    repository = SqlAlchemyLoteRepository(session)

    lotes = await repository.list_active(limit=limit, offset=offset, estado=estado)
    total = await repository.count_active(estado=estado)

    return LotesPageSchema(
        items=[
            LoteSchema(
                id=str(lote.id),
                codigo_lote=lote.codigo_lote,
                nombre=lote.nombre,
                fecha_importacion=lote.fecha_importacion,
                cantidad_registros=lote.cantidad_registros,
                estado=lote.estado.value,
            )
            for lote in lotes
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
