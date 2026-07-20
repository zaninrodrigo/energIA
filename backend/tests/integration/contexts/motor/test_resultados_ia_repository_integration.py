"""Integration tests for `SqlResultadosIaRepository` (Etapas 7-8, AI_ENGINE_SPEC.md §10/§11/§14)
against real `energia_test` rows -- THE convergence write: `resultados_ia`, `feature_vectors.
resultado_ia_id` (backfill), `anomalias`, `ire`, `impacto_economico`, all in one call.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.motor.domain.features import VERSION_FEATURES
from energia.contexts.motor.domain.isolation_forest import ALGORITMO_ISOLATION_FOREST
from energia.contexts.motor.domain.ports import (
    AnomaliaParaGuardar,
    FeatureVectorParaGuardar,
    PrediccionParaGuardar,
    ResultadoIAParaGuardar,
)
from energia.contexts.motor.infrastructure.feature_vector_repository import (
    SqlFeatureVectorRepository,
)
from energia.contexts.motor.infrastructure.modelos_ia_repository import SqlModelosIaRepository
from energia.contexts.motor.infrastructure.predicciones_repository import (
    SqlPrediccionesRepository,
)
from energia.contexts.motor.infrastructure.resultados_ia_repository import (
    SqlResultadosIaRepository,
)
from tests.integration.contexts.motor.conftest import insert_lote, insert_suministro

pytestmark = pytest.mark.integration


async def _modelo(session: AsyncSession, *, version: str = "v1-RIA-aaaaaaaa") -> object:
    return await SqlModelosIaRepository(session).registrar_fit(
        nombre="isolation-forest-global", version=version, algoritmo=ALGORITMO_ISOLATION_FOREST
    )


async def _prediccion(
    session: AsyncSession, *, suministro_id, lote_id, modelo_id, score: float = 0.5
) -> object:
    prediccion_id = uuid4()
    await SqlPrediccionesRepository(session).reemplazar_lote(
        lote_id,
        [
            PrediccionParaGuardar(
                id=prediccion_id,
                modelo_ia_id=modelo_id,
                suministro_id=suministro_id,
                lote_id=lote_id,
                score=score,
                clasificacion="Atención",
            )
        ],
    )
    return prediccion_id


def _entrada(
    *,
    suministro_id,
    lote_id,
    modelo_id,
    prediccion_id,
    ire_valor: int = 50,
    iee_kwh: float | None = None,
    clasificacion: str = "Atención",
    observaciones: str = "[]",
    anomalias: tuple[AnomaliaParaGuardar, ...] = (),
) -> ResultadoIAParaGuardar:
    return ResultadoIAParaGuardar(
        suministro_id=suministro_id,
        lote_id=lote_id,
        modelo_ia_id=modelo_id,
        prediccion_id=prediccion_id,
        score_anomalia=-0.42,
        probabilidad=0.5,
        clasificacion=clasificacion,
        observaciones=observaciones,
        ire_valor=ire_valor,
        iee_kwh=iee_kwh,
        anomalias=anomalias,
    )


async def test_persistir_inserts_resultado_ia_row(db_session: AsyncSession) -> None:
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-RIA-1", cantidad_registros=0)
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-RIA-1")
    modelo_id = await _modelo(db_session)
    prediccion_id = await _prediccion(
        db_session, suministro_id=suministro_id, lote_id=lote_id, modelo_id=modelo_id
    )
    await db_session.commit()
    repository = SqlResultadosIaRepository(db_session)

    ids = await repository.persistir(
        lote_id,
        [
            _entrada(
                suministro_id=suministro_id,
                lote_id=lote_id,
                modelo_id=modelo_id,
                prediccion_id=prediccion_id,
                ire_valor=62,
                clasificacion="Alto Riesgo",
                observaciones='[{"factor": "score_ia", "contribution": 26.09, "reason": "x"}]',
            )
        ],
    )
    await db_session.commit()

    assert ids == {suministro_id: ids[suministro_id]}
    result = await db_session.execute(
        text(
            "SELECT suministro_id, lote_id, modelo_ia_id, prediccion_id, clasificacion, "
            "observaciones, probabilidad, score_anomalia "
            "FROM resultados_ia WHERE suministro_id = :s AND lote_id = :l"
        ),
        {"s": suministro_id, "l": lote_id},
    )
    row = result.one()
    assert row.suministro_id == suministro_id
    assert row.lote_id == lote_id
    assert row.modelo_ia_id == modelo_id
    assert row.prediccion_id == prediccion_id
    assert row.clasificacion == "Alto Riesgo"
    assert float(row.probabilidad) == pytest.approx(0.5)
    assert float(row.score_anomalia) == pytest.approx(-0.42)
    assert "score_ia" in row.observaciones


async def test_persistir_upserts_on_retry_preserving_the_same_id(db_session: AsyncSession) -> None:
    """RD-023 ("no puede existir más de un ResultadoIA por suministro y lote") + idempotent
    Error-retry reprocess (mission directive #6): a retry of the SAME (suministro, lote) reuses
    the SAME `resultados_ia.id`, never duplicating the row -- critical for `anomalias`/`ire`/
    `impacto_economico`'s FKs to keep resolving across a retry."""
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-RIA-2", cantidad_registros=0)
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-RIA-2")
    modelo_id = await _modelo(db_session, version="v1-RIA-2-aaaaaaaa")
    prediccion_id = await _prediccion(
        db_session, suministro_id=suministro_id, lote_id=lote_id, modelo_id=modelo_id
    )
    await db_session.commit()
    repository = SqlResultadosIaRepository(db_session)

    ids_1 = await repository.persistir(
        lote_id,
        [
            _entrada(
                suministro_id=suministro_id,
                lote_id=lote_id,
                modelo_id=modelo_id,
                prediccion_id=prediccion_id,
                ire_valor=30,
                clasificacion="Atención",
            )
        ],
    )
    await db_session.commit()

    ids_2 = await repository.persistir(
        lote_id,
        [
            _entrada(
                suministro_id=suministro_id,
                lote_id=lote_id,
                modelo_id=modelo_id,
                prediccion_id=prediccion_id,
                ire_valor=90,
                clasificacion="Crítico",
            )
        ],
    )
    await db_session.commit()

    assert ids_1[suministro_id] == ids_2[suministro_id]  # SAME row, not duplicated
    total = await db_session.execute(
        text("SELECT count(*) FROM resultados_ia WHERE suministro_id = :s AND lote_id = :l"),
        {"s": suministro_id, "l": lote_id},
    )
    assert total.scalar_one() == 1
    fila = await db_session.execute(
        text("SELECT clasificacion FROM resultados_ia WHERE suministro_id = :s"),
        {"s": suministro_id},
    )
    assert fila.scalar_one() == "Crítico"  # refreshed to the SECOND run's value


async def test_persistir_backfills_feature_vectors_resultado_ia_id(
    db_session: AsyncSession,
) -> None:
    """§14's write order: `feature_vectors` was written by Etapa 3-4 with `resultado_ia_id NULL`
    -- this is the ONE place it gets filled in, using the resolved `resultados_ia.id`."""
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-RIA-3", cantidad_registros=0)
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-RIA-3")
    modelo_id = await _modelo(db_session, version="v1-RIA-3-aaaaaaaa")
    prediccion_id = await _prediccion(
        db_session, suministro_id=suministro_id, lote_id=lote_id, modelo_id=modelo_id
    )
    await SqlFeatureVectorRepository(db_session).guardar_batch(
        [
            FeatureVectorParaGuardar(
                suministro_id=suministro_id, lote_id=lote_id, features={"avg_consumption": 10.0}
            )
        ]
    )
    await db_session.commit()

    verificacion_previa = await db_session.execute(
        text("SELECT resultado_ia_id FROM feature_vectors WHERE suministro_id = :s"),
        {"s": suministro_id},
    )
    assert verificacion_previa.scalar_one() is None

    repository = SqlResultadosIaRepository(db_session)
    ids = await repository.persistir(
        lote_id,
        [
            _entrada(
                suministro_id=suministro_id,
                lote_id=lote_id,
                modelo_id=modelo_id,
                prediccion_id=prediccion_id,
            )
        ],
    )
    await db_session.commit()

    verificacion = await db_session.execute(
        text(
            "SELECT resultado_ia_id FROM feature_vectors "
            "WHERE suministro_id = :s AND lote_id = :l AND version = :v"
        ),
        {"s": suministro_id, "l": lote_id, "v": VERSION_FEATURES},
    )
    assert verificacion.scalar_one() == ids[suministro_id]


async def test_persistir_replaces_anomalias_soft_delete_then_insert(
    db_session: AsyncSession,
) -> None:
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-RIA-4", cantidad_registros=0)
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-RIA-4")
    modelo_id = await _modelo(db_session, version="v1-RIA-4-aaaaaaaa")
    prediccion_id = await _prediccion(
        db_session, suministro_id=suministro_id, lote_id=lote_id, modelo_id=modelo_id
    )
    await db_session.commit()
    repository = SqlResultadosIaRepository(db_session)

    await repository.persistir(
        lote_id,
        [
            _entrada(
                suministro_id=suministro_id,
                lote_id=lote_id,
                modelo_id=modelo_id,
                prediccion_id=prediccion_id,
                anomalias=(
                    AnomaliaParaGuardar(
                        tipo="Persistencia Anómala", severidad="Alta", descripcion="racha de 3"
                    ),
                ),
            )
        ],
    )
    await db_session.commit()

    activas_1 = await db_session.execute(
        text(
            "SELECT tipo FROM anomalias a JOIN resultados_ia r ON a.resultado_ia_id = r.id "
            "WHERE r.lote_id = :l AND a.deleted_at IS NULL"
        ),
        {"l": lote_id},
    )
    assert [row[0] for row in activas_1] == ["Persistencia Anómala"]

    # Retry: DIFFERENT anomalias set this time (e.g. R1 no longer fires, R6 does instead).
    await repository.persistir(
        lote_id,
        [
            _entrada(
                suministro_id=suministro_id,
                lote_id=lote_id,
                modelo_id=modelo_id,
                prediccion_id=prediccion_id,
                anomalias=(
                    AnomaliaParaGuardar(
                        tipo="Desvío Estadístico", severidad="Media", descripcion="z=4.2"
                    ),
                ),
            )
        ],
    )
    await db_session.commit()

    activas_2 = await db_session.execute(
        text(
            "SELECT tipo FROM anomalias a JOIN resultados_ia r ON a.resultado_ia_id = r.id "
            "WHERE r.lote_id = :l AND a.deleted_at IS NULL"
        ),
        {"l": lote_id},
    )
    assert [row[0] for row in activas_2] == ["Desvío Estadístico"]

    eliminadas = await db_session.execute(
        text(
            "SELECT count(*) FROM anomalias a JOIN resultados_ia r ON a.resultado_ia_id = r.id "
            "WHERE r.lote_id = :l AND a.deleted_at IS NOT NULL"
        ),
        {"l": lote_id},
    )
    assert eliminadas.scalar_one() == 1  # the FIRST attempt's row, soft-deleted, never removed


async def test_persistir_upserts_ire(db_session: AsyncSession) -> None:
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-RIA-5", cantidad_registros=0)
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-RIA-5")
    modelo_id = await _modelo(db_session, version="v1-RIA-5-aaaaaaaa")
    prediccion_id = await _prediccion(
        db_session, suministro_id=suministro_id, lote_id=lote_id, modelo_id=modelo_id
    )
    await db_session.commit()
    repository = SqlResultadosIaRepository(db_session)

    await repository.persistir(
        lote_id,
        [
            _entrada(
                suministro_id=suministro_id,
                lote_id=lote_id,
                modelo_id=modelo_id,
                prediccion_id=prediccion_id,
                ire_valor=35,
            )
        ],
    )
    await db_session.commit()

    fila = await db_session.execute(
        text(
            "SELECT i.valor, i.nivel FROM ire i JOIN resultados_ia r ON i.resultado_ia_id = r.id "
            "WHERE r.suministro_id = :s"
        ),
        {"s": suministro_id},
    )
    valor, nivel = fila.one()
    assert int(valor) == 35
    assert nivel == "Bajo"  # 21-40 -> Bajo, ire.nivel's GENERATED column (§10.2)

    # Retry with a different valor -- must UPDATE the SAME row (uq_ire_resultado_ia), not insert
    # a second one.
    await repository.persistir(
        lote_id,
        [
            _entrada(
                suministro_id=suministro_id,
                lote_id=lote_id,
                modelo_id=modelo_id,
                prediccion_id=prediccion_id,
                ire_valor=85,
            )
        ],
    )
    await db_session.commit()

    total = await db_session.execute(
        text(
            "SELECT count(*) FROM ire i JOIN resultados_ia r ON i.resultado_ia_id = r.id "
            "WHERE r.suministro_id = :s"
        ),
        {"s": suministro_id},
    )
    assert total.scalar_one() == 1
    fila_2 = await db_session.execute(
        text(
            "SELECT i.valor, i.nivel FROM ire i JOIN resultados_ia r ON i.resultado_ia_id = r.id "
            "WHERE r.suministro_id = :s"
        ),
        {"s": suministro_id},
    )
    valor_2, nivel_2 = fila_2.one()
    assert int(valor_2) == 85
    assert nivel_2 == "Crítico"  # 81-100 -> Crítico


async def test_persistir_writes_impacto_economico_in_kwh_only_when_iee_present(
    db_session: AsyncSession,
) -> None:
    """§11.2's v1 persistence convention: `monto_estimado` carries the kWh value, `moneda =
    'kWh'` -- and NO row at all for a cold-start suministro (`iee_kwh is None`)."""
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-RIA-6", cantidad_registros=0)
    suministro_con_iee = await insert_suministro(db_session, numero_suministro="SUM-RIA-6A")
    suministro_sin_iee = await insert_suministro(db_session, numero_suministro="SUM-RIA-6B")
    modelo_id = await _modelo(db_session, version="v1-RIA-6-aaaaaaaa")
    prediccion_con_iee = await _prediccion(
        db_session, suministro_id=suministro_con_iee, lote_id=lote_id, modelo_id=modelo_id
    )
    prediccion_sin_iee = await _prediccion(
        db_session, suministro_id=suministro_sin_iee, lote_id=lote_id, modelo_id=modelo_id
    )
    await db_session.commit()
    repository = SqlResultadosIaRepository(db_session)

    await repository.persistir(
        lote_id,
        [
            _entrada(
                suministro_id=suministro_con_iee,
                lote_id=lote_id,
                modelo_id=modelo_id,
                prediccion_id=prediccion_con_iee,
                iee_kwh=42.5,
            ),
            _entrada(
                suministro_id=suministro_sin_iee,
                lote_id=lote_id,
                modelo_id=modelo_id,
                prediccion_id=prediccion_sin_iee,
                iee_kwh=None,
            ),
        ],
    )
    await db_session.commit()

    fila = await db_session.execute(
        text(
            "SELECT monto_estimado, moneda FROM impacto_economico ie "
            "JOIN resultados_ia r ON ie.resultado_ia_id = r.id "
            "WHERE r.suministro_id = :s"
        ),
        {"s": suministro_con_iee},
    )
    monto, moneda = fila.one()
    assert float(monto) == pytest.approx(42.5)
    assert moneda == "kWh"

    conteo_sin_iee = await db_session.execute(
        text(
            "SELECT count(*) FROM impacto_economico ie "
            "JOIN resultados_ia r ON ie.resultado_ia_id = r.id "
            "WHERE r.suministro_id = :s"
        ),
        {"s": suministro_sin_iee},
    )
    assert conteo_sin_iee.scalar_one() == 0


async def test_persistir_clears_impacto_economico_when_retry_loses_its_iee(
    db_session: AsyncSession,
) -> None:
    """The soft-delete-first step handles a retry whose suministro NO LONGER has an IEE (a
    plain `ON CONFLICT` upsert could never express "this row should stop existing")."""
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-RIA-7", cantidad_registros=0)
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-RIA-7")
    modelo_id = await _modelo(db_session, version="v1-RIA-7-aaaaaaaa")
    prediccion_id = await _prediccion(
        db_session, suministro_id=suministro_id, lote_id=lote_id, modelo_id=modelo_id
    )
    await db_session.commit()
    repository = SqlResultadosIaRepository(db_session)

    await repository.persistir(
        lote_id,
        [
            _entrada(
                suministro_id=suministro_id,
                lote_id=lote_id,
                modelo_id=modelo_id,
                prediccion_id=prediccion_id,
                iee_kwh=100.0,
            )
        ],
    )
    await db_session.commit()
    activo = await db_session.execute(
        text(
            "SELECT count(*) FROM impacto_economico ie "
            "JOIN resultados_ia r ON ie.resultado_ia_id = r.id "
            "WHERE r.suministro_id = :s AND ie.deleted_at IS NULL"
        ),
        {"s": suministro_id},
    )
    assert activo.scalar_one() == 1

    await repository.persistir(
        lote_id,
        [
            _entrada(
                suministro_id=suministro_id,
                lote_id=lote_id,
                modelo_id=modelo_id,
                prediccion_id=prediccion_id,
                iee_kwh=None,
            )
        ],
    )
    await db_session.commit()

    activo_tras_retry = await db_session.execute(
        text(
            "SELECT count(*) FROM impacto_economico ie "
            "JOIN resultados_ia r ON ie.resultado_ia_id = r.id "
            "WHERE r.suministro_id = :s AND ie.deleted_at IS NULL"
        ),
        {"s": suministro_id},
    )
    assert activo_tras_retry.scalar_one() == 0
    soft_eliminado = await db_session.execute(
        text(
            "SELECT count(*) FROM impacto_economico ie "
            "JOIN resultados_ia r ON ie.resultado_ia_id = r.id "
            "WHERE r.suministro_id = :s AND ie.deleted_at IS NOT NULL"
        ),
        {"s": suministro_id},
    )
    assert soft_eliminado.scalar_one() == 1


async def test_persistir_empty_entradas_still_clears_stale_anomalias_and_impacto(
    db_session: AsyncSession,
) -> None:
    """Mirrors `SqlPrediccionesRepository`'s own "empty batch still clears stale rows" contract
    (module docstring) -- an Error-retry that ends up analyzing NOTHING for this lote still
    soft-deletes any `anomalias`/`impacto_economico` rows a PREVIOUS attempt left behind."""
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-RIA-8", cantidad_registros=0)
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-RIA-8")
    modelo_id = await _modelo(db_session, version="v1-RIA-8-aaaaaaaa")
    prediccion_id = await _prediccion(
        db_session, suministro_id=suministro_id, lote_id=lote_id, modelo_id=modelo_id
    )
    await db_session.commit()
    repository = SqlResultadosIaRepository(db_session)

    await repository.persistir(
        lote_id,
        [
            _entrada(
                suministro_id=suministro_id,
                lote_id=lote_id,
                modelo_id=modelo_id,
                prediccion_id=prediccion_id,
                iee_kwh=10.0,
                anomalias=(
                    AnomaliaParaGuardar(tipo="Consumo Muy Alto", severidad="Media", descripcion=""),
                ),
            )
        ],
    )
    await db_session.commit()

    ids = await repository.persistir(lote_id, [])
    await db_session.commit()

    assert ids == {}
    activas_anomalias = await db_session.execute(
        text(
            "SELECT count(*) FROM anomalias a JOIN resultados_ia r ON a.resultado_ia_id = r.id "
            "WHERE r.lote_id = :l AND a.deleted_at IS NULL"
        ),
        {"l": lote_id},
    )
    assert activas_anomalias.scalar_one() == 0
    activo_impacto = await db_session.execute(
        text(
            "SELECT count(*) FROM impacto_economico ie "
            "JOIN resultados_ia r ON ie.resultado_ia_id = r.id "
            "WHERE r.lote_id = :l AND ie.deleted_at IS NULL"
        ),
        {"l": lote_id},
    )
    assert activo_impacto.scalar_one() == 0
    # The `resultados_ia` row ITSELF is untouched (module docstring's "Known v1 limitation") --
    # only its DEPENDENT rows (anomalias/impacto_economico) are cleared.
    resultado_sigue_existiendo = await db_session.execute(
        text("SELECT count(*) FROM resultados_ia WHERE suministro_id = :s"), {"s": suministro_id}
    )
    assert resultado_sigue_existiendo.scalar_one() == 1
