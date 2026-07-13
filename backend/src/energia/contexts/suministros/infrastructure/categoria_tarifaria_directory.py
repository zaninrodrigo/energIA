"""SqlAlchemyCategoriaTarifariaDirectory: the `CategoriaTarifariaDirectory` port implementation.

Unlike `SqlDirectClienteDirectory` (cliente_directory.py), this queries `categorias_tarifarias`
through the ORM (`CategoriaTarifariaModel`, infrastructure/models.py) rather than raw SQL:
`CategoriaTarifaria` belongs to the same bounded context as `Suministro` (DOMAIN_MODEL.md §4.2,
"Gestión de Suministros"), so there is no cross-context boundary to route around here -- this is
an ordinary same-context, read-only lookup, the same as any other query this context's
repository runs against its own tables.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.suministros.infrastructure.models import CategoriaTarifariaModel


class SqlAlchemyCategoriaTarifariaDirectory:
    """`CategoriaTarifariaDirectory` (domain/ports.py) backed by SQLAlchemy's async session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(self, nombre: str) -> UUID | None:
        stmt = select(CategoriaTarifariaModel.id).where(
            CategoriaTarifariaModel.nombre == nombre,
            CategoriaTarifariaModel.deleted_at.is_(None),
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()
