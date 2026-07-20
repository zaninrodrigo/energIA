"""Integration tests for `SqlPrediccionesRepository` (Etapa 6, AI_ENGINE_SPEC.md §9) -- the
soft-delete-then-insert idempotent-reprocess contract `predicciones` needs since it carries no
natural unique key beyond its own surrogate `id` (`docker/postgres/init/01_schema.sql`).

**FIX (reviewer finding, CRITICAL, 2026-07-15) — soft delete, not physical `DELETE`.** A retry
must leave exactly one ACTIVE (`deleted_at IS NULL`) `predicciones` row per scored suministro, but
the PREVIOUS attempt's rows are soft-deleted (`deleted_at` set), never physically removed
(DATABASE_DESIGN.md §10: "ningún DELETE físico") — this is what keeps a retry FK-safe once Etapa 7
(not yet implemented) writes `resultados_ia.prediccion_id`, see
`test_reemplazar_lote_retry_with_a_referencing_resultados_ia_row_does_not_violate_fk` below.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.motor.domain.isolation_forest import ALGORITMO_ISOLATION_FOREST
from energia.contexts.motor.domain.ports import PrediccionParaGuardar
from energia.contexts.motor.infrastructure.modelos_ia_repository import SqlModelosIaRepository
from energia.contexts.motor.infrastructure.predicciones_repository import (
    SqlPrediccionesRepository,
)
from tests.integration.contexts.motor.conftest import insert_lote, insert_suministro

pytestmark = pytest.mark.integration


async def _contar_predicciones(session: AsyncSession, *, lote_id) -> int:
    """Count of ACTIVE (`deleted_at IS NULL`) `predicciones` rows for `lote_id`."""
    result = await session.execute(
        text("SELECT count(*) FROM predicciones WHERE lote_id = :lote_id AND deleted_at IS NULL"),
        {"lote_id": lote_id},
    )
    return result.scalar_one()


async def _contar_predicciones_eliminadas(session: AsyncSession, *, lote_id) -> int:
    """Count of SOFT-DELETED (`deleted_at IS NOT NULL`) `predicciones` rows for `lote_id` -- a
    previous attempt's rows accumulate here across retries, never physically removed."""
    result = await session.execute(
        text(
            "SELECT count(*) FROM predicciones WHERE lote_id = :lote_id AND deleted_at IS NOT NULL"
        ),
        {"lote_id": lote_id},
    )
    return result.scalar_one()


async def _modelo(session: AsyncSession) -> object:
    return await SqlModelosIaRepository(session).registrar_fit(
        nombre="isolation-forest-global",
        version="v1-LOTE-PR-aaaaaaaa",
        algoritmo=ALGORITMO_ISOLATION_FOREST,
    )


async def test_reemplazar_lote_inserts_rows(db_session: AsyncSession) -> None:
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-PR-1", cantidad_registros=0)
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-PR-1")
    modelo_id = await _modelo(db_session)
    await db_session.commit()
    repository = SqlPrediccionesRepository(db_session)

    await repository.reemplazar_lote(
        lote_id,
        [
            PrediccionParaGuardar(
                id=uuid4(),
                modelo_ia_id=modelo_id,
                suministro_id=suministro_id,
                lote_id=lote_id,
                score=0.75,
                clasificacion="Alto Riesgo",
            )
        ],
    )
    await db_session.commit()

    total = await _contar_predicciones(db_session, lote_id=lote_id)
    assert total == 1
    result = await db_session.execute(
        text("SELECT score, clasificacion, modelo_ia_id FROM predicciones WHERE lote_id = :l"),
        {"l": lote_id},
    )
    row = result.first()
    assert row is not None
    assert float(row.score) == pytest.approx(0.75)
    assert row.clasificacion == "Alto Riesgo"
    assert row.modelo_ia_id == modelo_id


async def test_reemplazar_lote_retry_soft_deletes_previous_rows_first(
    db_session: AsyncSession,
) -> None:
    """An Error-retry reprocess of the SAME lote must leave EXACTLY the new set of ACTIVE rows
    behind, never accumulate on top of the previous attempt's rows (mission directive #6) -- but
    the previous attempt's rows are SOFT-deleted (`deleted_at` set), never physically removed
    (DATABASE_DESIGN.md §10: "ningún DELETE físico")."""
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-PR-2", cantidad_registros=0)
    suministro_a = await insert_suministro(db_session, numero_suministro="SUM-PR-2A")
    suministro_b = await insert_suministro(db_session, numero_suministro="SUM-PR-2B")
    modelo_id = await _modelo(db_session)
    await db_session.commit()
    repository = SqlPrediccionesRepository(db_session)

    await repository.reemplazar_lote(
        lote_id,
        [
            PrediccionParaGuardar(
                id=uuid4(),
                modelo_ia_id=modelo_id,
                suministro_id=suministro_a,
                lote_id=lote_id,
                score=0.10,
                clasificacion="Normal",
            ),
            PrediccionParaGuardar(
                id=uuid4(),
                modelo_ia_id=modelo_id,
                suministro_id=suministro_b,
                lote_id=lote_id,
                score=0.20,
                clasificacion="Normal",
            ),
        ],
    )
    await db_session.commit()
    assert await _contar_predicciones(db_session, lote_id=lote_id) == 2
    assert await _contar_predicciones_eliminadas(db_session, lote_id=lote_id) == 0

    # Retry: only suministro_a scored this time (e.g. suministro_b got excluded on reimport).
    await repository.reemplazar_lote(
        lote_id,
        [
            PrediccionParaGuardar(
                id=uuid4(),
                modelo_ia_id=modelo_id,
                suministro_id=suministro_a,
                lote_id=lote_id,
                score=0.95,
                clasificacion="Crítico",
            )
        ],
    )
    await db_session.commit()

    # Exactly 1 ACTIVE row survives the retry...
    assert await _contar_predicciones(db_session, lote_id=lote_id) == 1
    # ...but BOTH previous-attempt rows are still there, soft-deleted, never physically removed.
    assert await _contar_predicciones_eliminadas(db_session, lote_id=lote_id) == 2
    result = await db_session.execute(
        text(
            "SELECT suministro_id, score, clasificacion FROM predicciones "
            "WHERE lote_id = :l AND deleted_at IS NULL"
        ),
        {"l": lote_id},
    )
    row = result.first()
    assert row is not None
    assert row.suministro_id == suministro_a
    assert float(row.score) == pytest.approx(0.95)
    assert row.clasificacion == "Crítico"


async def test_reemplazar_lote_does_not_touch_other_lotes(db_session: AsyncSession) -> None:
    lote_a = await insert_lote(db_session, codigo_lote="LOTE-PR-3A", cantidad_registros=0)
    lote_b = await insert_lote(db_session, codigo_lote="LOTE-PR-3B", cantidad_registros=0)
    suministro_a = await insert_suministro(db_session, numero_suministro="SUM-PR-3A")
    suministro_b = await insert_suministro(db_session, numero_suministro="SUM-PR-3B")
    modelo_id = await _modelo(db_session)
    await db_session.commit()
    repository = SqlPrediccionesRepository(db_session)

    await repository.reemplazar_lote(
        lote_a,
        [
            PrediccionParaGuardar(
                id=uuid4(),
                modelo_ia_id=modelo_id,
                suministro_id=suministro_a,
                lote_id=lote_a,
                score=0.5,
                clasificacion="Atención",
            )
        ],
    )
    await repository.reemplazar_lote(
        lote_b,
        [
            PrediccionParaGuardar(
                id=uuid4(),
                modelo_ia_id=modelo_id,
                suministro_id=suministro_b,
                lote_id=lote_b,
                score=0.6,
                clasificacion="Atención",
            )
        ],
    )
    await db_session.commit()

    # Re-running lote_a's replace must not disturb lote_b's row.
    await repository.reemplazar_lote(
        lote_a,
        [
            PrediccionParaGuardar(
                id=uuid4(),
                modelo_ia_id=modelo_id,
                suministro_id=suministro_a,
                lote_id=lote_a,
                score=0.55,
                clasificacion="Atención",
            )
        ],
    )
    await db_session.commit()

    assert await _contar_predicciones(db_session, lote_id=lote_a) == 1
    assert await _contar_predicciones(db_session, lote_id=lote_b) == 1


async def test_reemplazar_lote_empty_filas_still_clears_previous_rows(
    db_session: AsyncSession,
) -> None:
    """A retry where ZERO suministros ended up scored (e.g. every one excluded on reimport) must
    still clear the stale predicciones from the previous attempt -- an empty `filas` is not a
    no-op here, unlike `FeatureVectorRepository.guardar_batch`'s own empty-batch no-op (that
    port never needs to CLEAR anything on an empty batch, since it only ever upserts). "Clear"
    means soft-deleted, not physically removed (DATABASE_DESIGN.md §10)."""
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-PR-4", cantidad_registros=0)
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-PR-4")
    modelo_id = await _modelo(db_session)
    await db_session.commit()
    repository = SqlPrediccionesRepository(db_session)

    await repository.reemplazar_lote(
        lote_id,
        [
            PrediccionParaGuardar(
                id=uuid4(),
                modelo_ia_id=modelo_id,
                suministro_id=suministro_id,
                lote_id=lote_id,
                score=0.3,
                clasificacion="Atención",
            )
        ],
    )
    await db_session.commit()
    assert await _contar_predicciones(db_session, lote_id=lote_id) == 1

    await repository.reemplazar_lote(lote_id, [])
    await db_session.commit()

    assert await _contar_predicciones(db_session, lote_id=lote_id) == 0
    assert await _contar_predicciones_eliminadas(db_session, lote_id=lote_id) == 1


async def test_reemplazar_lote_retry_with_a_referencing_resultados_ia_row_does_not_violate_fk(
    db_session: AsyncSession,
) -> None:
    """Etapa 7 (not yet implemented) will write `resultados_ia.prediccion_id` -- a
    `FOREIGN KEY ... REFERENCES predicciones (id)` with the default `NO ACTION` delete rule
    (`docker/postgres/init/01_schema.sql`). A retry of a lote whose OLD `predicciones` row is
    already referenced by a `resultados_ia` row must NOT raise a foreign key violation, which a
    physical `DELETE` would: the soft-delete-then-insert contract leaves the referenced row in
    place (only flagged `deleted_at`), so the retry succeeds, the old row ends up soft-deleted,
    the `resultados_ia` row's FK still resolves, and a fresh `predicciones` row is inserted."""
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-PR-5", cantidad_registros=0)
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-PR-5")
    modelo_id = await _modelo(db_session)
    await db_session.commit()
    repository = SqlPrediccionesRepository(db_session)

    await repository.reemplazar_lote(
        lote_id,
        [
            PrediccionParaGuardar(
                id=uuid4(),
                modelo_ia_id=modelo_id,
                suministro_id=suministro_id,
                lote_id=lote_id,
                score=0.4,
                clasificacion="Atención",
            )
        ],
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT id FROM predicciones WHERE lote_id = :l AND deleted_at IS NULL"),
        {"l": lote_id},
    )
    prediccion_original_id = result.scalar_one()

    # Etapa 7 (future, not implemented yet): a full, valid `resultados_ia` row referencing the
    # `predicciones` row just inserted -- built with a registered `modelo_ia_id`, exactly as a
    # real Etapa 7 write would.
    resultado_ia_id = uuid4()
    await db_session.execute(
        text(
            "INSERT INTO resultados_ia "
            "(id, suministro_id, lote_id, modelo_ia_id, prediccion_id, clasificacion) "
            "VALUES (:id, :suministro_id, :lote_id, :modelo_ia_id, :prediccion_id, "
            ":clasificacion)"
        ),
        {
            "id": resultado_ia_id,
            "suministro_id": suministro_id,
            "lote_id": lote_id,
            "modelo_ia_id": modelo_id,
            "prediccion_id": prediccion_original_id,
            "clasificacion": "Atención",
        },
    )
    await db_session.commit()

    # Retry: must NOT raise a FK violation despite `resultados_ia` referencing the OLD row.
    await repository.reemplazar_lote(
        lote_id,
        [
            PrediccionParaGuardar(
                id=uuid4(),
                modelo_ia_id=modelo_id,
                suministro_id=suministro_id,
                lote_id=lote_id,
                score=0.9,
                clasificacion="Crítico",
            )
        ],
    )
    await db_session.commit()

    assert await _contar_predicciones(db_session, lote_id=lote_id) == 1  # new ACTIVE row
    assert await _contar_predicciones_eliminadas(db_session, lote_id=lote_id) == 1  # old, soft

    fila_original = await db_session.execute(
        text("SELECT deleted_at FROM predicciones WHERE id = :id"), {"id": prediccion_original_id}
    )
    assert fila_original.scalar_one() is not None  # soft-deleted, never physically removed

    # The `resultados_ia` row's FK still resolves -- the referenced row was never removed.
    fila_resultado = await db_session.execute(
        text("SELECT prediccion_id FROM resultados_ia WHERE id = :id"), {"id": resultado_ia_id}
    )
    assert fila_resultado.scalar_one() == prediccion_original_id
