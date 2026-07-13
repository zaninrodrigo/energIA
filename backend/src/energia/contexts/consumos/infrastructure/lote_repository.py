"""SqlAlchemyLoteRepository: the `LoteRepository` port implementation (async)."""

from typing import cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.consumos.domain.lote import EstadoLote, Lote
from energia.contexts.consumos.domain.ports import LoteConflictError
from energia.contexts.consumos.infrastructure.models import LoteModel


class SqlAlchemyLoteRepository:
    """`LoteRepository` (domain/ports.py) backed by SQLAlchemy's async session.

    The session is request-scoped, provided by `energia.shared.db.get_db_session` (wired onto
    `app.state` at lifespan startup, or overridden in tests) -- this class never builds its own
    engine or connection. Mirrors `contexts.consumos.infrastructure.lectura_repository.
    SqlAlchemyLecturaRepository` exactly -- see that class's docstrings for the full rationale
    behind the upsert/savepoint/Core-statement choices repeated here, adapted to `Lote`'s single
    natural key `codigo_lote`.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_codigo_lote(self, codigo_lote: str) -> Lote | None:
        stmt = select(LoteModel).where(
            LoteModel.codigo_lote == codigo_lote,
            LoteModel.deleted_at.is_(None),
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(model) if model is not None else None

    async def get_most_recently_deleted_by_codigo_lote(self, codigo_lote: str) -> Lote | None:
        stmt = (
            select(LoteModel)
            .where(
                LoteModel.codigo_lote == codigo_lote,
                LoteModel.deleted_at.is_not(None),
            )
            # `id DESC` is a secondary sort key purely to make the pick deterministic when two
            # dead rows share the exact same `deleted_at` -- `id` carries no business meaning
            # here (arbitrary-but-stable), it just guarantees a repeatable choice instead of
            # leaving ties to Postgres's unspecified scan order.
            .order_by(LoteModel.deleted_at.desc(), LoteModel.id.desc())
            .limit(1)
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(model) if model is not None else None

    async def save(self, lote: Lote) -> None:
        """Insert or update, keyed by the natural key `codigo_lote`, scoped to non-soft-deleted
        rows -- the exact partial unique index `uq_lotes_codigo_lote` enforces: `ON (codigo_lote)
        WHERE deleted_at IS NULL`. Same atomicity/race guarantees as
        `SqlAlchemyLecturaRepository.save()`: does not commit (caller controls the transaction)
        and is wrapped in its own `SAVEPOINT` (`session.begin_nested()`) so a conflicting write
        only rolls back this one record.

        `estado` and `fecha_importacion` are deliberately excluded from the `ON CONFLICT DO
        UPDATE SET` column list below (FIX 3) -- an update on an existing row never writes either
        column, no matter what the given `lote` carries. This is *not* the same as the previous
        version of this docstring claimed ("always safe" because `ImportLotes` always passes the
        right value forward via `dataclasses.replace()`): that reasoning has a lost-update race.
        `ImportLotes.execute()` reads the existing row, then later calls `save()` with an entity
        built from that read -- if another session/transaction changes `estado` (e.g. the future
        processing engine finishing the batch) in between, `ImportLotes`' in-memory `existing`
        is stale, and unconditionally writing `estado` from it on conflict would revert the row
        back to whatever `estado` that stale read saw, silently undoing the concurrent change.
        Excluding `estado`/`fecha_importacion` from the update SET makes the guarantee structural
        (enforced by the SQL statement itself) instead of resting on every caller always reading
        fresh enough data -- neither column is *ever* touched here on an update, so no caller of
        `save()` can revert either one, stale read or not. Only the initial `INSERT` (no existing
        row yet, so no conflict) still writes both from `lote` as given -- a brand-new row has no
        prior `estado`/`fecha_importacion` to protect.
        """
        values = {
            "id": lote.id,
            "codigo_lote": lote.codigo_lote,
            "nombre": lote.nombre,
            "fecha_importacion": lote.fecha_importacion,
            "cantidad_registros": lote.cantidad_registros,
            "estado": lote.estado.value,
        }
        # `estado`/`fecha_importacion` intentionally left out here -- see this method's docstring.
        update_only_columns = ("codigo_lote", "nombre", "cantidad_registros")
        insert_stmt = pg_insert(LoteModel).values(**values)
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[LoteModel.codigo_lote],
            index_where=LoteModel.deleted_at.is_(None),
            set_={key: values[key] for key in update_only_columns},
        )
        try:
            async with self._session.begin_nested():
                await self._session.execute(upsert_stmt)
        except IntegrityError as error:
            raise LoteConflictError(
                str(error.orig) if error.orig is not None else str(error)
            ) from error

    async def resurrect(self, lote: Lote) -> None:
        """Revive a soft-deleted row by `lote.id`, clearing `deleted_at` and writing `nombre`/
        `cantidad_registros` -- mirrors `SqlAlchemyClienteRepository.resurrect()` for the "update
        by id, not `ON CONFLICT`" rationale (a dead row is excluded from
        `uq_lotes_codigo_lote`'s partial index, so `save()`'s upsert cannot target it at all).

        `estado`/`fecha_importacion` are deliberately excluded from the `SET` here too -- exactly
        `save()`'s own exclusion (see that method's docstring): a resurrection must not reset the
        `estado` a lote had when it was soft-deleted, the same RD-010 intent that already
        protects an ordinary update.

        The `WHERE` clause also requires `deleted_at IS NOT NULL`, and the affected row count is
        checked afterwards -- see `SqlAlchemyClienteRepository.resurrect()`'s docstring for why:
        two concurrent resurrections of the SAME dead row are a lost-update race without this
        guard, since both `UPDATE`s would otherwise match unconditionally. A zero `rowcount` here
        means a concurrent resurrection already won, and is raised as `LoteConflictError` instead
        of silently re-applying (and overwriting) whatever the winner just wrote.
        """
        values = {
            "codigo_lote": lote.codigo_lote,
            "nombre": lote.nombre,
            "cantidad_registros": lote.cantidad_registros,
            "deleted_at": None,
        }
        stmt = (
            update(LoteModel)
            .where(LoteModel.id == lote.id, LoteModel.deleted_at.is_not(None))
            .values(**values)
        )
        try:
            async with self._session.begin_nested():
                result = cast(CursorResult[None], await self._session.execute(stmt))
                if result.rowcount == 0:
                    raise LoteConflictError(
                        f"lote {lote.id} is no longer soft-deleted -- a concurrent "
                        "resurrection already claimed it"
                    )
        except IntegrityError as error:
            raise LoteConflictError(
                str(error.orig) if error.orig is not None else str(error)
            ) from error

    async def list_active(
        self, *, limit: int, offset: int, estado: EstadoLote | None = None
    ) -> list[Lote]:
        stmt = select(LoteModel).where(LoteModel.deleted_at.is_(None))
        if estado is not None:
            stmt = stmt.where(LoteModel.estado == estado.value)
        # Ordered by (fecha_importacion desc, id): most recently imported batch first -- see
        # `presentation/routes.py`'s `list_lotes` docstring for the business justification.
        # `id` is only a tiebreaker for a fully deterministic pagination order.
        stmt = (
            stmt.order_by(LoteModel.fecha_importacion.desc(), LoteModel.id)
            .limit(limit)
            .offset(offset)
        )
        models = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain(model) for model in models]

    async def count_active(self, *, estado: EstadoLote | None = None) -> int:
        stmt = select(func.count()).select_from(LoteModel).where(LoteModel.deleted_at.is_(None))
        if estado is not None:
            stmt = stmt.where(LoteModel.estado == estado.value)
        return (await self._session.execute(stmt)).scalar_one()


def _to_domain(model: LoteModel) -> Lote:
    return Lote(
        id=model.id,
        codigo_lote=model.codigo_lote,
        nombre=model.nombre,
        fecha_importacion=model.fecha_importacion,
        cantidad_registros=model.cantidad_registros,
        estado=EstadoLote(model.estado),
    )
