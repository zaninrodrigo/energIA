"""Integration tests for `SqlFeatureVectorRepository` against real `energia_test` rows -- the
upsert contract `feature_vectors`' `UNIQUE (suministro_id, lote_id, version)` requires (mission
directive #6: a reprocess must overwrite, never duplicate).
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.motor.domain.features import VERSION_FEATURES
from energia.contexts.motor.domain.ports import FeatureVectorParaGuardar
from energia.contexts.motor.infrastructure.feature_vector_repository import (
    SqlFeatureVectorRepository,
)
from tests.integration.contexts.motor.conftest import insert_lote, insert_suministro

pytestmark = pytest.mark.integration


async def _contar_feature_vectors(session: AsyncSession, *, suministro_id, lote_id) -> int:
    result = await session.execute(
        text(
            "SELECT count(*) FROM feature_vectors "
            "WHERE suministro_id = :suministro_id AND lote_id = :lote_id"
        ),
        {"suministro_id": suministro_id, "lote_id": lote_id},
    )
    return result.scalar_one()


async def test_guardar_batch_inserts_a_new_row(db_session: AsyncSession) -> None:
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-FVR-1")
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-FVR-1", cantidad_registros=0)
    repository = SqlFeatureVectorRepository(db_session)

    await repository.guardar_batch(
        [
            FeatureVectorParaGuardar(
                suministro_id=suministro_id,
                lote_id=lote_id,
                features={"avg_consumption": 100.0, "is_cold_start": True},
            )
        ]
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT version, features FROM feature_vectors WHERE suministro_id = :s"),
        {"s": suministro_id},
    )
    row = result.first()
    assert row is not None
    assert row.version == VERSION_FEATURES
    assert row.features == {"avg_consumption": 100.0, "is_cold_start": True}


async def test_guardar_batch_reprocess_upserts_without_duplicating(
    db_session: AsyncSession,
) -> None:
    """Mission directive #6: an `Error -> Procesando` retry that regenerates the SAME
    suministro/lote/version vector must overwrite the existing row, not insert a second one --
    `feature_vectors.uq_feature_vectors_suministro_lote_version` is exactly the constraint this
    upsert relies on."""
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-FVR-2")
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-FVR-2", cantidad_registros=0)
    repository = SqlFeatureVectorRepository(db_session)

    await repository.guardar_batch(
        [
            FeatureVectorParaGuardar(
                suministro_id=suministro_id, lote_id=lote_id, features={"avg_consumption": 100.0}
            )
        ]
    )
    await db_session.commit()

    await repository.guardar_batch(
        [
            FeatureVectorParaGuardar(
                suministro_id=suministro_id, lote_id=lote_id, features={"avg_consumption": 250.0}
            )
        ]
    )
    await db_session.commit()

    total = await _contar_feature_vectors(db_session, suministro_id=suministro_id, lote_id=lote_id)
    assert total == 1
    result = await db_session.execute(
        text("SELECT features FROM feature_vectors WHERE suministro_id = :s"), {"s": suministro_id}
    )
    row = result.first()
    assert row is not None
    assert row.features == {"avg_consumption": 250.0}


async def test_guardar_batch_writes_multiple_suministros_in_one_call(
    db_session: AsyncSession,
) -> None:
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-FVR-3", cantidad_registros=0)
    suministro_a = await insert_suministro(db_session, numero_suministro="SUM-FVR-3A")
    suministro_b = await insert_suministro(db_session, numero_suministro="SUM-FVR-3B")
    repository = SqlFeatureVectorRepository(db_session)

    await repository.guardar_batch(
        [
            FeatureVectorParaGuardar(
                suministro_id=suministro_a, lote_id=lote_id, features={"avg_consumption": 10.0}
            ),
            FeatureVectorParaGuardar(
                suministro_id=suministro_b, lote_id=lote_id, features={"avg_consumption": 20.0}
            ),
        ]
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT count(*) FROM feature_vectors WHERE lote_id = :lote_id"), {"lote_id": lote_id}
    )
    assert result.scalar_one() == 2


async def test_guardar_batch_empty_list_is_a_noop(db_session: AsyncSession) -> None:
    repository = SqlFeatureVectorRepository(db_session)
    await repository.guardar_batch([])
    await db_session.commit()

    result = await db_session.execute(text("SELECT count(*) FROM feature_vectors"))
    assert result.scalar_one() == 0
