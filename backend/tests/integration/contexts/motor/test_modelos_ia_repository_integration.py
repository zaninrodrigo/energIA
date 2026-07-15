"""Integration tests for `SqlModelosIaRepository` (Etapa 6, AI_ENGINE_SPEC.md §9.4) -- the
upsert-by-`(nombre, version)` + single-Activo-per-`nombre` semantics against real `energia_test`
rows.

**FIX (reviewer finding, CRITICAL, 2026-07-15) — single-Activo race across concurrent lotes.**
`test_registrar_fit_concurrent_lotes_of_the_same_scope_leave_exactly_one_activo_row_behind` below
reproduces the race through the REAL repository (two real sessions, `asyncio.gather`) and proves
the transaction-scoped advisory lock (`infrastructure/modelos_ia_repository.py`'s docstring)
serializes it: exactly one `Activo` row survives, post-commit.
"""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from energia.contexts.motor.domain.isolation_forest import ALGORITMO_ISOLATION_FOREST
from energia.contexts.motor.infrastructure.modelos_ia_repository import SqlModelosIaRepository

pytestmark = pytest.mark.integration


async def _fila(session: AsyncSession, modelo_ia_id):
    result = await session.execute(
        text(
            "SELECT nombre, version, algoritmo, estado, fecha_entrenamiento FROM modelos_ia "
            "WHERE id = :id"
        ),
        {"id": modelo_ia_id},
    )
    row = result.first()
    assert row is not None
    return row


async def test_registrar_fit_inserts_a_new_activo_row(db_session: AsyncSession) -> None:
    repository = SqlModelosIaRepository(db_session)

    modelo_id = await repository.registrar_fit(
        nombre="isolation-forest-global",
        version="v1-LOTE-A-aaaaaaaa",
        algoritmo=ALGORITMO_ISOLATION_FOREST,
    )
    await db_session.commit()

    row = await _fila(db_session, modelo_id)
    assert row.nombre == "isolation-forest-global"
    assert row.version == "v1-LOTE-A-aaaaaaaa"
    assert row.algoritmo == ALGORITMO_ISOLATION_FOREST
    assert row.estado == "Activo"
    assert row.fecha_entrenamiento is not None


async def test_registrar_fit_flips_previous_activo_rows_of_the_same_nombre_to_obsoleto(
    db_session: AsyncSession,
) -> None:
    """A second lote's fit for the SAME `nombre` (scope) must flip the FIRST fit's row from
    `Activo` to `Obsoleto`, never delete it (RD-048: "toda versión debe conservarse")."""
    repository = SqlModelosIaRepository(db_session)

    primero_id = await repository.registrar_fit(
        nombre="isolation-forest-global",
        version="v1-LOTE-A-aaaaaaaa",
        algoritmo=ALGORITMO_ISOLATION_FOREST,
    )
    await db_session.commit()

    segundo_id = await repository.registrar_fit(
        nombre="isolation-forest-global",
        version="v1-LOTE-B-bbbbbbbb",
        algoritmo=ALGORITMO_ISOLATION_FOREST,
    )
    await db_session.commit()

    fila_primero = await _fila(db_session, primero_id)
    fila_segundo = await _fila(db_session, segundo_id)
    assert fila_primero.estado == "Obsoleto"
    assert fila_segundo.estado == "Activo"


async def test_registrar_fit_different_nombre_does_not_affect_each_other(
    db_session: AsyncSession,
) -> None:
    """Two DIFFERENT scopes (`nombre`s) never flip each other's `Activo` row -- the single-Activo
    invariant is scoped per `nombre`, not global across the whole table."""
    repository = SqlModelosIaRepository(db_session)

    global_id = await repository.registrar_fit(
        nombre="isolation-forest-global",
        version="v1-LOTE-A-aaaaaaaa",
        algoritmo=ALGORITMO_ISOLATION_FOREST,
    )
    await db_session.commit()
    residencial_id = await repository.registrar_fit(
        nombre="isolation-forest-Residencial",
        version="v1-LOTE-A-aaaaaaaa",
        algoritmo=ALGORITMO_ISOLATION_FOREST,
    )
    await db_session.commit()

    fila_global = await _fila(db_session, global_id)
    fila_residencial = await _fila(db_session, residencial_id)
    assert fila_global.estado == "Activo"
    assert fila_residencial.estado == "Activo"


async def test_registrar_fit_same_nombre_and_version_is_idempotent_retry(
    db_session: AsyncSession,
) -> None:
    """An Error-retry reprocess of the SAME lote (`domain/isolation_forest.py`'s
    `construir_version_modelo` is deterministic per `codigo_lote`) re-registers the SAME
    `(nombre, version)` pair -- must reuse the SAME row (`id`), not insert a duplicate that would
    violate `uq_modelos_ia_nombre_version`, and must NOT flip itself to `Obsoleto`."""
    repository = SqlModelosIaRepository(db_session)

    primero_id = await repository.registrar_fit(
        nombre="isolation-forest-global",
        version="v1-LOTE-A-aaaaaaaa",
        algoritmo=ALGORITMO_ISOLATION_FOREST,
    )
    await db_session.commit()

    segundo_id = await repository.registrar_fit(
        nombre="isolation-forest-global",
        version="v1-LOTE-A-aaaaaaaa",
        algoritmo=ALGORITMO_ISOLATION_FOREST,
    )
    await db_session.commit()

    assert segundo_id == primero_id
    fila = await _fila(db_session, primero_id)
    assert fila.estado == "Activo"

    result = await db_session.execute(
        text("SELECT count(*) FROM modelos_ia WHERE nombre = :n"),
        {"n": "isolation-forest-global"},
    )
    assert result.scalar_one() == 1


async def test_registrar_fit_concurrent_lotes_of_the_same_scope_leave_exactly_one_activo_row(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reproduces the reviewer-found race: two concurrent transactions processing DIFFERENT lotes
    (`db_session` alone cannot reproduce this -- it is a single session/transaction; this test
    needs two REAL, independent sessions racing against the SAME database, `test_session_factory`)
    both `registrar_fit` for the SAME `nombre` (scope). Without the transaction-scoped advisory
    lock (`infrastructure/modelos_ia_repository.py`'s docstring), each transaction's `INSERT`
    (different `version`, so no unique-constraint collision) is invisible to the OTHER under
    `READ COMMITTED` until it commits -- so BOTH `_FLIP_OBSOLETOS_SQL` runs would affect zero
    rows, and BOTH commits would leave TWO `Activo` rows for the SAME `nombre`. The lock serializes
    the two calls (whichever acquires it first fully commits or rolls back before the second
    proceeds), so exactly one `Activo` row survives, post-commit."""

    async def _fit(version: str) -> None:
        async with test_session_factory() as session:
            repository = SqlModelosIaRepository(session)
            await repository.registrar_fit(
                nombre="isolation-forest-race",
                version=version,
                algoritmo=ALGORITMO_ISOLATION_FOREST,
            )
            await session.commit()

    # `asyncio.gather` starts both coroutines concurrently; real network round-trips to Postgres
    # (asyncpg awaits) give each a chance to interleave with the other before either commits --
    # no artificial stagger needed to reproduce the overlapping-transactions window above.
    await asyncio.gather(_fit("v1-LOTE-RACE-A-11111111"), _fit("v1-LOTE-RACE-B-22222222"))

    async with test_session_factory() as verify_session:
        result = await verify_session.execute(
            text(
                "SELECT count(*) FROM modelos_ia "
                "WHERE nombre = :n AND estado = 'Activo' AND deleted_at IS NULL"
            ),
            {"n": "isolation-forest-race"},
        )
        assert result.scalar_one() == 1

        total = await verify_session.execute(
            text("SELECT count(*) FROM modelos_ia WHERE nombre = :n"),
            {"n": "isolation-forest-race"},
        )
        assert total.scalar_one() == 2  # both fits registered -- RD-048, neither was lost
