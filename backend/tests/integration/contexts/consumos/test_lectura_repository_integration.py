"""Integration tests for SqlAlchemyLecturaRepository against the real `energia_test` database.
Never touches `energia` -- see tests/integration/conftest.py for the isolation strategy. Mirrors
`tests/integration/contexts/suministros/test_suministro_repository_integration.py`, adapted to
Lectura's composite natural key `(suministro_id, fecha_lectura)`.
"""

import asyncio
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from energia.contexts.consumos.application.import_lecturas import ImportLecturas, ImportSummary
from energia.contexts.consumos.domain.lectura import Lectura
from energia.contexts.consumos.domain.ports import LecturaConflictError, LecturaSourceRecord
from energia.contexts.consumos.infrastructure.json_lectura_source import JsonLecturaSource
from energia.contexts.consumos.infrastructure.lectura_repository import SqlAlchemyLecturaRepository
from energia.contexts.consumos.infrastructure.suministro_directory import (
    SqlDirectSuministroDirectory,
)
from tests.integration.contexts.consumos.conftest import insert_suministro

pytestmark = pytest.mark.integration


async def test_save_then_get_by_suministro_and_fecha_round_trips_a_new_lectura(
    db_session: AsyncSession, suministro_id: UUID
) -> None:
    repository = SqlAlchemyLecturaRepository(db_session)
    lectura = Lectura.create(
        suministro_id=suministro_id,
        fecha_lectura="2024-01-15",
        lectura_anterior="100.000",
        lectura_actual="150.500",
        dias_facturados=30,
    )

    await repository.save(lectura)
    found = await repository.get_by_suministro_and_fecha(suministro_id, date(2024, 1, 15))

    assert found is not None
    assert found.id == lectura.id
    assert found.suministro_id == suministro_id
    assert found.fecha_lectura == date(2024, 1, 15)
    assert found.lectura_anterior == Decimal("100.000")
    assert found.lectura_actual == Decimal("150.500")
    assert found.dias_facturados == 30


async def test_get_by_suministro_and_fecha_returns_none_when_absent(
    db_session: AsyncSession, suministro_id: UUID
) -> None:
    repository = SqlAlchemyLecturaRepository(db_session)

    assert await repository.get_by_suministro_and_fecha(suministro_id, date(2099, 1, 1)) is None


async def test_save_updates_an_existing_row_by_id(
    db_session: AsyncSession, suministro_id: UUID
) -> None:
    repository = SqlAlchemyLecturaRepository(db_session)
    original = Lectura.create(
        suministro_id=suministro_id,
        fecha_lectura="2024-02-01",
        lectura_anterior="100.000",
        lectura_actual="150.000",
        dias_facturados=30,
    )
    await repository.save(original)

    updated = Lectura.create(
        id=original.id,
        suministro_id=suministro_id,
        fecha_lectura="2024-02-01",
        lectura_anterior="100.000",
        lectura_actual="175.000",
        dias_facturados=31,
    )
    await repository.save(updated)

    found = await repository.get_by_suministro_and_fecha(suministro_id, date(2024, 2, 1))
    assert found is not None
    assert found.id == original.id
    assert found.lectura_actual == Decimal("175.000")
    assert found.dias_facturados == 31


async def test_the_same_fecha_for_two_different_suministros_does_not_collide(
    db_session: AsyncSession,
) -> None:
    repository = SqlAlchemyLecturaRepository(db_session)
    suministro_a = await insert_suministro(db_session, numero_suministro="LEC-A")
    suministro_b = await insert_suministro(db_session, numero_suministro="LEC-B")

    await repository.save(
        Lectura.create(
            suministro_id=suministro_a,
            fecha_lectura="2024-01-01",
            lectura_anterior="0.000",
            lectura_actual="10.000",
            dias_facturados=30,
        )
    )
    await repository.save(
        Lectura.create(
            suministro_id=suministro_b,
            fecha_lectura="2024-01-01",
            lectura_anterior="0.000",
            lectura_actual="20.000",
            dias_facturados=30,
        )
    )

    found_a = await repository.get_by_suministro_and_fecha(suministro_a, date(2024, 1, 1))
    found_b = await repository.get_by_suministro_and_fecha(suministro_b, date(2024, 1, 1))
    assert found_a is not None and found_a.lectura_actual == Decimal("10.000")
    assert found_b is not None and found_b.lectura_actual == Decimal("20.000")
    assert await repository.count_active() == 2


async def test_list_active_excludes_soft_deleted_lecturas(
    db_session: AsyncSession, suministro_id: UUID
) -> None:
    repository = SqlAlchemyLecturaRepository(db_session)
    await repository.save(
        Lectura.create(
            suministro_id=suministro_id,
            fecha_lectura="2024-01-01",
            lectura_anterior="0.000",
            lectura_actual="10.000",
            dias_facturados=30,
        )
    )
    await repository.save(
        Lectura.create(
            suministro_id=suministro_id,
            fecha_lectura="2024-02-01",
            lectura_anterior="10.000",
            lectura_actual="20.000",
            dias_facturados=31,
        )
    )
    await db_session.execute(
        text("UPDATE lecturas SET deleted_at = now() WHERE fecha_lectura = '2024-02-01'")
    )
    await db_session.commit()

    active = await repository.list_active(limit=50, offset=0)

    fechas = {lectura.fecha_lectura for lectura in active}
    assert date(2024, 1, 1) in fechas
    assert date(2024, 2, 1) not in fechas


# --- DECISION #9: resurrection ---------------------------------------------------------------


async def test_get_most_recently_deleted_by_suministro_and_fecha_picks_the_latest_deleted_at(
    db_session: AsyncSession, suministro_id: UUID
) -> None:
    repository = SqlAlchemyLecturaRepository(db_session)
    older = Lectura.create(
        suministro_id=suministro_id,
        fecha_lectura="2024-03-01",
        lectura_anterior="0.000",
        lectura_actual="1.000",
        dias_facturados=30,
    )
    await repository.save(older)
    await db_session.execute(
        text("UPDATE lecturas SET deleted_at = now() - interval '2 hours' WHERE id = :id"),
        {"id": older.id},
    )
    newer = Lectura.create(
        suministro_id=suministro_id,
        fecha_lectura="2024-03-01",
        lectura_anterior="0.000",
        lectura_actual="2.000",
        dias_facturados=30,
    )
    await repository.save(newer)
    await db_session.execute(
        text("UPDATE lecturas SET deleted_at = now() - interval '1 hour' WHERE id = :id"),
        {"id": newer.id},
    )
    await db_session.commit()

    dead = await repository.get_most_recently_deleted_by_suministro_and_fecha(
        suministro_id, date(2024, 3, 1)
    )

    assert dead is not None
    assert dead.id == newer.id


async def test_resurrect_clears_deleted_at_and_writes_every_mutable_field(
    db_session: AsyncSession, suministro_id: UUID
) -> None:
    repository = SqlAlchemyLecturaRepository(db_session)
    original = Lectura.create(
        suministro_id=suministro_id,
        fecha_lectura="2024-04-01",
        lectura_anterior="0.000",
        lectura_actual="10.000",
        dias_facturados=30,
    )
    await repository.save(original)
    await db_session.execute(
        text("UPDATE lecturas SET deleted_at = now() WHERE id = :id"), {"id": original.id}
    )
    await db_session.commit()

    resurrected = Lectura.create(
        id=original.id,
        suministro_id=suministro_id,
        fecha_lectura="2024-04-01",
        lectura_anterior="0.000",
        lectura_actual="25.000",
        dias_facturados=30,
    )
    await repository.resurrect(resurrected)

    found = await repository.get_by_suministro_and_fecha(suministro_id, date(2024, 4, 1))
    assert found is not None
    assert found.id == original.id
    assert found.lectura_actual == Decimal("25.000")
    row = (
        await db_session.execute(
            text("SELECT deleted_at FROM lecturas WHERE id = :id"), {"id": original.id}
        )
    ).one()
    assert row.deleted_at is None


async def test_resurrect_raises_lectura_conflict_error_on_a_natural_key_race(
    db_session: AsyncSession, suministro_id: UUID
) -> None:
    """A dead row's own `(suministro_id, fecha_lectura)` can be claimed by a concurrent active
    insert before its resurrection commits; the update that clears `deleted_at` back to a
    duplicate active key violates `uq_lecturas_suministro_fecha` -- surfaced as
    `LecturaConflictError`, not an unhandled IntegrityError."""
    repository = SqlAlchemyLecturaRepository(db_session)
    dead = Lectura.create(
        suministro_id=suministro_id,
        fecha_lectura="2024-05-01",
        lectura_anterior="0.000",
        lectura_actual="1.000",
        dias_facturados=30,
    )
    await repository.save(dead)
    await db_session.execute(
        text("UPDATE lecturas SET deleted_at = now() WHERE id = :id"), {"id": dead.id}
    )
    await db_session.commit()
    await repository.save(
        Lectura.create(
            suministro_id=suministro_id,
            fecha_lectura="2024-05-01",
            lectura_anterior="0.000",
            lectura_actual="2.000",
            dias_facturados=30,
        )
    )

    with pytest.raises(LecturaConflictError):
        await repository.resurrect(
            Lectura.create(
                id=dead.id,
                suministro_id=suministro_id,
                fecha_lectura="2024-05-01",
                lectura_anterior="0.000",
                lectura_actual="3.000",
                dias_facturados=30,
            )
        )


async def test_resurrect_raises_lectura_conflict_error_when_the_row_is_no_longer_dead(
    db_session: AsyncSession, suministro_id: UUID
) -> None:
    """FIX 1 (lost-update race): two concurrent resurrections of the SAME dead row used to both
    "succeed" with no guard on `deleted_at` in `resurrect()`'s `WHERE` clause -- whichever commit
    landed last silently won, with no error raised at all. Simulated here by clearing
    `deleted_at` directly via SQL (standing in for "a concurrent session already resurrected this
    row first") before calling `resurrect()`: the `deleted_at IS NOT NULL` guard then matches
    zero rows, and the zero-`rowcount` check raises `LecturaConflictError` instead of silently
    re-applying (and overwriting) whatever the winner wrote."""
    repository = SqlAlchemyLecturaRepository(db_session)
    dead = Lectura.create(
        suministro_id=suministro_id,
        fecha_lectura="2024-05-02",
        lectura_anterior="0.000",
        lectura_actual="1.000",
        dias_facturados=30,
    )
    await repository.save(dead)
    await db_session.execute(
        text("UPDATE lecturas SET deleted_at = now() WHERE id = :id"), {"id": dead.id}
    )
    await db_session.commit()
    # Simulates the concurrent winner: by the time this resurrect() call runs, the row is no
    # longer dead.
    await db_session.execute(
        text("UPDATE lecturas SET deleted_at = NULL WHERE id = :id"), {"id": dead.id}
    )
    await db_session.commit()

    with pytest.raises(LecturaConflictError):
        await repository.resurrect(
            Lectura.create(
                id=dead.id,
                suministro_id=suministro_id,
                fecha_lectura="2024-05-02",
                lectura_anterior="0.000",
                lectura_actual="999.000",
                dias_facturados=30,
            )
        )

    found = await repository.get_by_suministro_and_fecha(suministro_id, date(2024, 5, 2))
    assert found is not None
    assert found.lectura_actual == Decimal("1.000")


async def test_list_active_filters_by_suministro_id(db_session: AsyncSession) -> None:
    repository = SqlAlchemyLecturaRepository(db_session)
    suministro_a = await insert_suministro(db_session, numero_suministro="LEC-C")
    suministro_b = await insert_suministro(db_session, numero_suministro="LEC-D")
    await repository.save(
        Lectura.create(
            suministro_id=suministro_a,
            fecha_lectura="2024-01-01",
            lectura_anterior="0.000",
            lectura_actual="10.000",
            dias_facturados=30,
        )
    )
    await repository.save(
        Lectura.create(
            suministro_id=suministro_b,
            fecha_lectura="2024-01-01",
            lectura_anterior="0.000",
            lectura_actual="20.000",
            dias_facturados=30,
        )
    )

    filtered = await repository.list_active(limit=50, offset=0, suministro_id=suministro_a)

    assert {lectura.suministro_id for lectura in filtered} == {suministro_a}


async def test_list_active_orders_by_fecha_lectura_then_id(
    db_session: AsyncSession, suministro_id: UUID
) -> None:
    repository = SqlAlchemyLecturaRepository(db_session)
    for fecha in ("2024-03-01", "2024-01-01", "2024-02-01"):
        await repository.save(
            Lectura.create(
                suministro_id=suministro_id,
                fecha_lectura=fecha,
                lectura_anterior="0.000",
                lectura_actual="10.000",
                dias_facturados=30,
            )
        )

    ordered = await repository.list_active(limit=50, offset=0)

    assert [lectura.fecha_lectura for lectura in ordered] == [
        date(2024, 1, 1),
        date(2024, 2, 1),
        date(2024, 3, 1),
    ]


async def test_count_active_counts_only_non_deleted_lecturas(
    db_session: AsyncSession, suministro_id: UUID
) -> None:
    repository = SqlAlchemyLecturaRepository(db_session)
    await repository.save(
        Lectura.create(
            suministro_id=suministro_id,
            fecha_lectura="2024-01-01",
            lectura_anterior="0.000",
            lectura_actual="10.000",
            dias_facturados=30,
        )
    )
    await repository.save(
        Lectura.create(
            suministro_id=suministro_id,
            fecha_lectura="2024-02-01",
            lectura_anterior="10.000",
            lectura_actual="20.000",
            dias_facturados=31,
        )
    )
    await db_session.execute(
        text("UPDATE lecturas SET deleted_at = now() WHERE fecha_lectura = '2024-02-01'")
    )
    await db_session.commit()

    assert await repository.count_active() == 1


async def test_save_does_not_commit_the_transaction(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`save()` only flushes; the caller (route/use-case boundary) controls the commit."""
    async with test_session_factory() as session:
        suministro = await insert_suministro(session, numero_suministro="LEC-NOCOMMIT")
        repository = SqlAlchemyLecturaRepository(session)
        await repository.save(
            Lectura.create(
                suministro_id=suministro,
                fecha_lectura="2024-01-01",
                lectura_anterior="0.000",
                lectura_actual="10.000",
                dias_facturados=30,
            )
        )
        # Deliberately no `await session.commit()` here for the lectura itself.

    async with test_session_factory() as verify_session:
        verify_repository = SqlAlchemyLecturaRepository(verify_session)
        assert (
            await verify_repository.get_by_suministro_and_fecha(suministro, date(2024, 1, 1))
            is None
        )


async def test_save_upserts_by_suministro_and_fecha_even_with_a_different_fresh_id(
    db_session: AsyncSession, suministro_id: UUID
) -> None:
    repository = SqlAlchemyLecturaRepository(db_session)
    first = Lectura.create(
        suministro_id=suministro_id,
        fecha_lectura="2024-01-01",
        lectura_anterior="0.000",
        lectura_actual="10.000",
        dias_facturados=30,
    )
    second = Lectura.create(
        suministro_id=suministro_id,
        fecha_lectura="2024-01-01",
        lectura_anterior="0.000",
        lectura_actual="15.000",
        dias_facturados=30,
    )
    assert first.id != second.id

    await repository.save(first)
    await repository.save(second)

    found = await repository.get_by_suministro_and_fecha(suministro_id, date(2024, 1, 1))
    assert found is not None
    assert found.id == first.id
    assert found.lectura_actual == Decimal("15.000")
    assert await repository.count_active() == 1


async def test_save_raises_lectura_conflict_error_on_a_genuine_integrity_violation(
    db_session: AsyncSession, suministro_id: UUID
) -> None:
    """Deterministic real integrity conflict: two Lecturas sharing the same `id` -- a
    primary-key collision the natural-key ON CONFLICT clause does not resolve. `save()` rolls
    back to its SAVEPOINT and translates it into `LecturaConflictError`, leaving the rest of the
    session's transaction usable."""
    repository = SqlAlchemyLecturaRepository(db_session)
    shared_id = uuid4()
    await repository.save(
        Lectura.create(
            id=shared_id,
            suministro_id=suministro_id,
            fecha_lectura="2024-01-01",
            lectura_anterior="0.000",
            lectura_actual="10.000",
            dias_facturados=30,
        )
    )

    with pytest.raises(LecturaConflictError):
        await repository.save(
            Lectura.create(
                id=shared_id,
                suministro_id=suministro_id,
                fecha_lectura="2024-02-01",
                lectura_anterior="0.000",
                lectura_actual="10.000",
                dias_facturados=30,
            )
        )

    await repository.save(
        Lectura.create(
            suministro_id=suministro_id,
            fecha_lectura="2024-03-01",
            lectura_anterior="0.000",
            lectura_actual="10.000",
            dias_facturados=30,
        )
    )
    assert await repository.get_by_suministro_and_fecha(suministro_id, date(2024, 3, 1)) is not None
    assert await repository.get_by_suministro_and_fecha(suministro_id, date(2024, 2, 1)) is None


async def test_concurrent_imports_of_the_same_new_key_do_not_crash(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reproduces the same TOCTOU race `suministros` closes: two competing sessions both decide
    the same brand-new `(suministro_id, fecha_lectura)` does not exist yet, and both attempt to
    create it concurrently. `ON CONFLICT (suministro_id, fecha_lectura) WHERE deleted_at IS NULL
    DO UPDATE` resolves it atomically: the loser's INSERT blocks until the winner commits, then
    updates instead of raising an unhandled IntegrityError.
    """
    async with test_session_factory() as setup_session:
        suministro = await insert_suministro(setup_session, numero_suministro="LEC-RACE")

    async def _import_one() -> ImportSummary:
        async with test_session_factory() as session:
            use_case = ImportLecturas(
                repository=SqlAlchemyLecturaRepository(session),
                suministro_directory=SqlDirectSuministroDirectory(session),
            )
            summary = await use_case.execute(
                JsonLecturaSource(
                    [
                        LecturaSourceRecord(
                            numero_suministro="LEC-RACE",
                            fecha_lectura="2024-01-01",
                            lectura_anterior="0.000",
                            lectura_actual="10.000",
                            dias_facturados=30,
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
        repository = SqlAlchemyLecturaRepository(verify_session)
        assert await repository.count_active() == 1
        found = await repository.get_by_suministro_and_fecha(suministro, date(2024, 1, 1))
        assert found is not None
