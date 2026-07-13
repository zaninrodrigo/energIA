"""Integration tests for SqlAlchemyLoteRepository against the real `energia_test` database.
Never touches `energia` -- see tests/integration/conftest.py for the isolation strategy. Mirrors
`tests/integration/contexts/consumos/test_lectura_repository_integration.py`, adapted to `Lote`'s
single natural key `codigo_lote` (and its `estado` column).
"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from energia.contexts.consumos.application.import_lotes import ImportLotes, ImportSummary
from energia.contexts.consumos.domain.lote import EstadoLote, Lote
from energia.contexts.consumos.domain.ports import LoteConflictError, LoteSourceRecord
from energia.contexts.consumos.infrastructure.json_lote_source import JsonLoteSource
from energia.contexts.consumos.infrastructure.lote_repository import SqlAlchemyLoteRepository

pytestmark = pytest.mark.integration


async def test_save_then_get_by_codigo_lote_round_trips_a_new_lote(
    db_session: AsyncSession,
) -> None:
    repository = SqlAlchemyLoteRepository(db_session)
    lote = Lote.create(codigo_lote="LOTE-2024-01", nombre="Enero 2024", cantidad_registros=100)

    await repository.save(lote)
    found = await repository.get_by_codigo_lote("LOTE-2024-01")

    assert found is not None
    assert found.id == lote.id
    assert found.codigo_lote == "LOTE-2024-01"
    assert found.nombre == "Enero 2024"
    assert found.cantidad_registros == 100
    assert found.estado is EstadoLote.PENDIENTE


async def test_get_by_codigo_lote_returns_none_when_absent(db_session: AsyncSession) -> None:
    repository = SqlAlchemyLoteRepository(db_session)

    assert await repository.get_by_codigo_lote("does-not-exist") is None


async def test_save_updates_an_existing_row_by_id(db_session: AsyncSession) -> None:
    repository = SqlAlchemyLoteRepository(db_session)
    original = Lote.create(codigo_lote="LOTE-2024-02", cantidad_registros=100)
    await repository.save(original)

    updated = replace(original, cantidad_registros=200)
    await repository.save(updated)

    found = await repository.get_by_codigo_lote("LOTE-2024-02")
    assert found is not None
    assert found.id == original.id
    assert found.cantidad_registros == 200


async def test_save_persists_an_estado_on_the_initial_insert(db_session: AsyncSession) -> None:
    """`save()`'s INSERT path (a brand-new row, no conflict yet) still writes whatever `estado`
    the given `Lote` carries -- only the ON CONFLICT DO UPDATE path excludes it (FIX 3, see the
    test right below)."""
    repository = SqlAlchemyLoteRepository(db_session)
    lote = Lote.create(codigo_lote="LOTE-2024-03").transition_to(EstadoLote.PROCESANDO)

    await repository.save(lote)

    found = await repository.get_by_codigo_lote("LOTE-2024-03")
    assert found is not None
    assert found.estado is EstadoLote.PROCESANDO


async def test_save_never_overwrites_estado_on_an_existing_row(db_session: AsyncSession) -> None:
    """FIX 3: `save()`'s `ON CONFLICT DO UPDATE SET` deliberately excludes `estado` (and
    `fecha_importacion`) -- an update on an existing row never writes either column, no matter
    what the given `Lote` carries. This is a structural guarantee the repository itself enforces
    (see `save()`'s docstring), not merely a matter of `ImportLotes` (application layer) always
    happening to pass the right value forward -- see
    `test_save_never_reverts_a_concurrently_updated_estado` below for why that distinction
    matters when two sessions race."""
    repository = SqlAlchemyLoteRepository(db_session)
    lote = Lote.create(codigo_lote="LOTE-2024-03")
    await repository.save(lote)

    procesando = lote.transition_to(EstadoLote.PROCESANDO)
    await repository.save(procesando)

    found = await repository.get_by_codigo_lote("LOTE-2024-03")
    assert found is not None
    assert found.estado is EstadoLote.PENDIENTE


async def test_list_active_excludes_soft_deleted_lotes(db_session: AsyncSession) -> None:
    repository = SqlAlchemyLoteRepository(db_session)
    await repository.save(Lote.create(codigo_lote="LOTE-A"))
    await repository.save(Lote.create(codigo_lote="LOTE-B"))
    await db_session.execute(
        text("UPDATE lotes SET deleted_at = now() WHERE codigo_lote = 'LOTE-B'")
    )
    await db_session.commit()

    active = await repository.list_active(limit=50, offset=0)

    codigos = {lote.codigo_lote for lote in active}
    assert "LOTE-A" in codigos
    assert "LOTE-B" not in codigos


async def test_list_active_filters_by_estado(db_session: AsyncSession) -> None:
    repository = SqlAlchemyLoteRepository(db_session)
    pendiente = Lote.create(codigo_lote="LOTE-PEND")
    procesando = Lote.create(codigo_lote="LOTE-PROC").transition_to(EstadoLote.PROCESANDO)
    await repository.save(pendiente)
    await repository.save(procesando)

    filtered = await repository.list_active(limit=50, offset=0, estado=EstadoLote.PROCESANDO)

    assert {lote.codigo_lote for lote in filtered} == {"LOTE-PROC"}


async def test_list_active_orders_by_fecha_importacion_desc_then_id(
    db_session: AsyncSession,
) -> None:
    """Most recently imported batch first -- see `presentation/routes.py`'s `list_lotes`
    docstring for the business justification."""
    repository = SqlAlchemyLoteRepository(db_session)
    fechas = [
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 3, 1, tzinfo=UTC),
        datetime(2024, 2, 1, tzinfo=UTC),
    ]
    for index, fecha in enumerate(fechas):
        await repository.save(
            Lote.create(codigo_lote=f"LOTE-ORDER-{index}", fecha_importacion=fecha)
        )

    ordered = await repository.list_active(limit=50, offset=0)

    assert [lote.fecha_importacion for lote in ordered] == [
        datetime(2024, 3, 1, tzinfo=UTC),
        datetime(2024, 2, 1, tzinfo=UTC),
        datetime(2024, 1, 1, tzinfo=UTC),
    ]


async def test_count_active_counts_only_non_deleted_lotes(db_session: AsyncSession) -> None:
    repository = SqlAlchemyLoteRepository(db_session)
    await repository.save(Lote.create(codigo_lote="LOTE-C"))
    await repository.save(Lote.create(codigo_lote="LOTE-D"))
    await db_session.execute(
        text("UPDATE lotes SET deleted_at = now() WHERE codigo_lote = 'LOTE-D'")
    )
    await db_session.commit()

    assert await repository.count_active() == 1


async def test_count_active_filters_by_estado(db_session: AsyncSession) -> None:
    repository = SqlAlchemyLoteRepository(db_session)
    await repository.save(Lote.create(codigo_lote="LOTE-E"))
    await repository.save(Lote.create(codigo_lote="LOTE-F").transition_to(EstadoLote.PROCESANDO))

    assert await repository.count_active(estado=EstadoLote.PENDIENTE) == 1
    assert await repository.count_active(estado=EstadoLote.PROCESANDO) == 1


async def test_save_does_not_commit_the_transaction(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`save()` only flushes; the caller (route/use-case boundary) controls the commit."""
    async with test_session_factory() as session:
        repository = SqlAlchemyLoteRepository(session)
        await repository.save(Lote.create(codigo_lote="LOTE-NOCOMMIT"))
        # Deliberately no `await session.commit()` here.

    async with test_session_factory() as verify_session:
        verify_repository = SqlAlchemyLoteRepository(verify_session)
        assert await verify_repository.get_by_codigo_lote("LOTE-NOCOMMIT") is None


async def test_save_raises_lote_conflict_error_on_a_genuine_integrity_violation(
    db_session: AsyncSession,
) -> None:
    """Deterministic real integrity conflict: two Lotes sharing the same `id` -- a primary-key
    collision the natural-key ON CONFLICT clause does not resolve. `save()` rolls back to its
    SAVEPOINT and translates it into `LoteConflictError`, leaving the rest of the session's
    transaction usable."""
    repository = SqlAlchemyLoteRepository(db_session)
    shared_id = uuid4()
    await repository.save(Lote.create(id=shared_id, codigo_lote="LOTE-SHARED-1"))

    with pytest.raises(LoteConflictError):
        await repository.save(Lote.create(id=shared_id, codigo_lote="LOTE-SHARED-2"))

    await repository.save(Lote.create(codigo_lote="LOTE-SHARED-3"))
    assert await repository.get_by_codigo_lote("LOTE-SHARED-3") is not None
    assert await repository.get_by_codigo_lote("LOTE-SHARED-2") is None


async def test_concurrent_imports_of_the_same_new_key_do_not_crash(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reproduces the same TOCTOU race `lecturas`/`suministros` close: two competing sessions
    both decide the same brand-new `codigo_lote` does not exist yet, and both attempt to create
    it concurrently. `ON CONFLICT (codigo_lote) WHERE deleted_at IS NULL DO UPDATE` resolves it
    atomically: the loser's INSERT blocks until the winner commits, then updates instead of
    raising an unhandled IntegrityError.
    """

    async def _import_one() -> ImportSummary:
        async with test_session_factory() as session:
            use_case = ImportLotes(repository=SqlAlchemyLoteRepository(session))
            summary = await use_case.execute(
                JsonLoteSource([LoteSourceRecord(codigo_lote="LOTE-RACE", cantidad_registros=1)])
            )
            await session.commit()
            return summary

    summary_a, summary_b = await asyncio.gather(_import_one(), _import_one())

    assert summary_a.rejected == []
    assert summary_b.rejected == []
    async with test_session_factory() as verify_session:
        repository = SqlAlchemyLoteRepository(verify_session)
        assert await repository.count_active() == 1


async def test_save_never_reverts_a_concurrently_updated_estado(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """FIX 3, reviewer-reproduced lost-update race: session A reads an existing lote (a stale
    in-memory copy, `estado=Pendiente`); before session A writes anything back, a concurrent
    session commits `estado='Procesado'` on that same row (simulating the future processing
    engine finishing the batch). Session A then calls `save()` with its stale copy -- updating an
    unrelated field, `cantidad_registros` -- to persist an ordinary re-import. The final `estado`
    MUST stay `Procesado`: `save()`'s `ON CONFLICT DO UPDATE` must never write `estado` at all,
    regardless of what session A's stale in-memory `Lote` happens to carry.
    """
    async with test_session_factory() as setup_session:
        setup_repository = SqlAlchemyLoteRepository(setup_session)
        await setup_repository.save(
            Lote.create(codigo_lote="LOTE-RACE-ESTADO", cantidad_registros=1)
        )
        await setup_session.commit()

    async with test_session_factory() as session_a:
        repository_a = SqlAlchemyLoteRepository(session_a)
        stale = await repository_a.get_by_codigo_lote("LOTE-RACE-ESTADO")
        assert stale is not None
        assert stale.estado is EstadoLote.PENDIENTE

        async with test_session_factory() as concurrent_session:
            await concurrent_session.execute(
                text("UPDATE lotes SET estado = 'Procesado' WHERE codigo_lote = 'LOTE-RACE-ESTADO'")
            )
            await concurrent_session.commit()

        await repository_a.save(replace(stale, cantidad_registros=99))
        await session_a.commit()

    async with test_session_factory() as verify_session:
        verify_repository = SqlAlchemyLoteRepository(verify_session)
        found = await verify_repository.get_by_codigo_lote("LOTE-RACE-ESTADO")
        assert found is not None
        assert found.estado is EstadoLote.PROCESADO
        assert found.cantidad_registros == 99
