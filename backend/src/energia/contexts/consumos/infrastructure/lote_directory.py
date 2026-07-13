"""SqlAlchemyLoteDirectory: the `LoteDirectory` port implementation (US-004).

Unlike `SqlDirectSuministroDirectory` (suministro_directory.py), this queries `lotes` through the
ORM (`LoteModel`, infrastructure/models.py) rather than raw SQL: `Lote` belongs to the same bounded
context as `Consumo` (`DOMAIN_MODEL.md` §4.3, "Gestión de Consumos"), so there is no cross-context
boundary to route around here -- this is an ordinary same-context, read-only lookup, exactly the
role `SqlAlchemyCategoriaTarifariaDirectory` plays for `suministros`/`CategoriaTarifaria` (see
`contexts/README.md`, "cross-context directory-port pattern" -> "when a directory port is *not*
needed").
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.consumos.infrastructure.models import LoteModel


class SqlAlchemyLoteDirectory:
    """`LoteDirectory` (domain/ports.py) backed by SQLAlchemy's async session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(self, codigo_lote: str) -> UUID | None:
        stmt = select(LoteModel.id).where(
            LoteModel.codigo_lote == codigo_lote,
            LoteModel.deleted_at.is_(None),
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()
