"""Integration tests for `SqlFeaturesDataSource` against real `energia_test` rows -- Etapa 3's
set-based queries (US-008, AI_ENGINE_SPEC.md §6), the same discipline
`test_duplicidades_data_source_integration.py` applies to Etapa 2's queries.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.motor.infrastructure.features_data_source import SqlFeaturesDataSource
from tests.integration.contexts.motor.conftest import (
    insert_consumo,
    insert_lote,
    insert_suministro,
)

pytestmark = pytest.mark.integration


async def test_fetch_historial_kwh_returns_full_cross_lote_history(
    db_session: AsyncSession,
) -> None:
    """A suministro with periods across TWO lotes -- `fetch_historial_kwh` returns both, scoped
    by "at least one active consumo in the lote being processed", exactly like
    `SqlDuplicidadesDataSource.fetch_historial_periodos`."""
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-FEAT-1")
    lote_previo_id = await insert_lote(
        db_session, codigo_lote="LOTE-FEAT-1-PREVIO", cantidad_registros=1
    )
    consumo_previo_id = await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_previo_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("100.000"),
    )
    lote_actual_id = await insert_lote(db_session, codigo_lote="LOTE-FEAT-1", cantidad_registros=1)
    consumo_actual_id = await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_actual_id,
        fecha_inicio=date(2024, 2, 1),
        fecha_fin=date(2024, 2, 29),
        dias_facturados=29,
        kwh=Decimal("150.500"),
    )

    source = SqlFeaturesDataSource(db_session)
    filas = await source.fetch_historial_kwh(lote_actual_id)

    assert len(filas) == 2
    por_consumo = {fila.consumo_id: fila for fila in filas}
    assert por_consumo[consumo_previo_id].kwh == Decimal("100.000")
    assert por_consumo[consumo_previo_id].lote_id == lote_previo_id
    assert por_consumo[consumo_actual_id].kwh == Decimal("150.500")
    assert por_consumo[consumo_actual_id].dias_facturados == 29


async def test_fetch_historial_kwh_excludes_soft_deleted_consumos(db_session: AsyncSession) -> None:
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-FEAT-2")
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-FEAT-2", cantidad_registros=1)
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("100.000"),
    )
    otro_suministro_id = await insert_suministro(db_session, numero_suministro="SUM-FEAT-2B")
    consumo_borrado_id = await insert_consumo(
        db_session,
        suministro_id=otro_suministro_id,
        lote_id=lote_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("999.000"),
    )
    await db_session.execute(
        text("UPDATE consumos SET deleted_at = now() WHERE id = :id"),
        {"id": consumo_borrado_id},
    )
    await db_session.commit()

    source = SqlFeaturesDataSource(db_session)
    filas = await source.fetch_historial_kwh(lote_id)

    # The soft-deleted consumo drops its suministro out of "suministros_del_lote" entirely (no
    # ACTIVE consumo left in this lote for it), so none of its history (active or not) surfaces.
    assert all(fila.suministro_id != otro_suministro_id for fila in filas)


async def test_fetch_metadata_suministros_returns_categoria_localidad_fecha_alta(
    db_session: AsyncSession,
) -> None:
    suministro_id = await insert_suministro(
        db_session,
        numero_suministro="SUM-FEAT-3",
        localidad="Clorinda",
        fecha_alta=date(2019, 6, 15),
    )
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-FEAT-3", cantidad_registros=1)
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("100.000"),
    )

    source = SqlFeaturesDataSource(db_session)
    filas = await source.fetch_metadata_suministros(lote_id)

    assert len(filas) == 1
    assert filas[0].suministro_id == suministro_id
    assert filas[0].localidad == "Clorinda"
    assert filas[0].fecha_alta == date(2019, 6, 15)
    assert filas[0].categoria_tarifaria_id is not None
    assert filas[0].numero_suministro == "SUM-FEAT-3"
    assert filas[0].estado == "Activo"
    # Etapa 6 (AI_ENGINE_SPEC.md §9, DEC-010) needs the human-readable categoria name, not just
    # the UUID, to build `modelos_ia.nombre` -- `insert_suministro`'s default categoria is the
    # seeded "Residencial" (tests/integration/contexts/motor/conftest.py).
    assert filas[0].categoria_tarifaria_nombre == "Residencial"


async def test_fetch_metadata_suministros_returns_a_non_active_estado(
    db_session: AsyncSession,
) -> None:
    """Etapa 5's R1 (AI_ENGINE_SPEC.md §8) needs the REAL `suministros.estado` value, not always
    the seeded default -- proven here with a non-`Activo` value round-tripping unchanged."""
    suministro_id = await insert_suministro(
        db_session, numero_suministro="SUM-FEAT-5", estado="Baja"
    )
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-FEAT-5", cantidad_registros=1)
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("100.000"),
    )

    source = SqlFeaturesDataSource(db_session)
    filas = await source.fetch_metadata_suministros(lote_id)

    assert len(filas) == 1
    assert filas[0].estado == "Baja"


async def test_fetch_prior_anomaly_counts_is_zero_today_correct_by_construction(
    db_session: AsyncSession,
) -> None:
    """F15 `prior_anomaly_count`: no `anomalias`/`resultados_ia` rows exist yet anywhere in this
    codebase (Etapas 6-7 are not implemented) -- the query returns no rows at all, never a
    fabricated `0`-count row (`ProcesarLote._generar_features` defaults missing entries to `0`)."""
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-FEAT-4")
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-FEAT-4", cantidad_registros=1)
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("100.000"),
    )

    source = SqlFeaturesDataSource(db_session)
    filas = await source.fetch_prior_anomaly_counts(lote_id)

    assert filas == []
