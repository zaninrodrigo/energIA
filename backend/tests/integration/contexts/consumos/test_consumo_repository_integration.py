"""Integration tests for SqlAlchemyConsumoRepository against the real `energia_test` database.
Never touches `energia` -- see tests/integration/conftest.py for the isolation strategy. Mirrors
`test_lectura_repository_integration.py`, adapted to `Consumo`'s composite natural key
`(suministro_id, fecha_inicio, fecha_fin)` and to the partitioned `consumos` table.
"""

import asyncio
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from energia.contexts.consumos.application.import_consumos import ImportConsumos, ImportSummary
from energia.contexts.consumos.domain.consumo import Consumo
from energia.contexts.consumos.domain.ports import ConsumoConflictError, ConsumoSourceRecord
from energia.contexts.consumos.infrastructure.consumo_repository import SqlAlchemyConsumoRepository
from energia.contexts.consumos.infrastructure.json_consumo_source import JsonConsumoSource
from energia.contexts.consumos.infrastructure.lectura_repository import SqlAlchemyLecturaRepository
from energia.contexts.consumos.infrastructure.lote_directory import SqlAlchemyLoteDirectory
from energia.contexts.consumos.infrastructure.suministro_directory import (
    SqlDirectSuministroDirectory,
)
from tests.integration.contexts.consumos.conftest import insert_lote, insert_suministro

pytestmark = pytest.mark.integration


async def test_save_then_get_by_suministro_and_periodo_round_trips_a_new_consumo(
    db_session: AsyncSession, suministro_id: UUID, lote_id: UUID
) -> None:
    repository = SqlAlchemyConsumoRepository(db_session)
    consumo = Consumo.create(
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio="2024-01-01",
        fecha_fin="2024-01-31",
        dias_facturados=31,
        kwh="310.000",
    )

    await repository.save(consumo)
    found = await repository.get_by_suministro_and_periodo(
        suministro_id, date(2024, 1, 1), date(2024, 1, 31)
    )

    assert found is not None
    assert found.id == consumo.id
    assert found.suministro_id == suministro_id
    assert found.lote_id == lote_id
    assert found.lectura_id is None
    assert found.fecha_inicio == date(2024, 1, 1)
    assert found.fecha_fin == date(2024, 1, 31)
    assert found.dias_facturados == 31
    assert found.kwh == Decimal("310.000")
    assert found.consumo_promedio_diario == Decimal("10.000")


async def test_get_by_suministro_and_periodo_returns_none_when_absent(
    db_session: AsyncSession, suministro_id: UUID
) -> None:
    repository = SqlAlchemyConsumoRepository(db_session)

    found = await repository.get_by_suministro_and_periodo(
        suministro_id, date(2099, 1, 1), date(2099, 1, 31)
    )
    assert found is None


async def test_save_updates_an_existing_row_by_id(
    db_session: AsyncSession, suministro_id: UUID, lote_id: UUID
) -> None:
    repository = SqlAlchemyConsumoRepository(db_session)
    original = Consumo.create(
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio="2024-02-01",
        fecha_fin="2024-02-29",
        dias_facturados=29,
        kwh="100.000",
    )
    await repository.save(original)

    updated = Consumo.create(
        id=original.id,
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio="2024-02-01",
        fecha_fin="2024-02-29",
        dias_facturados=29,
        kwh="200.000",
    )
    await repository.save(updated)

    found = await repository.get_by_suministro_and_periodo(
        suministro_id, date(2024, 2, 1), date(2024, 2, 29)
    )
    assert found is not None
    assert found.id == original.id
    assert found.kwh == Decimal("200.000")


async def test_list_active_excludes_soft_deleted_consumos(
    db_session: AsyncSession, suministro_id: UUID, lote_id: UUID
) -> None:
    repository = SqlAlchemyConsumoRepository(db_session)
    await repository.save(
        Consumo.create(
            suministro_id=suministro_id,
            lote_id=lote_id,
            fecha_inicio="2024-01-01",
            fecha_fin="2024-01-31",
            dias_facturados=31,
            kwh="100.000",
        )
    )
    await repository.save(
        Consumo.create(
            suministro_id=suministro_id,
            lote_id=lote_id,
            fecha_inicio="2024-02-01",
            fecha_fin="2024-02-29",
            dias_facturados=29,
            kwh="100.000",
        )
    )
    await db_session.execute(
        text("UPDATE consumos SET deleted_at = now() WHERE fecha_inicio = '2024-02-01'")
    )
    await db_session.commit()

    active = await repository.list_active(limit=50, offset=0)

    periodos = {consumo.fecha_inicio for consumo in active}
    assert date(2024, 1, 1) in periodos
    assert date(2024, 2, 1) not in periodos


async def test_list_active_filters_by_suministro_id_and_lote_id(
    db_session: AsyncSession,
) -> None:
    repository = SqlAlchemyConsumoRepository(db_session)
    suministro_a = await insert_suministro(db_session, numero_suministro="CON-A")
    suministro_b = await insert_suministro(db_session, numero_suministro="CON-B")
    lote_a = await insert_lote(db_session, codigo_lote="LOTE-CON-A")
    lote_b = await insert_lote(db_session, codigo_lote="LOTE-CON-B")
    await repository.save(
        Consumo.create(
            suministro_id=suministro_a,
            lote_id=lote_a,
            fecha_inicio="2024-01-01",
            fecha_fin="2024-01-31",
            dias_facturados=31,
            kwh="100.000",
        )
    )
    await repository.save(
        Consumo.create(
            suministro_id=suministro_b,
            lote_id=lote_b,
            fecha_inicio="2024-01-01",
            fecha_fin="2024-01-31",
            dias_facturados=31,
            kwh="200.000",
        )
    )

    by_suministro = await repository.list_active(limit=50, offset=0, suministro_id=suministro_a)
    by_lote = await repository.list_active(limit=50, offset=0, lote_id=lote_b)

    assert {consumo.suministro_id for consumo in by_suministro} == {suministro_a}
    assert {consumo.lote_id for consumo in by_lote} == {lote_b}


async def test_list_active_orders_by_fecha_inicio_desc_then_id(
    db_session: AsyncSession, suministro_id: UUID, lote_id: UUID
) -> None:
    repository = SqlAlchemyConsumoRepository(db_session)
    for fecha_inicio, fecha_fin in [
        ("2024-03-01", "2024-03-31"),
        ("2024-01-01", "2024-01-31"),
        ("2024-02-01", "2024-02-29"),
    ]:
        await repository.save(
            Consumo.create(
                suministro_id=suministro_id,
                lote_id=lote_id,
                fecha_inicio=fecha_inicio,
                fecha_fin=fecha_fin,
                dias_facturados=28,
                kwh="100.000",
            )
        )

    ordered = await repository.list_active(limit=50, offset=0)

    assert [consumo.fecha_inicio for consumo in ordered] == [
        date(2024, 3, 1),
        date(2024, 2, 1),
        date(2024, 1, 1),
    ]


async def test_count_active_counts_only_non_deleted_consumos(
    db_session: AsyncSession, suministro_id: UUID, lote_id: UUID
) -> None:
    repository = SqlAlchemyConsumoRepository(db_session)
    await repository.save(
        Consumo.create(
            suministro_id=suministro_id,
            lote_id=lote_id,
            fecha_inicio="2024-01-01",
            fecha_fin="2024-01-31",
            dias_facturados=31,
            kwh="100.000",
        )
    )
    await repository.save(
        Consumo.create(
            suministro_id=suministro_id,
            lote_id=lote_id,
            fecha_inicio="2024-02-01",
            fecha_fin="2024-02-29",
            dias_facturados=29,
            kwh="100.000",
        )
    )
    await db_session.execute(
        text("UPDATE consumos SET deleted_at = now() WHERE fecha_inicio = '2024-02-01'")
    )
    await db_session.commit()

    assert await repository.count_active() == 1


async def test_save_does_not_commit_the_transaction(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`save()` only flushes; the caller (route/use-case boundary) controls the commit."""
    async with test_session_factory() as session:
        suministro = await insert_suministro(session, numero_suministro="CON-NOCOMMIT")
        lote = await insert_lote(session, codigo_lote="LOTE-NOCOMMIT")
        repository = SqlAlchemyConsumoRepository(session)
        await repository.save(
            Consumo.create(
                suministro_id=suministro,
                lote_id=lote,
                fecha_inicio="2024-01-01",
                fecha_fin="2024-01-31",
                dias_facturados=31,
                kwh="100.000",
            )
        )
        # Deliberately no `await session.commit()` here.

    async with test_session_factory() as verify_session:
        verify_repository = SqlAlchemyConsumoRepository(verify_session)
        assert (
            await verify_repository.get_by_suministro_and_periodo(
                suministro, date(2024, 1, 1), date(2024, 1, 31)
            )
            is None
        )


async def test_save_upserts_by_suministro_and_periodo_even_with_a_different_fresh_id(
    db_session: AsyncSession, suministro_id: UUID, lote_id: UUID
) -> None:
    repository = SqlAlchemyConsumoRepository(db_session)
    first = Consumo.create(
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio="2024-01-01",
        fecha_fin="2024-01-31",
        dias_facturados=31,
        kwh="100.000",
    )
    second = Consumo.create(
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio="2024-01-01",
        fecha_fin="2024-01-31",
        dias_facturados=31,
        kwh="150.000",
    )
    assert first.id != second.id

    await repository.save(first)
    await repository.save(second)

    found = await repository.get_by_suministro_and_periodo(
        suministro_id, date(2024, 1, 1), date(2024, 1, 31)
    )
    assert found is not None
    assert found.id == first.id
    assert found.kwh == Decimal("150.000")
    assert await repository.count_active() == 1


async def test_save_raises_consumo_conflict_error_on_a_genuine_integrity_violation(
    db_session: AsyncSession, suministro_id: UUID, lote_id: UUID
) -> None:
    """Deterministic real integrity conflict: two Consumos sharing the same `(id, fecha_inicio)`
    -- the composite primary key -- but a different `fecha_fin`, so the natural-key `ON CONFLICT`
    (over `(suministro_id, fecha_inicio, fecha_fin)`) does not resolve it. `save()` rolls back to
    its SAVEPOINT and translates it into `ConsumoConflictError`, leaving the rest of the session's
    transaction usable."""
    repository = SqlAlchemyConsumoRepository(db_session)
    shared_id = uuid4()
    await repository.save(
        Consumo.create(
            id=shared_id,
            suministro_id=suministro_id,
            lote_id=lote_id,
            fecha_inicio="2024-01-01",
            fecha_fin="2024-01-31",
            dias_facturados=31,
            kwh="100.000",
        )
    )

    with pytest.raises(ConsumoConflictError):
        await repository.save(
            Consumo.create(
                id=shared_id,
                suministro_id=suministro_id,
                lote_id=lote_id,
                fecha_inicio="2024-01-01",
                fecha_fin="2024-02-29",
                dias_facturados=29,
                kwh="100.000",
            )
        )

    await repository.save(
        Consumo.create(
            suministro_id=suministro_id,
            lote_id=lote_id,
            fecha_inicio="2024-03-01",
            fecha_fin="2024-03-31",
            dias_facturados=31,
            kwh="100.000",
        )
    )
    assert (
        await repository.get_by_suministro_and_periodo(
            suministro_id, date(2024, 3, 1), date(2024, 3, 31)
        )
        is not None
    )


async def test_concurrent_imports_of_the_same_new_key_do_not_crash(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reproduces the same TOCTOU race `lecturas`/`lotes` close: two competing sessions both
    decide the same brand-new `(suministro_id, fecha_inicio, fecha_fin)` does not exist yet, and
    both attempt to create it concurrently. `ON CONFLICT (suministro_id, fecha_inicio, fecha_fin)
    WHERE deleted_at IS NULL DO UPDATE` resolves it atomically on the *partitioned* table -- the
    partial unique index (debt #10, `docker/postgres/init/01_schema.sql`) propagates to every
    partition, so the arbiter still applies per-partition.
    """
    async with test_session_factory() as setup_session:
        suministro = await insert_suministro(setup_session, numero_suministro="CON-RACE")
        await insert_lote(setup_session, codigo_lote="LOTE-CON-RACE")

    async def _import_one() -> ImportSummary:
        async with test_session_factory() as session:
            use_case = ImportConsumos(
                repository=SqlAlchemyConsumoRepository(session),
                suministro_directory=SqlDirectSuministroDirectory(session),
                lote_directory=SqlAlchemyLoteDirectory(session),
                lectura_repository=SqlAlchemyLecturaRepository(session),
            )
            summary = await use_case.execute(
                JsonConsumoSource(
                    [
                        ConsumoSourceRecord(
                            numero_suministro="CON-RACE",
                            codigo_lote="LOTE-CON-RACE",
                            fecha_inicio="2024-01-01",
                            fecha_fin="2024-01-31",
                            dias_facturados=31,
                            kwh="100.000",
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
        repository = SqlAlchemyConsumoRepository(verify_session)
        assert await repository.count_active() == 1
        found = await repository.get_by_suministro_and_periodo(
            suministro, date(2024, 1, 1), date(2024, 1, 31)
        )
        assert found is not None


async def test_a_fecha_inicio_outside_2022_2026_lands_in_the_default_partition(
    db_session: AsyncSession, suministro_id: UUID, lote_id: UUID
) -> None:
    """`consumos_default` is the partition of last resort for any `fecha_inicio` outside the
    2022-2026 range the 01_schema.sql partitions cover -- historical files older than 2022 must
    still import successfully, not be rejected."""
    repository = SqlAlchemyConsumoRepository(db_session)
    consumo = Consumo.create(
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio="2019-06-01",
        fecha_fin="2019-06-30",
        dias_facturados=29,
        kwh="50.000",
    )

    await repository.save(consumo)

    result = await db_session.execute(
        text("SELECT tableoid::regclass::text FROM consumos WHERE id = :id"), {"id": consumo.id}
    )
    partition = result.scalar_one()
    assert partition == "consumos_default"
    found = await repository.get_by_suministro_and_periodo(
        suministro_id, date(2019, 6, 1), date(2019, 6, 30)
    )
    assert found is not None


# --- DECISION #9: resurrection -----------------------------------------------------------------


async def test_get_most_recently_deleted_by_suministro_and_periodo_picks_the_latest_deleted_at(
    db_session: AsyncSession, suministro_id: UUID, lote_id: UUID
) -> None:
    repository = SqlAlchemyConsumoRepository(db_session)
    older = Consumo.create(
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio="2024-06-01",
        fecha_fin="2024-06-30",
        dias_facturados=30,
        kwh="60.000",
    )
    await repository.save(older)
    await db_session.execute(
        text("UPDATE consumos SET deleted_at = now() - interval '2 hours' WHERE id = :id"),
        {"id": older.id},
    )
    newer = Consumo.create(
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio="2024-06-01",
        fecha_fin="2024-06-30",
        dias_facturados=30,
        kwh="90.000",
    )
    await repository.save(newer)
    await db_session.execute(
        text("UPDATE consumos SET deleted_at = now() - interval '1 hour' WHERE id = :id"),
        {"id": newer.id},
    )
    await db_session.commit()

    dead = await repository.get_most_recently_deleted_by_suministro_and_periodo(
        suministro_id, date(2024, 6, 1), date(2024, 6, 30)
    )

    assert dead is not None
    assert dead.id == newer.id


async def test_resurrect_clears_deleted_at_and_writes_every_mutable_field(
    db_session: AsyncSession, suministro_id: UUID, lote_id: UUID
) -> None:
    repository = SqlAlchemyConsumoRepository(db_session)
    original = Consumo.create(
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio="2024-07-01",
        fecha_fin="2024-07-31",
        dias_facturados=31,
        kwh="310.000",
    )
    await repository.save(original)
    await db_session.execute(
        text("UPDATE consumos SET deleted_at = now() WHERE id = :id"), {"id": original.id}
    )
    await db_session.commit()

    resurrected = Consumo.create(
        id=original.id,
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio="2024-07-01",
        fecha_fin="2024-07-31",
        dias_facturados=31,
        kwh="620.000",
    )
    await repository.resurrect(resurrected)

    found = await repository.get_by_suministro_and_periodo(
        suministro_id, date(2024, 7, 1), date(2024, 7, 31)
    )
    assert found is not None
    assert found.id == original.id
    assert found.kwh == Decimal("620.000")
    assert found.consumo_promedio_diario == Decimal("20.000")
    row = (
        await db_session.execute(
            text("SELECT deleted_at FROM consumos WHERE id = :id"), {"id": original.id}
        )
    ).one()
    assert row.deleted_at is None


async def test_resurrect_raises_consumo_conflict_error_on_a_natural_key_race(
    db_session: AsyncSession, suministro_id: UUID, lote_id: UUID
) -> None:
    """A dead row's own `(suministro_id, fecha_inicio, fecha_fin)` can be claimed by a
    concurrent active insert before its resurrection commits; the update that clears
    `deleted_at` back to a duplicate active period violates `uq_consumos_suministro_periodo` --
    surfaced as `ConsumoConflictError`, not an unhandled IntegrityError."""
    repository = SqlAlchemyConsumoRepository(db_session)
    dead = Consumo.create(
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio="2024-11-01",
        fecha_fin="2024-11-30",
        dias_facturados=30,
        kwh="10.000",
    )
    await repository.save(dead)
    await db_session.execute(
        text("UPDATE consumos SET deleted_at = now() WHERE id = :id"), {"id": dead.id}
    )
    await db_session.commit()
    await repository.save(
        Consumo.create(
            suministro_id=suministro_id,
            lote_id=lote_id,
            fecha_inicio="2024-11-01",
            fecha_fin="2024-11-30",
            dias_facturados=30,
            kwh="20.000",
        )
    )

    with pytest.raises(ConsumoConflictError):
        await repository.resurrect(
            Consumo.create(
                id=dead.id,
                suministro_id=suministro_id,
                lote_id=lote_id,
                fecha_inicio="2024-11-01",
                fecha_fin="2024-11-30",
                dias_facturados=30,
                kwh="30.000",
            )
        )


async def test_resurrect_raises_consumo_conflict_error_when_the_row_is_no_longer_dead(
    db_session: AsyncSession, suministro_id: UUID, lote_id: UUID
) -> None:
    """FIX 1 (lost-update race): two concurrent resurrections of the SAME dead row used to both
    "succeed" with no guard on `deleted_at` in `resurrect()`'s `WHERE` clause -- whichever commit
    landed last silently won, with no error raised at all. Simulated here by clearing
    `deleted_at` directly via SQL (standing in for "a concurrent session already resurrected this
    row first") before calling `resurrect()`: the `deleted_at IS NOT NULL` guard then matches
    zero rows, and the zero-`rowcount` check raises `ConsumoConflictError` instead of silently
    re-applying (and overwriting) whatever the winner wrote."""
    repository = SqlAlchemyConsumoRepository(db_session)
    dead = Consumo.create(
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio="2024-12-01",
        fecha_fin="2024-12-31",
        dias_facturados=31,
        kwh="10.000",
    )
    await repository.save(dead)
    await db_session.execute(
        text("UPDATE consumos SET deleted_at = now() WHERE id = :id"), {"id": dead.id}
    )
    await db_session.commit()
    # Simulates the concurrent winner: by the time this resurrect() call runs, the row is no
    # longer dead.
    await db_session.execute(
        text("UPDATE consumos SET deleted_at = NULL WHERE id = :id"), {"id": dead.id}
    )
    await db_session.commit()

    with pytest.raises(ConsumoConflictError):
        await repository.resurrect(
            Consumo.create(
                id=dead.id,
                suministro_id=suministro_id,
                lote_id=lote_id,
                fecha_inicio="2024-12-01",
                fecha_fin="2024-12-31",
                dias_facturados=31,
                kwh="999.000",
            )
        )

    found = await repository.get_by_suministro_and_periodo(
        suministro_id, date(2024, 12, 1), date(2024, 12, 31)
    )
    assert found is not None
    assert found.kwh == Decimal("10.000")


# --- DECISION #13: soft-delete correction path --------------------------------------------------


async def test_soft_delete_marks_an_active_consumo_deleted_and_returns_true(
    db_session: AsyncSession, suministro_id: UUID, lote_id: UUID
) -> None:
    repository = SqlAlchemyConsumoRepository(db_session)
    consumo = Consumo.create(
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio="2024-08-01",
        fecha_fin="2024-08-31",
        dias_facturados=31,
        kwh="100.000",
    )
    await repository.save(consumo)

    deleted = await repository.soft_delete(consumo.id)

    assert deleted is True
    assert (
        await repository.get_by_suministro_and_periodo(
            suministro_id, date(2024, 8, 1), date(2024, 8, 31)
        )
        is None
    )
    row = (
        await db_session.execute(
            text("SELECT deleted_at FROM consumos WHERE id = :id"), {"id": consumo.id}
        )
    ).one()
    assert row.deleted_at is not None


async def test_soft_delete_returns_false_for_an_unknown_id(db_session: AsyncSession) -> None:
    repository = SqlAlchemyConsumoRepository(db_session)

    assert await repository.soft_delete(uuid4()) is False


async def test_soft_delete_returns_false_for_an_already_deleted_consumo(
    db_session: AsyncSession, suministro_id: UUID, lote_id: UUID
) -> None:
    repository = SqlAlchemyConsumoRepository(db_session)
    consumo = Consumo.create(
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio="2024-09-01",
        fecha_fin="2024-09-30",
        dias_facturados=30,
        kwh="50.000",
    )
    await repository.save(consumo)
    assert await repository.soft_delete(consumo.id) is True

    assert await repository.soft_delete(consumo.id) is False


async def test_soft_delete_does_not_commit_the_transaction(
    test_session_factory: async_sessionmaker[AsyncSession],
    suministro_id: UUID,
    lote_id: UUID,
) -> None:
    async with test_session_factory() as session:
        repository = SqlAlchemyConsumoRepository(session)
        consumo = Consumo.create(
            suministro_id=suministro_id,
            lote_id=lote_id,
            fecha_inicio="2024-10-01",
            fecha_fin="2024-10-31",
            dias_facturados=31,
            kwh="70.000",
        )
        await repository.save(consumo)
        await session.commit()

        await repository.soft_delete(consumo.id)
        # Deliberately no commit here: closing the session below rolls back the soft-delete.

    async with test_session_factory() as verify_session:
        verify_repository = SqlAlchemyConsumoRepository(verify_session)
        found = await verify_repository.get_by_suministro_and_periodo(
            suministro_id, date(2024, 10, 1), date(2024, 10, 31)
        )
        assert found is not None
