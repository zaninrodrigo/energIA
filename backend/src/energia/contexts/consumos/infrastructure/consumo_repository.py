"""SqlAlchemyConsumoRepository: the `ConsumoRepository` port implementation (async), US-004."""

from datetime import date
from typing import cast
from uuid import UUID

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.consumos.domain.consumo import Consumo
from energia.contexts.consumos.domain.ports import ConsumoConflictError
from energia.contexts.consumos.infrastructure.models import ConsumoModel


class SqlAlchemyConsumoRepository:
    """`ConsumoRepository` (domain/ports.py) backed by SQLAlchemy's async session.

    The session is request-scoped, provided by `energia.shared.db.get_db_session` (wired onto
    `app.state` at lifespan startup, or overridden in tests) -- this class never builds its own
    engine or connection. Mirrors `contexts.consumos.infrastructure.lectura_repository.
    SqlAlchemyLecturaRepository` exactly -- see that class's docstrings for the full rationale
    behind the upsert/savepoint/Core-statement choices repeated here, adapted to `Consumo`'s
    composite natural key `(suministro_id, fecha_inicio, fecha_fin)` and to `consumos` being a
    partitioned table (docker/postgres/init/01_schema.sql, `PARTITION BY RANGE (fecha_inicio)`).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_suministro_and_periodo(
        self, suministro_id: UUID, fecha_inicio: date, fecha_fin: date
    ) -> Consumo | None:
        stmt = select(ConsumoModel).where(
            ConsumoModel.suministro_id == suministro_id,
            ConsumoModel.fecha_inicio == fecha_inicio,
            ConsumoModel.fecha_fin == fecha_fin,
            ConsumoModel.deleted_at.is_(None),
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(model) if model is not None else None

    async def get_most_recently_deleted_by_suministro_and_periodo(
        self, suministro_id: UUID, fecha_inicio: date, fecha_fin: date
    ) -> Consumo | None:
        stmt = (
            select(ConsumoModel)
            .where(
                ConsumoModel.suministro_id == suministro_id,
                ConsumoModel.fecha_inicio == fecha_inicio,
                ConsumoModel.fecha_fin == fecha_fin,
                ConsumoModel.deleted_at.is_not(None),
            )
            # `id DESC` is a secondary sort key purely to make the pick deterministic when two
            # dead rows share the exact same `deleted_at` -- `id` carries no business meaning
            # here (arbitrary-but-stable), it just guarantees a repeatable choice instead of
            # leaving ties to Postgres's unspecified scan order.
            .order_by(ConsumoModel.deleted_at.desc(), ConsumoModel.id.desc())
            .limit(1)
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(model) if model is not None else None

    async def save(self, consumo: Consumo) -> None:
        """Insert or update, keyed by the composite natural key `(suministro_id, fecha_inicio,
        fecha_fin)`, scoped to non-soft-deleted rows -- the exact partial unique index
        `uq_consumos_suministro_periodo` enforces: `ON (suministro_id, fecha_inicio, fecha_fin)
        WHERE deleted_at IS NULL` (debt #10, `docker/postgres/init/01_schema.sql`, paid before
        this slice). That index is defined on the partitioned parent and PostgreSQL propagates it
        to every partition, so this same `ON CONFLICT` clause works regardless of which partition
        `fecha_inicio` routes the row to (verified at runtime -- see
        `tests/integration/contexts/consumos/test_consumo_repository_integration.py`).

        Same atomicity/race guarantees as `SqlAlchemyLecturaRepository.save()`: does not commit
        (caller controls the transaction) and is wrapped in its own `SAVEPOINT`
        (`session.begin_nested()`) so a conflicting write only rolls back this one record.
        """
        values = {
            "id": consumo.id,
            "suministro_id": consumo.suministro_id,
            "lote_id": consumo.lote_id,
            "lectura_id": consumo.lectura_id,
            "fecha_inicio": consumo.fecha_inicio,
            "fecha_fin": consumo.fecha_fin,
            "dias_facturados": consumo.dias_facturados,
            "kwh": consumo.kwh,
            "consumo_promedio_diario": consumo.consumo_promedio_diario,
        }
        insert_stmt = pg_insert(ConsumoModel).values(**values)
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[
                ConsumoModel.suministro_id,
                ConsumoModel.fecha_inicio,
                ConsumoModel.fecha_fin,
            ],
            index_where=ConsumoModel.deleted_at.is_(None),
            set_={key: value for key, value in values.items() if key != "id"},
        )
        try:
            async with self._session.begin_nested():
                await self._session.execute(upsert_stmt)
        except IntegrityError as error:
            raise ConsumoConflictError(
                str(error.orig) if error.orig is not None else str(error)
            ) from error

    async def resurrect(self, consumo: Consumo) -> None:
        """Revive a soft-deleted row by `(consumo.id, consumo.fecha_inicio)`, clearing
        `deleted_at` and writing every mutable field -- mirrors
        `SqlAlchemyClienteRepository.resurrect()` for the "update by identity, not `ON CONFLICT`"
        rationale (a dead row is excluded from `uq_consumos_suministro_periodo`'s partial index,
        so `save()`'s upsert cannot target it at all).

        `fecha_inicio` is included in the `WHERE` alongside `id` (unlike `resurrect()` on the
        other, non-partitioned repositories): `consumos` is partitioned by RANGE on
        `fecha_inicio`, and the caller (`ImportConsumos`) already knows it -- it is part of the
        composite natural key `(suministro_id, fecha_inicio, fecha_fin)` a dead row was looked up
        by in the first place. Including it lets Postgres prune to a single partition instead of
        scanning every one of them (contrast `soft_delete()` below, which only ever has `id`).

        The `WHERE` clause also requires `deleted_at IS NOT NULL`, and the affected row count is
        checked afterwards -- see `SqlAlchemyClienteRepository.resurrect()`'s docstring for why:
        two concurrent resurrections of the SAME dead row are a lost-update race without this
        guard, since both `UPDATE`s would otherwise match unconditionally. A zero `rowcount` here
        means a concurrent resurrection already won, and is raised as `ConsumoConflictError`
        instead of silently re-applying (and overwriting) whatever the winner just wrote.
        """
        values = {
            "suministro_id": consumo.suministro_id,
            "lote_id": consumo.lote_id,
            "lectura_id": consumo.lectura_id,
            "fecha_inicio": consumo.fecha_inicio,
            "fecha_fin": consumo.fecha_fin,
            "dias_facturados": consumo.dias_facturados,
            "kwh": consumo.kwh,
            "consumo_promedio_diario": consumo.consumo_promedio_diario,
            "deleted_at": None,
        }
        stmt = (
            update(ConsumoModel)
            .where(
                ConsumoModel.id == consumo.id,
                ConsumoModel.fecha_inicio == consumo.fecha_inicio,
                ConsumoModel.deleted_at.is_not(None),
            )
            .values(**values)
        )
        try:
            async with self._session.begin_nested():
                result = cast(CursorResult[None], await self._session.execute(stmt))
                if result.rowcount == 0:
                    raise ConsumoConflictError(
                        f"consumo {consumo.id} is no longer soft-deleted -- a concurrent "
                        "resurrection already claimed it"
                    )
        except IntegrityError as error:
            raise ConsumoConflictError(
                str(error.orig) if error.orig is not None else str(error)
            ) from error

    async def soft_delete(self, consumo_id: UUID) -> bool:
        """DECISION #13: soft-delete the ACTIVE consumo with this `id` alone -- see
        `ConsumoRepository.soft_delete()`'s docstring (domain/ports.py) for the composite-PK/
        partition-pruning trade-off this accepts (no `fecha_inicio` is known at this HTTP
        boundary, so Postgres must scan every partition instead of pruning to one -- acceptable
        for a one-record-at-a-time correction endpoint, not a bulk operation).

        Does not commit. Not wrapped in its own `SAVEPOINT` unlike `save()`/`resurrect()`: there
        is no `ON CONFLICT`/integrity-violation path to translate here, just a plain conditional
        `UPDATE` that either matches a row or does not.
        """
        stmt = (
            update(ConsumoModel)
            .where(ConsumoModel.id == consumo_id, ConsumoModel.deleted_at.is_(None))
            .values(deleted_at=func.now())
        )
        # `session.execute()` is statically typed as `Result[Any]`, but a Core UPDATE statement
        # always actually returns a `CursorResult` (the subclass that carries `rowcount`) at
        # runtime -- the same gap `SQLAlchemy`'s own typing has for every DML statement, not
        # something specific to this repository.
        result = cast(CursorResult[None], await self._session.execute(stmt))
        return result.rowcount > 0

    async def list_active(
        self,
        *,
        limit: int,
        offset: int,
        suministro_id: UUID | None = None,
        lote_id: UUID | None = None,
    ) -> list[Consumo]:
        stmt = select(ConsumoModel).where(ConsumoModel.deleted_at.is_(None))
        if suministro_id is not None:
            stmt = stmt.where(ConsumoModel.suministro_id == suministro_id)
        if lote_id is not None:
            stmt = stmt.where(ConsumoModel.lote_id == lote_id)
        # Ordered by (fecha_inicio desc, id): most recent billing period first -- what US-004's
        # "disponer del histórico completo para entrenar el modelo de IA" cares about when
        # browsing without a filter. `id` is only a tiebreaker for a fully deterministic
        # pagination order (two different suministros can share the same fecha_inicio).
        stmt = (
            stmt.order_by(ConsumoModel.fecha_inicio.desc(), ConsumoModel.id)
            .limit(limit)
            .offset(offset)
        )
        models = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain(model) for model in models]

    async def count_active(
        self, *, suministro_id: UUID | None = None, lote_id: UUID | None = None
    ) -> int:
        stmt = (
            select(func.count()).select_from(ConsumoModel).where(ConsumoModel.deleted_at.is_(None))
        )
        if suministro_id is not None:
            stmt = stmt.where(ConsumoModel.suministro_id == suministro_id)
        if lote_id is not None:
            stmt = stmt.where(ConsumoModel.lote_id == lote_id)
        return (await self._session.execute(stmt)).scalar_one()


def _to_domain(model: ConsumoModel) -> Consumo:
    return Consumo(
        id=model.id,
        suministro_id=model.suministro_id,
        lote_id=model.lote_id,
        lectura_id=model.lectura_id,
        fecha_inicio=model.fecha_inicio,
        fecha_fin=model.fecha_fin,
        dias_facturados=model.dias_facturados,
        kwh=model.kwh,
        consumo_promedio_diario=model.consumo_promedio_diario,
    )
