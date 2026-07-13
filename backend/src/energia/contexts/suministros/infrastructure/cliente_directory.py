"""SqlDirectClienteDirectory: the `ClienteDirectory` port implementation (ADR-006 cross-context
lookup shortcut).

`suministros` needs to resolve a `numero_cliente` natural key to a `cliente_id` UUID to satisfy
the `suministros.cliente_id` foreign key, but must not import anything from `contexts.clientes`
(ADR-001 module boundaries: bounded contexts only talk to each other through explicit ports, not
by reaching into each other's domain/application/infrastructure code). ADR-006 sanctions exactly
this situation for a modular monolith sharing one physical database: instead of an in-process
call into another context's repository, this adapter runs a direct, explicit SQL query against
the `clientes` table.

Deliberately raw SQL (`sqlalchemy.text`), not an ORM model duplicating `contexts.clientes.
infrastructure.models.ClienteModel`: a shadow ORM mapping of the same table would silently
double the places that table's shape is declared, and (worse) invite this context to depend on
`clientes`' column set evolving in lockstep, which is exactly the coupling the port exists to
avoid. A single, narrow, explicit query is the smaller and more honest surface for what this
adapter actually needs (one column, by one natural key). See `contexts/README.md`
("cross-context directory-port pattern") for the convention this establishes for future
contexts (lecturas, consumos, lotes, ...).
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class SqlDirectClienteDirectory:
    """`ClienteDirectory` (domain/ports.py) backed by a direct SQL query, same session/
    transaction as the rest of the request -- so a resolution and the suministro write it feeds
    read-consistent data, without a second connection or a cross-context in-process call.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(self, numero_cliente: str) -> UUID | None:
        stmt = text(
            "SELECT id FROM clientes WHERE numero_cliente = :numero_cliente AND deleted_at IS NULL"
        )
        result = await self._session.execute(stmt, {"numero_cliente": numero_cliente})
        row = result.first()
        return row[0] if row is not None else None
