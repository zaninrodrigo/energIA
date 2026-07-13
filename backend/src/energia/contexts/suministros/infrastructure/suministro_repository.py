"""SqlAlchemySuministroRepository: the `SuministroRepository` port implementation (async)."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.suministros.domain.ports import SuministroConflictError
from energia.contexts.suministros.domain.suministro import Suministro
from energia.contexts.suministros.infrastructure.models import SuministroModel


class SqlAlchemySuministroRepository:
    """`SuministroRepository` (domain/ports.py) backed by SQLAlchemy's async session.

    The session is request-scoped, provided by `energia.shared.db.get_db_session` (wired onto
    `app.state` at lifespan startup, or overridden in tests) -- this class never builds its own
    engine or connection. Mirrors `contexts.clientes.infrastructure.cliente_repository.
    SqlAlchemyClienteRepository` exactly -- see that class's docstrings for the full rationale
    behind the upsert/savepoint/Core-statement choices repeated here.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_numero_suministro(self, numero_suministro: str) -> Suministro | None:
        stmt = select(SuministroModel).where(
            SuministroModel.numero_suministro == numero_suministro,
            SuministroModel.deleted_at.is_(None),
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_domain(model) if model is not None else None

    async def save(self, suministro: Suministro) -> None:
        """Insert or update, keyed by the natural key (`numero_suministro`, scoped to
        non-soft-deleted rows -- the exact partial unique index
        `uq_suministros_numero_suministro` enforces: `ON (numero_suministro) WHERE deleted_at IS
        NULL`). Same atomicity/race guarantees as `SqlAlchemyClienteRepository.save()`: does not
        commit (caller controls the transaction) and is wrapped in its own `SAVEPOINT`
        (`session.begin_nested()`) so a conflicting write only rolls back this one record.
        """
        values = {
            "id": suministro.id,
            "numero_suministro": suministro.numero_suministro,
            "cliente_id": suministro.cliente_id,
            "categoria_tarifaria_id": suministro.categoria_tarifaria_id,
            "localidad": suministro.localidad,
            "barrio": suministro.barrio,
            "estado": suministro.estado,
            "fecha_alta": suministro.fecha_alta,
        }
        insert_stmt = pg_insert(SuministroModel).values(**values)
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[SuministroModel.numero_suministro],
            index_where=SuministroModel.deleted_at.is_(None),
            set_={key: value for key, value in values.items() if key != "id"},
        )
        try:
            async with self._session.begin_nested():
                await self._session.execute(upsert_stmt)
        except IntegrityError as error:
            raise SuministroConflictError(
                str(error.orig) if error.orig is not None else str(error)
            ) from error

    async def list_active(
        self, *, limit: int, offset: int, cliente_id: UUID | None = None
    ) -> list[Suministro]:
        stmt = select(SuministroModel).where(SuministroModel.deleted_at.is_(None))
        if cliente_id is not None:
            stmt = stmt.where(SuministroModel.cliente_id == cliente_id)
        stmt = stmt.order_by(SuministroModel.numero_suministro).limit(limit).offset(offset)
        models = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain(model) for model in models]

    async def count_active(self, *, cliente_id: UUID | None = None) -> int:
        stmt = (
            select(func.count())
            .select_from(SuministroModel)
            .where(SuministroModel.deleted_at.is_(None))
        )
        if cliente_id is not None:
            stmt = stmt.where(SuministroModel.cliente_id == cliente_id)
        return (await self._session.execute(stmt)).scalar_one()


def _to_domain(model: SuministroModel) -> Suministro:
    return Suministro(
        id=model.id,
        numero_suministro=model.numero_suministro,
        cliente_id=model.cliente_id,
        categoria_tarifaria_id=model.categoria_tarifaria_id,
        fecha_alta=model.fecha_alta,
        estado=model.estado,
        localidad=model.localidad,
        barrio=model.barrio,
    )
