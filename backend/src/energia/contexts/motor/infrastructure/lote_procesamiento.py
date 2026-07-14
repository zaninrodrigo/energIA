"""SqlLoteProcesamientoPort: `LoteProcesamientoPort`'s implementation (ADR-006 cross-context
write shortcut).

Deliberately raw SQL (`sqlalchemy.text`), never an ORM model of `lotes`/`consumos`: neither table
belongs to `motor` (DOMAIN_MODEL.md §4.4) -- mirrors
`contexts.consumos.infrastructure.suministro_directory.SqlDirectSuministroDirectory`'s rationale,
extended to a WRITE (`transicionar_estado`), not just a read -- see `domain/ports.py`'s module
docstring.
"""

from typing import cast
from uuid import UUID

from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.motor.domain.lote_estado import EstadoLote
from energia.contexts.motor.domain.ports import EstadoLoteActual


class SqlLoteProcesamientoPort:
    """`LoteProcesamientoPort` (domain/ports.py) backed by direct SQL queries/updates, same
    session/transaction as the rest of the request -- so a read and the transition it feeds see
    read-consistent data, without a second connection or a cross-context in-process call."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def obtener_estado_actual(self, codigo_lote: str) -> EstadoLoteActual | None:
        stmt = text(
            "SELECT id, codigo_lote, estado, cantidad_registros FROM lotes "
            "WHERE codigo_lote = :codigo_lote AND deleted_at IS NULL"
        )
        result = await self._session.execute(stmt, {"codigo_lote": codigo_lote})
        row = result.first()
        if row is None:
            return None
        return EstadoLoteActual(
            id=row.id,
            codigo_lote=row.codigo_lote,
            estado=EstadoLote(row.estado),
            cantidad_registros=row.cantidad_registros,
        )

    async def contar_consumos_activos(self, lote_id: UUID) -> int:
        stmt = text("SELECT count(*) FROM consumos WHERE lote_id = :lote_id AND deleted_at IS NULL")
        result = await self._session.execute(stmt, {"lote_id": lote_id})
        return result.scalar_one()  # type: ignore[no-any-return]

    async def transicionar_estado(
        self, lote_id: UUID, *, desde: frozenset[EstadoLote], hacia: EstadoLote
    ) -> bool:
        """Optimistic `UPDATE ... WHERE id = :lote_id AND estado IN (...)`: named placeholders
        built explicitly per state in `desde` (never a raw Python list bound to a single `:desde`
        placeholder -- that would rely on the asyncpg driver auto-encoding a list as a Postgres
        array for `= ANY(:desde)`, an implicit behavior this module avoids in favor of an
        explicit, always-correct `IN (...)` list). `desde` is always small (at most the 4
        `EstadoLote` values), so building one placeholder per element is cheap and exact.
        """
        placeholders = ", ".join(f":desde_{i}" for i in range(len(desde)))
        stmt = text(
            f"UPDATE lotes SET estado = :hacia "
            f"WHERE id = :lote_id AND estado IN ({placeholders}) AND deleted_at IS NULL"
        )
        params: dict[str, object] = {"lote_id": lote_id, "hacia": hacia.value}
        params.update({f"desde_{i}": estado.value for i, estado in enumerate(desde)})
        result = cast(CursorResult[None], await self._session.execute(stmt, params))
        return result.rowcount > 0
