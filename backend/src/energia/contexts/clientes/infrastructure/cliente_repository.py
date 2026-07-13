"""SqlAlchemyClienteRepository: the `ClienteRepository` port implementation (async)."""

from typing import cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.clientes.domain.cliente import Cliente, EstadoCliente
from energia.contexts.clientes.domain.ports import ClienteConflictError
from energia.contexts.clientes.infrastructure.models import ClienteModel


class SqlAlchemyClienteRepository:
    """`ClienteRepository` (domain/ports.py) backed by SQLAlchemy's async session.

    The session is request-scoped, provided by `energia.shared.db.get_db_session` (wired onto
    `app.state` at lifespan startup, or overridden in tests) — this class never builds its own
    engine or connection.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_numero_cliente(self, numero_cliente: str) -> Cliente | None:
        stmt = select(ClienteModel).where(
            ClienteModel.numero_cliente == numero_cliente,
            ClienteModel.deleted_at.is_(None),
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(model) if model is not None else None

    async def get_most_recently_deleted_by_numero_cliente(
        self, numero_cliente: str
    ) -> Cliente | None:
        stmt = (
            select(ClienteModel)
            .where(
                ClienteModel.numero_cliente == numero_cliente,
                ClienteModel.deleted_at.is_not(None),
            )
            # `id DESC` is a secondary sort key purely to make the pick deterministic when two
            # dead rows share the exact same `deleted_at` -- `id` carries no business meaning
            # here (arbitrary-but-stable), it just guarantees a repeatable choice instead of
            # leaving ties to Postgres's unspecified scan order.
            .order_by(ClienteModel.deleted_at.desc(), ClienteModel.id.desc())
            .limit(1)
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(model) if model is not None else None

    async def save(self, cliente: Cliente) -> None:
        """Insert or update, keyed by the natural key (`numero_cliente`, scoped to
        non-soft-deleted rows — the exact partial unique index `uq_clientes_numero_cliente`
        enforces: `ON (numero_cliente) WHERE deleted_at IS NULL`).

        Keying the upsert on the natural key rather than `cliente.id` is what makes concurrent
        imports of the same brand-new `numero_cliente` race-safe: `INSERT ... ON CONFLICT
        (numero_cliente) WHERE deleted_at IS NULL DO UPDATE` is Postgres's documented atomic
        upsert — the loser of a real race blocks until the winner commits, then gracefully
        updates the winner's row instead of raising an integrity error (the bug this replaces:
        `ON CONFLICT (id)` never conflicts for two different fresh ids, so the loser's INSERT
        used to hit `uq_clientes_numero_cliente` directly, an unhandled `IntegrityError`). `id`
        is deliberately excluded from `SET`, so an update never overwrites the matched row's
        existing identity with the caller's (possibly fresh) id.

        Does not commit — the caller (route/use-case boundary, see `ImportClientes.execute()`)
        controls the transaction, so a whole import batch commits atomically, once, at the end.

        Wrapped in its own `SAVEPOINT` (`session.begin_nested()`): if this specific write still
        violates some other database integrity constraint (see `ClienteConflictError`'s
        docstring for a worked example), only this savepoint rolls back — the rest of the
        caller's transaction, and any records already written within it, are unaffected.

        Deliberately a Core statement naming only the domain columns, not `session.merge()` on
        an ORM instance: `merge()` copies *every* mapped attribute of its source object onto
        the target — including audit columns like `created_at`/`updated_at`, which this
        repository never sets in Python so the database's own `DEFAULT now()` and the
        `trg_clientes_set_updated_at` trigger can apply. An unset mapped attribute reads back
        as `None` through SQLAlchemy's instrumentation, so `merge()` would write an explicit
        `NULL` into `created_at` instead of leaving it for the database to fill in, tripping
        its `NOT NULL` constraint. Never naming those columns here sidesteps that entirely.
        """
        values = {
            "id": cliente.id,
            "numero_cliente": cliente.numero_cliente,
            "nombre": cliente.nombre,
            "estado": cliente.estado.value,
            "documento": cliente.documento,
            "localidad": cliente.localidad,
            "barrio": cliente.barrio,
            "direccion": cliente.direccion,
        }
        insert_stmt = pg_insert(ClienteModel).values(**values)
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[ClienteModel.numero_cliente],
            index_where=ClienteModel.deleted_at.is_(None),
            set_={key: value for key, value in values.items() if key != "id"},
        )
        try:
            async with self._session.begin_nested():
                await self._session.execute(upsert_stmt)
        except IntegrityError as error:
            raise ClienteConflictError(
                str(error.orig) if error.orig is not None else str(error)
            ) from error

    async def resurrect(self, cliente: Cliente) -> None:
        """Revive a soft-deleted row by `cliente.id`, clearing `deleted_at` and writing every
        mutable field from `cliente` -- see `ClienteRepository.resurrect()`'s docstring
        (domain/ports.py) for why this cannot reuse `save()`'s `ON CONFLICT` upsert: a dead row
        (`deleted_at IS NOT NULL`) is excluded from `uq_clientes_numero_cliente`'s partial index,
        so an `INSERT ... ON CONFLICT (numero_cliente) WHERE deleted_at IS NULL` cannot target it
        -- inserting the same `id` again would instead collide with the primary key, a different,
        unhandled constraint. A plain `UPDATE ... WHERE id = :id` sidesteps that entirely.

        Same atomicity guarantees as `save()`: does not commit, wrapped in its own `SAVEPOINT`. If
        a concurrent transaction claims this `numero_cliente` for an active row before this one
        commits, `uq_clientes_numero_cliente` still catches it (this row's `deleted_at` is being
        set to `NULL` by this very statement) -- surfaces as `ClienteConflictError`, degrading to
        a per-record rejection instead of corrupting the partial unique index's guarantee.

        The `WHERE` clause also requires `deleted_at IS NOT NULL` (in addition to matching `id`),
        and the affected row count is checked afterwards: two concurrent resurrections of the
        SAME dead row are otherwise a lost-update race -- with no such guard, both `UPDATE`s
        match unconditionally, both "succeed", and whichever commits last silently wins with no
        error raised at all. Requiring the row to still be dead means the loser's `UPDATE`
        matches zero rows (the winner already cleared `deleted_at` first), which is detected via
        `result.rowcount == 0` and raised as `ClienteConflictError` -- the same per-record
        rejection outcome as the natural-key race above, not a silent overwrite.
        """
        values = {
            "numero_cliente": cliente.numero_cliente,
            "nombre": cliente.nombre,
            "estado": cliente.estado.value,
            "documento": cliente.documento,
            "localidad": cliente.localidad,
            "barrio": cliente.barrio,
            "direccion": cliente.direccion,
            "deleted_at": None,
        }
        stmt = (
            update(ClienteModel)
            .where(ClienteModel.id == cliente.id, ClienteModel.deleted_at.is_not(None))
            .values(**values)
        )
        try:
            async with self._session.begin_nested():
                result = cast(CursorResult[None], await self._session.execute(stmt))
                if result.rowcount == 0:
                    raise ClienteConflictError(
                        f"cliente {cliente.id} is no longer soft-deleted -- a concurrent "
                        "resurrection already claimed it"
                    )
        except IntegrityError as error:
            raise ClienteConflictError(
                str(error.orig) if error.orig is not None else str(error)
            ) from error

    async def list_active(self, *, limit: int, offset: int) -> list[Cliente]:
        stmt = (
            select(ClienteModel)
            .where(ClienteModel.deleted_at.is_(None))
            .order_by(ClienteModel.numero_cliente)
            .limit(limit)
            .offset(offset)
        )
        models = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain(model) for model in models]

    async def count_active(self) -> int:
        stmt = (
            select(func.count()).select_from(ClienteModel).where(ClienteModel.deleted_at.is_(None))
        )
        return (await self._session.execute(stmt)).scalar_one()


def _to_domain(model: ClienteModel) -> Cliente:
    return Cliente(
        id=model.id,
        numero_cliente=model.numero_cliente,
        nombre=model.nombre,
        estado=EstadoCliente(model.estado),
        documento=model.documento,
        localidad=model.localidad,
        barrio=model.barrio,
        direccion=model.direccion,
    )
