"""SqlAlchemyLecturaRepository: the `LecturaRepository` port implementation (async)."""

from datetime import date
from typing import cast
from uuid import UUID

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.consumos.domain.lectura import Lectura
from energia.contexts.consumos.domain.ports import LecturaConflictError
from energia.contexts.consumos.infrastructure.models import LecturaModel


class SqlAlchemyLecturaRepository:
    """`LecturaRepository` (domain/ports.py) backed by SQLAlchemy's async session.

    The session is request-scoped, provided by `energia.shared.db.get_db_session` (wired onto
    `app.state` at lifespan startup, or overridden in tests) -- this class never builds its own
    engine or connection. Mirrors `contexts.suministros.infrastructure.suministro_repository.
    SqlAlchemySuministroRepository` exactly -- see that class's docstrings for the full
    rationale behind the upsert/savepoint/Core-statement choices repeated here, adapted to
    Lectura's composite natural key `(suministro_id, fecha_lectura)`.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_suministro_and_fecha(
        self, suministro_id: UUID, fecha_lectura: date
    ) -> Lectura | None:
        stmt = select(LecturaModel).where(
            LecturaModel.suministro_id == suministro_id,
            LecturaModel.fecha_lectura == fecha_lectura,
            LecturaModel.deleted_at.is_(None),
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(model) if model is not None else None

    async def get_most_recently_deleted_by_suministro_and_fecha(
        self, suministro_id: UUID, fecha_lectura: date
    ) -> Lectura | None:
        stmt = (
            select(LecturaModel)
            .where(
                LecturaModel.suministro_id == suministro_id,
                LecturaModel.fecha_lectura == fecha_lectura,
                LecturaModel.deleted_at.is_not(None),
            )
            # `id DESC` is a secondary sort key purely to make the pick deterministic when two
            # dead rows share the exact same `deleted_at` -- `id` carries no business meaning
            # here (arbitrary-but-stable), it just guarantees a repeatable choice instead of
            # leaving ties to Postgres's unspecified scan order.
            .order_by(LecturaModel.deleted_at.desc(), LecturaModel.id.desc())
            .limit(1)
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(model) if model is not None else None

    async def save(self, lectura: Lectura) -> None:
        """Insert or update, keyed by the composite natural key `(suministro_id, fecha_lectura)`,
        scoped to non-soft-deleted rows -- the exact partial unique index
        `uq_lecturas_suministro_fecha` enforces: `ON (suministro_id, fecha_lectura) WHERE
        deleted_at IS NULL`. Same atomicity/race guarantees as
        `SqlAlchemySuministroRepository.save()`: does not commit (caller controls the
        transaction) and is wrapped in its own `SAVEPOINT` (`session.begin_nested()`) so a
        conflicting write only rolls back this one record.
        """
        values = {
            "id": lectura.id,
            "suministro_id": lectura.suministro_id,
            "fecha_lectura": lectura.fecha_lectura,
            "lectura_anterior": lectura.lectura_anterior,
            "lectura_actual": lectura.lectura_actual,
            "dias_facturados": lectura.dias_facturados,
        }
        insert_stmt = pg_insert(LecturaModel).values(**values)
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[LecturaModel.suministro_id, LecturaModel.fecha_lectura],
            index_where=LecturaModel.deleted_at.is_(None),
            set_={key: value for key, value in values.items() if key != "id"},
        )
        try:
            async with self._session.begin_nested():
                await self._session.execute(upsert_stmt)
        except IntegrityError as error:
            raise LecturaConflictError(
                str(error.orig) if error.orig is not None else str(error)
            ) from error

    async def resurrect(self, lectura: Lectura) -> None:
        """Revive a soft-deleted row by `lectura.id`, clearing `deleted_at` and writing every
        mutable field -- mirrors `SqlAlchemyClienteRepository.resurrect()` exactly, see that
        method's docstring for why a plain `UPDATE ... WHERE id = :id` is used instead of `save()`'s
        `ON CONFLICT` upsert (a dead row is excluded from `uq_lecturas_suministro_fecha`'s partial
        index, so that upsert cannot target it at all).

        The `WHERE` clause also requires `deleted_at IS NOT NULL`, and the affected row count is
        checked afterwards -- see `SqlAlchemyClienteRepository.resurrect()`'s docstring for why:
        two concurrent resurrections of the SAME dead row are a lost-update race without this
        guard, since both `UPDATE`s would otherwise match unconditionally. A zero `rowcount` here
        means a concurrent resurrection already won, and is raised as `LecturaConflictError`
        instead of silently re-applying (and overwriting) whatever the winner just wrote.
        """
        values = {
            "suministro_id": lectura.suministro_id,
            "fecha_lectura": lectura.fecha_lectura,
            "lectura_anterior": lectura.lectura_anterior,
            "lectura_actual": lectura.lectura_actual,
            "dias_facturados": lectura.dias_facturados,
            "deleted_at": None,
        }
        stmt = (
            update(LecturaModel)
            .where(LecturaModel.id == lectura.id, LecturaModel.deleted_at.is_not(None))
            .values(**values)
        )
        try:
            async with self._session.begin_nested():
                result = cast(CursorResult[None], await self._session.execute(stmt))
                if result.rowcount == 0:
                    raise LecturaConflictError(
                        f"lectura {lectura.id} is no longer soft-deleted -- a concurrent "
                        "resurrection already claimed it"
                    )
        except IntegrityError as error:
            raise LecturaConflictError(
                str(error.orig) if error.orig is not None else str(error)
            ) from error

    async def list_active(
        self, *, limit: int, offset: int, suministro_id: UUID | None = None
    ) -> list[Lectura]:
        stmt = select(LecturaModel).where(LecturaModel.deleted_at.is_(None))
        if suministro_id is not None:
            stmt = stmt.where(LecturaModel.suministro_id == suministro_id)
        # Ordered by (fecha_lectura, id), not by the composite natural key
        # (suministro_id, fecha_lectura): suministro_id is an internal UUID with no business
        # ordering meaning, whereas fecha_lectura is exactly what "histórico completo" (US-003)
        # is about -- browsing readings chronologically, whether globally or filtered to one
        # suministro. `id` is only a tiebreaker for a fully deterministic pagination order: two
        # different suministros can share the same fecha_lectura in an unfiltered listing (the
        # unique index only guarantees uniqueness per suministro), so fecha_lectura alone is not
        # always unique across the whole table.
        stmt = (
            stmt.order_by(LecturaModel.fecha_lectura, LecturaModel.id).limit(limit).offset(offset)
        )
        models = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain(model) for model in models]

    async def count_active(self, *, suministro_id: UUID | None = None) -> int:
        stmt = (
            select(func.count()).select_from(LecturaModel).where(LecturaModel.deleted_at.is_(None))
        )
        if suministro_id is not None:
            stmt = stmt.where(LecturaModel.suministro_id == suministro_id)
        return (await self._session.execute(stmt)).scalar_one()


def _to_domain(model: LecturaModel) -> Lectura:
    return Lectura(
        id=model.id,
        suministro_id=model.suministro_id,
        fecha_lectura=model.fecha_lectura,
        lectura_anterior=model.lectura_anterior,
        lectura_actual=model.lectura_actual,
        dias_facturados=model.dias_facturados,
    )
