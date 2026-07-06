"""Integration tests for SqlAlchemyClienteRepository against the real `energia_test` database.

Never touches `energia` — see tests/integration/conftest.py for the isolation strategy.
"""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from energia.contexts.clientes.application.import_clientes import ImportClientes, ImportSummary
from energia.contexts.clientes.domain.cliente import Cliente, EstadoCliente
from energia.contexts.clientes.domain.ports import ClienteConflictError, ClienteSourceRecord
from energia.contexts.clientes.infrastructure.cliente_repository import SqlAlchemyClienteRepository
from energia.contexts.clientes.infrastructure.json_cliente_source import JsonClienteSource

pytestmark = pytest.mark.integration


async def test_save_then_get_by_numero_cliente_round_trips_a_new_cliente(
    db_session: AsyncSession,
) -> None:
    repository = SqlAlchemyClienteRepository(db_session)
    cliente = Cliente.create(numero_cliente="100", nombre="Juana Perez")

    await repository.save(cliente)
    found = await repository.get_by_numero_cliente("100")

    assert found is not None
    assert found.id == cliente.id
    assert found.numero_cliente == "100"
    assert found.nombre == "Juana Perez"
    assert found.estado == EstadoCliente.ACTIVO


async def test_get_by_numero_cliente_returns_none_when_absent(db_session: AsyncSession) -> None:
    repository = SqlAlchemyClienteRepository(db_session)

    assert await repository.get_by_numero_cliente("does-not-exist") is None


async def test_save_updates_an_existing_row_by_id(db_session: AsyncSession) -> None:
    repository = SqlAlchemyClienteRepository(db_session)
    original = Cliente.create(numero_cliente="200", nombre="Old Name")
    await repository.save(original)

    updated = Cliente.create(id=original.id, numero_cliente="200", nombre="New Name")
    await repository.save(updated)

    found = await repository.get_by_numero_cliente("200")
    assert found is not None
    assert found.id == original.id
    assert found.nombre == "New Name"


async def test_list_active_excludes_soft_deleted_clients(db_session: AsyncSession) -> None:
    repository = SqlAlchemyClienteRepository(db_session)
    await repository.save(Cliente.create(numero_cliente="300", nombre="Visible"))
    hidden = Cliente.create(numero_cliente="301", nombre="Hidden")
    await repository.save(hidden)
    await db_session.execute(
        text("UPDATE clientes SET deleted_at = now() WHERE numero_cliente = '301'")
    )
    await db_session.commit()

    active = await repository.list_active(limit=50, offset=0)

    numeros = {c.numero_cliente for c in active}
    assert "300" in numeros
    assert "301" not in numeros


async def test_list_active_honors_limit_and_offset(db_session: AsyncSession) -> None:
    repository = SqlAlchemyClienteRepository(db_session)
    for n in range(5):
        await repository.save(Cliente.create(numero_cliente=f"4{n}", nombre=f"Cliente {n}"))

    page = await repository.list_active(limit=2, offset=1)

    assert len(page) == 2


async def test_count_active_counts_only_non_deleted_clients(db_session: AsyncSession) -> None:
    repository = SqlAlchemyClienteRepository(db_session)
    await repository.save(Cliente.create(numero_cliente="500", nombre="A"))
    await repository.save(Cliente.create(numero_cliente="501", nombre="B"))
    await db_session.execute(
        text("UPDATE clientes SET deleted_at = now() WHERE numero_cliente = '501'")
    )
    await db_session.commit()

    assert await repository.count_active() == 1


# --- FIX 1: atomic batch + race-safe upsert + per-record integrity rejection ---------------


async def test_save_does_not_commit_the_transaction(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`save()` only flushes; the caller (route/use-case boundary) controls the commit. If the
    caller never commits, nothing persists -- proven here by deliberately never committing."""
    async with test_session_factory() as session:
        repository = SqlAlchemyClienteRepository(session)
        await repository.save(Cliente.create(numero_cliente="9101", nombre="Never Committed"))
        # Deliberately no `await session.commit()` here: closing the session below rolls back.

    async with test_session_factory() as verify_session:
        verify_repository = SqlAlchemyClienteRepository(verify_session)
        assert await verify_repository.get_by_numero_cliente("9101") is None


async def test_save_upserts_by_numero_cliente_even_with_a_different_fresh_id(
    db_session: AsyncSession,
) -> None:
    """`save()` conflicts on the natural key (`numero_cliente`, scoped to non-deleted rows) --
    not on `id` -- so calling it twice for the same numero_cliente with two different fresh ids
    (as two competing "this is a new client" decisions would) upserts over the first row
    instead of raising an integrity error, keeping the first row's id."""
    repository = SqlAlchemyClienteRepository(db_session)
    first = Cliente.create(numero_cliente="9201", nombre="First Attempt")
    second = Cliente.create(numero_cliente="9201", nombre="Second Attempt")
    assert first.id != second.id

    await repository.save(first)
    await repository.save(second)

    found = await repository.get_by_numero_cliente("9201")
    assert found is not None
    assert found.id == first.id
    assert found.nombre == "Second Attempt"
    assert await repository.count_active() == 1


async def test_save_raises_cliente_conflict_error_on_a_genuine_integrity_violation(
    db_session: AsyncSession,
) -> None:
    """A deterministic way to force a real database-level integrity conflict without relying on
    true concurrency: two Clientes that pass every domain invariant but share the same `id` --
    a primary-key collision the natural-key ON CONFLICT clause does not resolve (it only
    targets the `numero_cliente` partial unique index), so Postgres raises a genuine
    IntegrityError. `save()` rolls back to its SAVEPOINT and translates it into
    `ClienteConflictError` (ADR-001: no SQLAlchemy exception type crosses into
    domain/application), leaving the rest of the session's transaction usable."""
    repository = SqlAlchemyClienteRepository(db_session)
    shared_id = uuid4()
    await repository.save(Cliente.create(id=shared_id, numero_cliente="9301", nombre="First"))

    with pytest.raises(ClienteConflictError):
        await repository.save(Cliente.create(id=shared_id, numero_cliente="9302", nombre="Second"))

    # The savepoint rollback did not poison the rest of the transaction: further writes work.
    await repository.save(Cliente.create(numero_cliente="9303", nombre="Third"))
    assert await repository.get_by_numero_cliente("9303") is not None
    assert await repository.get_by_numero_cliente("9302") is None


async def test_concurrent_imports_of_the_same_new_numero_cliente_do_not_crash(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reproduces the TOCTOU race this fix closes: two competing sessions both decide the same
    brand-new `numero_cliente` does not exist yet (`get_by_numero_cliente` returns `None` for
    both, each on its own connection, before either has committed) and both attempt to create
    it concurrently.

    Before this fix (`ON CONFLICT (id)`), the loser's INSERT would violate
    `uq_clientes_numero_cliente` outright -- an unhandled IntegrityError (a 500 through the
    route, with the winner's record already committed alone). `ON CONFLICT (numero_cliente)
    WHERE deleted_at IS NULL DO UPDATE` is Postgres's documented atomic upsert: the loser's
    INSERT blocks until the winner commits, then gracefully updates the winner's row instead of
    erroring.

    Note what this test does *not* assert: each side's own `created`/`updated` count reflects
    its own `get_by_numero_cliente` pre-check, taken before either side committed -- both may
    genuinely report `created`, since from each side's point of view the row did not exist yet.
    That per-request count is inherently approximate under a true concurrent race; what this
    fix guarantees, and what matters, is that neither side crashes and the database ends up
    with exactly one row, not two conflicting ones.
    """

    async def _import_one() -> ImportSummary:
        async with test_session_factory() as session:
            use_case = ImportClientes(SqlAlchemyClienteRepository(session))
            summary = await use_case.execute(
                JsonClienteSource(
                    [ClienteSourceRecord(numero_cliente="9401", nombre="Race Participant")]
                )
            )
            await session.commit()
            return summary

    summary_a, summary_b = await asyncio.gather(_import_one(), _import_one())

    assert summary_a.rejected == []
    assert summary_b.rejected == []
    async with test_session_factory() as verify_session:
        repository = SqlAlchemyClienteRepository(verify_session)
        assert await repository.count_active() == 1
        assert await repository.get_by_numero_cliente("9401") is not None
