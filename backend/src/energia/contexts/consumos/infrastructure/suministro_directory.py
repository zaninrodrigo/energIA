"""SqlDirectSuministroDirectory: the `SuministroDirectory` port implementation (ADR-006
cross-context lookup shortcut).

`consumos` needs to resolve a `numero_suministro` natural key to a `suministro_id` UUID to
satisfy the `lecturas.suministro_id` foreign key, but must not import anything from
`contexts.suministros` (ADR-001 module boundaries: bounded contexts only talk to each other
through explicit ports, not by reaching into each other's domain/application/infrastructure
code). ADR-006 sanctions exactly this situation for a modular monolith sharing one physical
database: instead of an in-process call into another context's repository, this adapter runs a
direct, explicit SQL query against the `suministros` table -- mirrors
`contexts.suministros.infrastructure.cliente_directory.SqlDirectClienteDirectory` exactly, the
precedent `contexts/README.md` ("cross-context directory-port pattern") already names as the
convention future contexts (lecturas among them) should follow.

Deliberately raw SQL (`sqlalchemy.text`), not an ORM model duplicating
`contexts.suministros.infrastructure.models.SuministroModel`: a shadow ORM mapping of the same
table would silently double the places that table's shape is declared, and invite this context
to depend on `suministros`' column set evolving in lockstep, exactly the coupling the port exists
to avoid.

Resolves *active* suministros only, i.e. `deleted_at IS NULL` -- the same "non-soft-deleted"
meaning `ClienteDirectory` already uses, not `suministros.estado = 'Activo'` (a separate,
business-level status column `suministros.estado` also has, per DOMAIN_MODEL.md §7.2). A
soft-deleted suministro must not be importable against, the same way a soft-deleted cliente
cannot be resolved by `ClienteDirectory`.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class SqlDirectSuministroDirectory:
    """`SuministroDirectory` (domain/ports.py) backed by a direct SQL query, same session/
    transaction as the rest of the request -- so a resolution and the lectura write it feeds
    read-consistent data, without a second connection or a cross-context in-process call.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(self, numero_suministro: str) -> UUID | None:
        stmt = text(
            "SELECT id FROM suministros "
            "WHERE numero_suministro = :numero_suministro AND deleted_at IS NULL"
        )
        result = await self._session.execute(stmt, {"numero_suministro": numero_suministro})
        row = result.first()
        return row[0] if row is not None else None
