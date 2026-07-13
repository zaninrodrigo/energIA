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


# --- DECISION #9: resurrection ---------------------------------------------------------------


async def test_get_most_recently_deleted_by_numero_cliente_returns_none_when_none_is_dead(
    db_session: AsyncSession,
) -> None:
    repository = SqlAlchemyClienteRepository(db_session)
    await repository.save(Cliente.create(numero_cliente="600", nombre="Active Only"))

    assert await repository.get_most_recently_deleted_by_numero_cliente("600") is None
    assert await repository.get_most_recently_deleted_by_numero_cliente("does-not-exist") is None


async def test_get_most_recently_deleted_by_numero_cliente_picks_the_latest_deleted_at(
    db_session: AsyncSession,
) -> None:
    """Two independently dead rows can share a `numero_cliente` (the partial unique index only
    forbids two *active* ones) -- the repository must pick the one with the greatest
    `deleted_at`, not simply the first/last one physically inserted."""
    repository = SqlAlchemyClienteRepository(db_session)
    older = Cliente.create(numero_cliente="601", nombre="Older Dead Row")
    await repository.save(older)
    await db_session.execute(
        text("UPDATE clientes SET deleted_at = now() - interval '2 hours' WHERE id = :id"),
        {"id": older.id},
    )
    newer = Cliente.create(numero_cliente="601", nombre="Newer Dead Row")
    await repository.save(newer)
    await db_session.execute(
        text("UPDATE clientes SET deleted_at = now() - interval '1 hour' WHERE id = :id"),
        {"id": newer.id},
    )
    await db_session.commit()

    dead = await repository.get_most_recently_deleted_by_numero_cliente("601")

    assert dead is not None
    assert dead.id == newer.id


async def test_resurrect_clears_deleted_at_and_writes_every_mutable_field(
    db_session: AsyncSession,
) -> None:
    repository = SqlAlchemyClienteRepository(db_session)
    original = Cliente.create(numero_cliente="602", nombre="Original")
    await repository.save(original)
    await db_session.execute(
        text("UPDATE clientes SET deleted_at = now() WHERE id = :id"), {"id": original.id}
    )
    await db_session.commit()

    resurrected = Cliente.create(
        id=original.id, numero_cliente="602", nombre="Resurrected", localidad="Formosa"
    )
    await repository.resurrect(resurrected)

    found = await repository.get_by_numero_cliente("602")
    assert found is not None
    assert found.id == original.id
    assert found.nombre == "Resurrected"
    assert found.localidad == "Formosa"
    row = (
        await db_session.execute(
            text("SELECT deleted_at FROM clientes WHERE id = :id"), {"id": original.id}
        )
    ).one()
    assert row.deleted_at is None


async def test_resurrect_does_not_commit_the_transaction(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Mirrors `test_save_does_not_commit_the_transaction`: `resurrect()` only flushes, the
    caller controls the commit."""
    async with test_session_factory() as session:
        repository = SqlAlchemyClienteRepository(session)
        original = Cliente.create(numero_cliente="603", nombre="Original")
        await repository.save(original)
        await session.execute(
            text("UPDATE clientes SET deleted_at = now() WHERE id = :id"), {"id": original.id}
        )
        await session.commit()

        await repository.resurrect(
            Cliente.create(id=original.id, numero_cliente="603", nombre="Resurrected")
        )
        # Deliberately no commit here: closing the session below rolls back the resurrection.

    async with test_session_factory() as verify_session:
        verify_repository = SqlAlchemyClienteRepository(verify_session)
        assert await verify_repository.get_by_numero_cliente("603") is None
        still_dead = await verify_repository.get_most_recently_deleted_by_numero_cliente("603")
        assert still_dead is not None


async def test_resurrect_raises_cliente_conflict_error_on_a_natural_key_race(
    db_session: AsyncSession,
) -> None:
    """A dead row's own `numero_cliente` can be claimed by a concurrent active insert before its
    resurrection commits; the update that clears `deleted_at` back to a duplicate active
    `numero_cliente` violates `uq_clientes_numero_cliente` -- surfaced as `ClienteConflictError`,
    not an unhandled IntegrityError."""
    repository = SqlAlchemyClienteRepository(db_session)
    dead = Cliente.create(numero_cliente="604", nombre="Original")
    await repository.save(dead)
    await db_session.execute(
        text("UPDATE clientes SET deleted_at = now() WHERE id = :id"), {"id": dead.id}
    )
    await db_session.commit()
    await repository.save(
        Cliente.create(numero_cliente="604", nombre="Someone Else Got Here First")
    )

    with pytest.raises(ClienteConflictError):
        await repository.resurrect(
            Cliente.create(id=dead.id, numero_cliente="604", nombre="Resurrected")
        )


async def test_resurrect_raises_cliente_conflict_error_when_the_row_is_no_longer_dead(
    db_session: AsyncSession,
) -> None:
    """FIX 1 (lost-update race): two concurrent resurrections of the SAME dead row used to both
    "succeed" with no guard on `deleted_at` in `resurrect()`'s `WHERE` clause -- whichever commit
    landed last silently won, with no error raised at all. Simulated here by clearing
    `deleted_at` directly via SQL (standing in for "a concurrent session already resurrected this
    row first") before calling `resurrect()`: the `deleted_at IS NOT NULL` guard then matches
    zero rows, and the zero-`rowcount` check raises `ClienteConflictError` -- a per-record
    rejection -- instead of silently re-applying (and overwriting) whatever the winner wrote."""
    repository = SqlAlchemyClienteRepository(db_session)
    dead = Cliente.create(numero_cliente="605", nombre="Original")
    await repository.save(dead)
    await db_session.execute(
        text("UPDATE clientes SET deleted_at = now() WHERE id = :id"), {"id": dead.id}
    )
    await db_session.commit()
    # Simulates the concurrent winner: by the time this resurrect() call runs, the row is no
    # longer dead.
    await db_session.execute(
        text("UPDATE clientes SET deleted_at = NULL WHERE id = :id"), {"id": dead.id}
    )
    await db_session.commit()

    with pytest.raises(ClienteConflictError):
        await repository.resurrect(
            Cliente.create(id=dead.id, numero_cliente="605", nombre="Late Resurrection Attempt")
        )

    # The loser's rejected attempt did not disturb the winner's already-active row.
    found = await repository.get_by_numero_cliente("605")
    assert found is not None
    assert found.nombre == "Original"


async def test_concurrent_resurrections_of_the_same_dead_row_one_wins_one_conflicts(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reproduces the reviewer's finding empirically, with two genuinely independent sessions
    (not the single-session simulation above): two competing sessions both race to resurrect the
    exact SAME dead row concurrently. Before FIX 1, `resurrect()`'s `UPDATE` carried no
    `deleted_at IS NOT NULL` guard, so both concurrent `UPDATE`s matched unconditionally and both
    "succeeded" with no error at all -- whichever committed last silently won (a lost update, not
    a crash). With the guard in place, Postgres serializes the two `UPDATE`s against the same row
    (the second blocks on the row lock until the first's transaction ends); once unblocked, the
    second's `WHERE deleted_at IS NOT NULL` is re-evaluated against the now-committed row (READ
    COMMITTED) and matches zero rows -- so exactly one of the two concurrent calls raises
    `ClienteConflictError`, never both, never neither."""
    async with test_session_factory() as setup_session:
        setup_repository = SqlAlchemyClienteRepository(setup_session)
        dead = Cliente.create(numero_cliente="9501", nombre="Original")
        await setup_repository.save(dead)
        await setup_session.execute(
            text("UPDATE clientes SET deleted_at = now() WHERE id = :id"), {"id": dead.id}
        )
        await setup_session.commit()

    async def _resurrect_once(nombre: str) -> str | None:
        async with test_session_factory() as session:
            repository = SqlAlchemyClienteRepository(session)
            try:
                await repository.resurrect(
                    Cliente.create(id=dead.id, numero_cliente="9501", nombre=nombre)
                )
            except ClienteConflictError as error:
                return str(error)
            await session.commit()
            return None

    result_a, result_b = await asyncio.gather(
        _resurrect_once("Winner Attempt"), _resurrect_once("Loser Attempt")
    )

    outcomes = [result_a, result_b]
    assert outcomes.count(None) == 1  # exactly one side committed successfully
    assert sum(1 for outcome in outcomes if outcome is not None) == 1  # the other raised
    async with test_session_factory() as verify_session:
        verify_repository = SqlAlchemyClienteRepository(verify_session)
        found = await verify_repository.get_by_numero_cliente("9501")
        assert found is not None
        assert found.nombre in ("Winner Attempt", "Loser Attempt")


async def test_get_most_recently_deleted_by_numero_cliente_breaks_deleted_at_ties_by_id_desc(
    db_session: AsyncSession,
) -> None:
    """FIX 2: two dead rows can legitimately share the exact same `deleted_at` (e.g. a bulk
    soft-delete in one statement) -- reproduced here by setting both to an identical literal
    timestamp. Without a tie-breaker, `ORDER BY deleted_at DESC` alone leaves which row comes
    first up to Postgres's unspecified scan order, not guaranteed stable across runs. `id DESC`
    is an arbitrary-but-stable secondary key that makes the pick deterministic; it carries no
    business meaning of its own -- this test only pins that the choice is reproducible, not that
    `id` means anything."""
    repository = SqlAlchemyClienteRepository(db_session)
    first = Cliente.create(numero_cliente="606", nombre="First")
    await repository.save(first)
    # `save()` upserts by natural key scoped to `deleted_at IS NULL`, so soft-delete `first`
    # before creating `second` -- otherwise the second `save()` would just update `first`'s row
    # instead of the two ending up as independently dead rows sharing one `numero_cliente`.
    await db_session.execute(
        text(
            "UPDATE clientes SET deleted_at = TIMESTAMPTZ '2024-01-01 00:00:00+00' WHERE id = :id"
        ),
        {"id": first.id},
    )
    second = Cliente.create(numero_cliente="606", nombre="Second")
    await repository.save(second)
    await db_session.execute(
        text(
            "UPDATE clientes SET deleted_at = TIMESTAMPTZ '2024-01-01 00:00:00+00' WHERE id = :id"
        ),
        {"id": second.id},
    )
    await db_session.commit()
    greater_id, lesser_id = sorted([first.id, second.id], reverse=True)

    dead = await repository.get_most_recently_deleted_by_numero_cliente("606")

    assert dead is not None
    assert dead.id == greater_id
    assert dead.id != lesser_id


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
