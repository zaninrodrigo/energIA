"""Integration tests for SqlAlchemySuministroRepository against the real `energia_test`
database. Never touches `energia` -- see tests/integration/conftest.py for the isolation
strategy. Mirrors `tests/integration/contexts/clientes/test_cliente_repository_integration.py`.
"""

import asyncio
from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from energia.contexts.suministros.application.import_suministros import (
    ImportSuministros,
    ImportSummary,
)
from energia.contexts.suministros.domain.ports import (
    SuministroConflictError,
    SuministroSourceRecord,
)
from energia.contexts.suministros.domain.suministro import Suministro
from energia.contexts.suministros.infrastructure.categoria_tarifaria_directory import (
    SqlAlchemyCategoriaTarifariaDirectory,
)
from energia.contexts.suministros.infrastructure.cliente_directory import SqlDirectClienteDirectory
from energia.contexts.suministros.infrastructure.json_suministro_source import JsonSuministroSource
from energia.contexts.suministros.infrastructure.suministro_repository import (
    SqlAlchemySuministroRepository,
)
from tests.integration.contexts.suministros.conftest import (
    get_categoria_tarifaria_id,
    insert_cliente,
)

pytestmark = pytest.mark.integration


async def test_save_then_get_by_numero_suministro_round_trips_a_new_suministro(
    db_session: AsyncSession, cliente_id: UUID, categoria_tarifaria_id: UUID
) -> None:
    repository = SqlAlchemySuministroRepository(db_session)
    suministro = Suministro.create(
        numero_suministro="S100",
        cliente_id=cliente_id,
        categoria_tarifaria_id=categoria_tarifaria_id,
        fecha_alta="2024-01-01",
    )

    await repository.save(suministro)
    found = await repository.get_by_numero_suministro("S100")

    assert found is not None
    assert found.id == suministro.id
    assert found.numero_suministro == "S100"
    assert found.cliente_id == cliente_id
    assert found.categoria_tarifaria_id == categoria_tarifaria_id
    assert found.fecha_alta == date(2024, 1, 1)
    assert found.estado == "Activo"


async def test_get_by_numero_suministro_returns_none_when_absent(db_session: AsyncSession) -> None:
    repository = SqlAlchemySuministroRepository(db_session)

    assert await repository.get_by_numero_suministro("does-not-exist") is None


async def test_save_updates_an_existing_row_by_id(
    db_session: AsyncSession, cliente_id: UUID, categoria_tarifaria_id: UUID
) -> None:
    repository = SqlAlchemySuministroRepository(db_session)
    original = Suministro.create(
        numero_suministro="S200",
        cliente_id=cliente_id,
        categoria_tarifaria_id=categoria_tarifaria_id,
        fecha_alta="2024-01-01",
        localidad="Old",
    )
    await repository.save(original)

    updated = Suministro.create(
        id=original.id,
        numero_suministro="S200",
        cliente_id=cliente_id,
        categoria_tarifaria_id=categoria_tarifaria_id,
        fecha_alta="2024-01-01",
        localidad="New",
    )
    await repository.save(updated)

    found = await repository.get_by_numero_suministro("S200")
    assert found is not None
    assert found.id == original.id
    assert found.localidad == "New"


async def test_list_active_excludes_soft_deleted_suministros(
    db_session: AsyncSession, cliente_id: UUID, categoria_tarifaria_id: UUID
) -> None:
    repository = SqlAlchemySuministroRepository(db_session)
    await repository.save(
        Suministro.create(
            numero_suministro="S300",
            cliente_id=cliente_id,
            categoria_tarifaria_id=categoria_tarifaria_id,
            fecha_alta="2024-01-01",
        )
    )
    await repository.save(
        Suministro.create(
            numero_suministro="S301",
            cliente_id=cliente_id,
            categoria_tarifaria_id=categoria_tarifaria_id,
            fecha_alta="2024-01-01",
        )
    )
    await db_session.execute(
        text("UPDATE suministros SET deleted_at = now() WHERE numero_suministro = 'S301'")
    )
    await db_session.commit()

    active = await repository.list_active(limit=50, offset=0)

    numeros = {s.numero_suministro for s in active}
    assert "S300" in numeros
    assert "S301" not in numeros


async def test_list_active_filters_by_cliente_id(
    db_session: AsyncSession, categoria_tarifaria_id: UUID
) -> None:
    repository = SqlAlchemySuministroRepository(db_session)
    cliente_a = await insert_cliente(db_session, numero_cliente="A1")
    cliente_b = await insert_cliente(db_session, numero_cliente="B1")
    await repository.save(
        Suministro.create(
            numero_suministro="S400",
            cliente_id=cliente_a,
            categoria_tarifaria_id=categoria_tarifaria_id,
            fecha_alta="2024-01-01",
        )
    )
    await repository.save(
        Suministro.create(
            numero_suministro="S401",
            cliente_id=cliente_b,
            categoria_tarifaria_id=categoria_tarifaria_id,
            fecha_alta="2024-01-01",
        )
    )

    filtered = await repository.list_active(limit=50, offset=0, cliente_id=cliente_a)

    assert {s.numero_suministro for s in filtered} == {"S400"}


async def test_list_active_honors_limit_and_offset(
    db_session: AsyncSession, cliente_id: UUID, categoria_tarifaria_id: UUID
) -> None:
    repository = SqlAlchemySuministroRepository(db_session)
    for n in range(5):
        await repository.save(
            Suministro.create(
                numero_suministro=f"S5{n}",
                cliente_id=cliente_id,
                categoria_tarifaria_id=categoria_tarifaria_id,
                fecha_alta="2024-01-01",
            )
        )

    page = await repository.list_active(limit=2, offset=1)

    assert len(page) == 2


async def test_count_active_counts_only_non_deleted_suministros(
    db_session: AsyncSession, cliente_id: UUID, categoria_tarifaria_id: UUID
) -> None:
    repository = SqlAlchemySuministroRepository(db_session)
    await repository.save(
        Suministro.create(
            numero_suministro="S600",
            cliente_id=cliente_id,
            categoria_tarifaria_id=categoria_tarifaria_id,
            fecha_alta="2024-01-01",
        )
    )
    await repository.save(
        Suministro.create(
            numero_suministro="S601",
            cliente_id=cliente_id,
            categoria_tarifaria_id=categoria_tarifaria_id,
            fecha_alta="2024-01-01",
        )
    )
    await db_session.execute(
        text("UPDATE suministros SET deleted_at = now() WHERE numero_suministro = 'S601'")
    )
    await db_session.commit()

    assert await repository.count_active() == 1


# --- DECISION #9: resurrection ---------------------------------------------------------------


async def test_get_most_recently_deleted_by_numero_suministro_picks_the_latest_deleted_at(
    db_session: AsyncSession, cliente_id: UUID, categoria_tarifaria_id: UUID
) -> None:
    repository = SqlAlchemySuministroRepository(db_session)
    older = Suministro.create(
        numero_suministro="S700",
        cliente_id=cliente_id,
        categoria_tarifaria_id=categoria_tarifaria_id,
        fecha_alta="2024-01-01",
    )
    await repository.save(older)
    await db_session.execute(
        text("UPDATE suministros SET deleted_at = now() - interval '2 hours' WHERE id = :id"),
        {"id": older.id},
    )
    newer = Suministro.create(
        numero_suministro="S700",
        cliente_id=cliente_id,
        categoria_tarifaria_id=categoria_tarifaria_id,
        fecha_alta="2024-01-01",
    )
    await repository.save(newer)
    await db_session.execute(
        text("UPDATE suministros SET deleted_at = now() - interval '1 hour' WHERE id = :id"),
        {"id": newer.id},
    )
    await db_session.commit()

    dead = await repository.get_most_recently_deleted_by_numero_suministro("S700")

    assert dead is not None
    assert dead.id == newer.id


async def test_resurrect_clears_deleted_at_and_writes_every_mutable_field(
    db_session: AsyncSession, cliente_id: UUID, categoria_tarifaria_id: UUID
) -> None:
    repository = SqlAlchemySuministroRepository(db_session)
    original = Suministro.create(
        numero_suministro="S701",
        cliente_id=cliente_id,
        categoria_tarifaria_id=categoria_tarifaria_id,
        fecha_alta="2024-01-01",
    )
    await repository.save(original)
    await db_session.execute(
        text("UPDATE suministros SET deleted_at = now() WHERE id = :id"), {"id": original.id}
    )
    await db_session.commit()

    resurrected = Suministro.create(
        id=original.id,
        numero_suministro="S701",
        cliente_id=cliente_id,
        categoria_tarifaria_id=categoria_tarifaria_id,
        fecha_alta="2024-01-01",
        localidad="Formosa",
    )
    await repository.resurrect(resurrected)

    found = await repository.get_by_numero_suministro("S701")
    assert found is not None
    assert found.id == original.id
    assert found.localidad == "Formosa"
    row = (
        await db_session.execute(
            text("SELECT deleted_at FROM suministros WHERE id = :id"), {"id": original.id}
        )
    ).one()
    assert row.deleted_at is None


async def test_resurrect_raises_suministro_conflict_error_on_a_natural_key_race(
    db_session: AsyncSession, cliente_id: UUID, categoria_tarifaria_id: UUID
) -> None:
    """A dead row's own `numero_suministro` can be claimed by a concurrent active insert before
    its resurrection commits; the update that clears `deleted_at` back to a duplicate active
    `numero_suministro` violates `uq_suministros_numero_suministro` -- surfaced as
    `SuministroConflictError`, not an unhandled IntegrityError. Mirrors
    `test_resurrect_raises_cliente_conflict_error_on_a_natural_key_race` exactly."""
    repository = SqlAlchemySuministroRepository(db_session)
    dead = Suministro.create(
        numero_suministro="S702",
        cliente_id=cliente_id,
        categoria_tarifaria_id=categoria_tarifaria_id,
        fecha_alta="2024-01-01",
    )
    await repository.save(dead)
    await db_session.execute(
        text("UPDATE suministros SET deleted_at = now() WHERE id = :id"), {"id": dead.id}
    )
    await db_session.commit()
    await repository.save(
        Suministro.create(
            numero_suministro="S702",
            cliente_id=cliente_id,
            categoria_tarifaria_id=categoria_tarifaria_id,
            fecha_alta="2024-01-01",
        )
    )

    with pytest.raises(SuministroConflictError):
        await repository.resurrect(
            Suministro.create(
                id=dead.id,
                numero_suministro="S702",
                cliente_id=cliente_id,
                categoria_tarifaria_id=categoria_tarifaria_id,
                fecha_alta="2024-01-01",
            )
        )


async def test_resurrect_raises_suministro_conflict_error_when_the_row_is_no_longer_dead(
    db_session: AsyncSession, cliente_id: UUID, categoria_tarifaria_id: UUID
) -> None:
    """FIX 1 (lost-update race): two concurrent resurrections of the SAME dead row used to both
    "succeed" with no guard on `deleted_at` in `resurrect()`'s `WHERE` clause -- whichever commit
    landed last silently won, with no error raised at all. Simulated here by clearing
    `deleted_at` directly via SQL (standing in for "a concurrent session already resurrected this
    row first") before calling `resurrect()`: the `deleted_at IS NOT NULL` guard then matches
    zero rows, and the zero-`rowcount` check raises `SuministroConflictError` instead of silently
    re-applying (and overwriting) whatever the winner wrote. Mirrors
    `test_resurrect_raises_cliente_conflict_error_when_the_row_is_no_longer_dead` exactly."""
    repository = SqlAlchemySuministroRepository(db_session)
    dead = Suministro.create(
        numero_suministro="S703",
        cliente_id=cliente_id,
        categoria_tarifaria_id=categoria_tarifaria_id,
        fecha_alta="2024-01-01",
    )
    await repository.save(dead)
    await db_session.execute(
        text("UPDATE suministros SET deleted_at = now() WHERE id = :id"), {"id": dead.id}
    )
    await db_session.commit()
    # Simulates the concurrent winner: by the time this resurrect() call runs, the row is no
    # longer dead.
    await db_session.execute(
        text("UPDATE suministros SET deleted_at = NULL WHERE id = :id"), {"id": dead.id}
    )
    await db_session.commit()

    with pytest.raises(SuministroConflictError):
        await repository.resurrect(
            Suministro.create(
                id=dead.id,
                numero_suministro="S703",
                cliente_id=cliente_id,
                categoria_tarifaria_id=categoria_tarifaria_id,
                fecha_alta="2024-01-01",
                localidad="Late Resurrection Attempt",
            )
        )

    found = await repository.get_by_numero_suministro("S703")
    assert found is not None
    assert found.localidad is None


async def test_save_does_not_commit_the_transaction(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`save()` only flushes; the caller (route/use-case boundary) controls the commit."""
    async with test_session_factory() as session:
        cliente = await insert_cliente(session, numero_cliente="9101")
        categoria = await get_categoria_tarifaria_id(session)
        repository = SqlAlchemySuministroRepository(session)
        await repository.save(
            Suministro.create(
                numero_suministro="S9101",
                cliente_id=cliente,
                categoria_tarifaria_id=categoria,
                fecha_alta="2024-01-01",
            )
        )
        # Deliberately no `await session.commit()` here for the suministro itself.

    async with test_session_factory() as verify_session:
        verify_repository = SqlAlchemySuministroRepository(verify_session)
        assert await verify_repository.get_by_numero_suministro("S9101") is None


async def test_save_upserts_by_numero_suministro_even_with_a_different_fresh_id(
    db_session: AsyncSession, cliente_id: UUID, categoria_tarifaria_id: UUID
) -> None:
    repository = SqlAlchemySuministroRepository(db_session)
    first = Suministro.create(
        numero_suministro="S9201",
        cliente_id=cliente_id,
        categoria_tarifaria_id=categoria_tarifaria_id,
        fecha_alta="2024-01-01",
        localidad="First",
    )
    second = Suministro.create(
        numero_suministro="S9201",
        cliente_id=cliente_id,
        categoria_tarifaria_id=categoria_tarifaria_id,
        fecha_alta="2024-01-01",
        localidad="Second",
    )
    assert first.id != second.id

    await repository.save(first)
    await repository.save(second)

    found = await repository.get_by_numero_suministro("S9201")
    assert found is not None
    assert found.id == first.id
    assert found.localidad == "Second"
    assert await repository.count_active() == 1


async def test_save_raises_suministro_conflict_error_on_a_genuine_integrity_violation(
    db_session: AsyncSession, cliente_id: UUID, categoria_tarifaria_id: UUID
) -> None:
    """Deterministic real integrity conflict: two Suministros sharing the same `id` -- a
    primary-key collision the natural-key ON CONFLICT clause does not resolve. `save()` rolls
    back to its SAVEPOINT and translates it into `SuministroConflictError`, leaving the rest of
    the session's transaction usable."""
    repository = SqlAlchemySuministroRepository(db_session)
    shared_id = uuid4()
    await repository.save(
        Suministro.create(
            id=shared_id,
            numero_suministro="S9301",
            cliente_id=cliente_id,
            categoria_tarifaria_id=categoria_tarifaria_id,
            fecha_alta="2024-01-01",
        )
    )

    with pytest.raises(SuministroConflictError):
        await repository.save(
            Suministro.create(
                id=shared_id,
                numero_suministro="S9302",
                cliente_id=cliente_id,
                categoria_tarifaria_id=categoria_tarifaria_id,
                fecha_alta="2024-01-01",
            )
        )

    await repository.save(
        Suministro.create(
            numero_suministro="S9303",
            cliente_id=cliente_id,
            categoria_tarifaria_id=categoria_tarifaria_id,
            fecha_alta="2024-01-01",
        )
    )
    assert await repository.get_by_numero_suministro("S9303") is not None
    assert await repository.get_by_numero_suministro("S9302") is None


async def test_concurrent_imports_of_the_same_new_numero_suministro_do_not_crash(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reproduces the same TOCTOU race `clientes` closes: two competing sessions both decide the
    same brand-new `numero_suministro` does not exist yet, and both attempt to create it
    concurrently. `ON CONFLICT (numero_suministro) WHERE deleted_at IS NULL DO UPDATE` resolves
    it atomically: the loser's INSERT blocks until the winner commits, then updates instead of
    raising an unhandled IntegrityError.
    """
    async with test_session_factory() as setup_session:
        await insert_cliente(setup_session, numero_cliente="9401")
        await get_categoria_tarifaria_id(setup_session)

    async def _import_one() -> ImportSummary:
        async with test_session_factory() as session:
            use_case = ImportSuministros(
                repository=SqlAlchemySuministroRepository(session),
                cliente_directory=SqlDirectClienteDirectory(session),
                categoria_directory=SqlAlchemyCategoriaTarifariaDirectory(session),
            )
            summary = await use_case.execute(
                JsonSuministroSource(
                    [
                        SuministroSourceRecord(
                            numero_suministro="S9401",
                            numero_cliente="9401",
                            categoria_tarifaria="Residencial",
                            fecha_alta="2024-01-01",
                        )
                    ]
                )
            )
            await session.commit()
            return summary

    summary_a, summary_b = await asyncio.gather(_import_one(), _import_one())

    assert summary_a.rejected == []
    assert summary_b.rejected == []
    async with test_session_factory() as verify_session:
        repository = SqlAlchemySuministroRepository(verify_session)
        assert await repository.count_active() == 1
        assert await repository.get_by_numero_suministro("S9401") is not None
