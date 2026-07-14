"""Integration tests for `SqlDuplicidadesDataSource` against real `energia_test` rows -- Etapa
2's set-based queries (US-007, AI_ENGINE_SPEC.md §5) are exercised directly here, the same
discipline `test_validacion_data_source_integration.py` applies to Etapa 1's `fetch_chain`.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.motor.domain.duplicidades import detectar_periodos_conflictivos
from energia.contexts.motor.infrastructure.duplicidades_data_source import (
    SqlDuplicidadesDataSource,
)
from tests.integration.contexts.motor.conftest import (
    insert_consumo,
    insert_lectura,
    insert_lote,
    insert_suministro,
)

pytestmark = pytest.mark.integration


async def test_fetch_historial_periodos_links_overlap_across_two_lotes(
    db_session: AsyncSession,
) -> None:
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-DUP-1")
    lote_previo_id = await insert_lote(
        db_session, codigo_lote="LOTE-DUP-1-PREVIO", cantidad_registros=1
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
    lote_actual_id = await insert_lote(db_session, codigo_lote="LOTE-DUP-1", cantidad_registros=1)
    consumo_actual_id = await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_actual_id,
        # 2024-01-15 <= 2024-01-31 (previous period's end) -> overlap.
        fecha_inicio=date(2024, 1, 15),
        fecha_fin=date(2024, 2, 15),
        dias_facturados=31,
        kwh=Decimal("100.000"),
    )

    source = SqlDuplicidadesDataSource(db_session)
    filas = await source.fetch_historial_periodos(lote_actual_id)

    assert len(filas) == 2
    por_consumo = {fila.consumo_id: fila for fila in filas}

    fila_previa = por_consumo[consumo_previo_id]
    assert fila_previa.lote_id == lote_previo_id
    assert fila_previa.fecha_inicio == date(2024, 1, 1)
    assert fila_previa.fecha_fin == date(2024, 1, 31)

    fila_actual = por_consumo[consumo_actual_id]
    assert fila_actual.lote_id == lote_actual_id
    assert fila_actual.fecha_inicio == date(2024, 1, 15)
    assert fila_actual.fecha_fin == date(2024, 2, 15)


async def test_fetch_historial_periodos_only_covers_suministros_of_the_lote(
    db_session: AsyncSession,
) -> None:
    suministro_a = await insert_suministro(db_session, numero_suministro="SUM-DUP-2A")
    suministro_b = await insert_suministro(db_session, numero_suministro="SUM-DUP-2B")
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-DUP-2", cantidad_registros=1)
    otro_lote_id = await insert_lote(db_session, codigo_lote="LOTE-DUP-2-OTRO")
    await insert_consumo(
        db_session,
        suministro_id=suministro_a,
        lote_id=lote_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("10.000"),
    )
    await insert_consumo(
        db_session,
        suministro_id=suministro_b,
        lote_id=otro_lote_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("20.000"),
    )

    source = SqlDuplicidadesDataSource(db_session)
    filas = await source.fetch_historial_periodos(lote_id)

    assert len(filas) == 1
    assert filas[0].suministro_id == suministro_a


async def test_fetch_historial_lecturas_returns_plain_rows_ordered_by_fecha(
    db_session: AsyncSession,
) -> None:
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-DUP-3")
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-DUP-3", cantidad_registros=1)
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("100.000"),
    )
    lectura_1_id = await insert_lectura(
        db_session,
        suministro_id=suministro_id,
        fecha_lectura=date(2024, 1, 31),
        lectura_anterior=Decimal("0.000"),
        lectura_actual=Decimal("100.000"),
        dias_facturados=31,
    )
    lectura_2_id = await insert_lectura(
        db_session,
        suministro_id=suministro_id,
        fecha_lectura=date(2024, 2, 2),
        lectura_anterior=Decimal("0.000"),
        lectura_actual=Decimal("100.000"),
        dias_facturados=2,
    )

    source = SqlDuplicidadesDataSource(db_session)
    filas = await source.fetch_historial_lecturas(lote_id)

    assert len(filas) == 2
    por_lectura = {fila.lectura_id: fila for fila in filas}
    assert por_lectura[lectura_1_id].fecha_lectura == date(2024, 1, 31)
    assert por_lectura[lectura_1_id].lectura_actual == Decimal("100.000")
    segunda = por_lectura[lectura_2_id]
    assert segunda.fecha_lectura == date(2024, 2, 2)
    assert segunda.lectura_actual == Decimal("100.000")


async def test_fetch_conteos_lotes_relacionados_excludes_own_lote_and_reports_drift(
    db_session: AsyncSession,
) -> None:
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-DUP-4")
    # `lote_previo` originally declared 2 records (two periods of this suministro); the February
    # one was later re-imported under `lote_actual` -- the natural-key upsert migrates that SAME
    # row's `lote_id` in place (module docstring), leaving `lote_previo` with only 1 active
    # consumo against its still-declared `cantidad_registros=2`: the drift residue this query
    # must surface. `lote_previo` stays DISCOVERABLE here because it still shares an active
    # suministro link with `lote_actual` (the January period) -- see
    # `SqlDuplicidadesDataSource.fetch_conteos_lotes_relacionados`'s docstring for why a lote
    # that loses its ENTIRE shared link is not detectable this way (best-effort, not exhaustive).
    lote_previo_id = await insert_lote(
        db_session, codigo_lote="LOTE-DUP-4-PREVIO", cantidad_registros=2
    )
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_previo_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("100.000"),
    )
    lote_actual_id = await insert_lote(db_session, codigo_lote="LOTE-DUP-4", cantidad_registros=1)
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_actual_id,
        fecha_inicio=date(2024, 2, 1),
        fecha_fin=date(2024, 2, 29),
        dias_facturados=29,
        kwh=Decimal("90.000"),
    )

    source = SqlDuplicidadesDataSource(db_session)
    filas = await source.fetch_conteos_lotes_relacionados(lote_actual_id)

    assert len(filas) == 1
    fila = filas[0]
    assert fila.lote_id == lote_previo_id
    assert fila.codigo_lote == "LOTE-DUP-4-PREVIO"
    assert fila.cantidad_registros == 2
    assert fila.consumos_activos == 1


async def test_fetch_conteos_lotes_relacionados_empty_when_no_shared_history(
    db_session: AsyncSession,
) -> None:
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-DUP-5")
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-DUP-5", cantidad_registros=1)
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("10.000"),
    )

    source = SqlDuplicidadesDataSource(db_session)
    filas = await source.fetch_conteos_lotes_relacionados(lote_id)

    assert filas == []


async def test_fetch_historial_periodos_long_period_overlaps_two_disjoint_shorter_periods(
    db_session: AsyncSession,
) -> None:
    """FIX 1 reviewer reproduction, exercised through the REAL raw-SQL data source AND the real
    domain sweep together: a long consumo period (2024-01-01 -> 2024-12-31) overlaps TWO disjoint
    shorter periods (P_a in March, P_b in June) of the SAME suministro. The plain rows
    `fetch_historial_periodos` returns (no `LAG`/`prev_*`) must let `detectar_periodos_conflictivos`
    find BOTH overlaps -- (P_long, P_a) AND (P_long, P_b) -- not just the one adjacent to P_long
    under `fecha_inicio` ordering, and NOT (P_a, P_b), which do not overlap each other."""
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-DUP-6")
    lote_largo_id = await insert_lote(db_session, codigo_lote="LOTE-DUP-6-LARGO")
    lote_a_id = await insert_lote(db_session, codigo_lote="LOTE-DUP-6-A")
    lote_b_id = await insert_lote(db_session, codigo_lote="LOTE-DUP-6-B")

    consumo_largo_id = await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_largo_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 12, 31),
        dias_facturados=366,
        kwh=Decimal("1200.000"),
    )
    consumo_a_id = await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_a_id,
        fecha_inicio=date(2024, 3, 1),
        fecha_fin=date(2024, 3, 31),
        dias_facturados=31,
        kwh=Decimal("100.000"),
    )
    consumo_b_id = await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_b_id,
        fecha_inicio=date(2024, 6, 1),
        fecha_fin=date(2024, 6, 30),
        dias_facturados=30,
        kwh=Decimal("100.000"),
    )

    source = SqlDuplicidadesDataSource(db_session)
    filas = await source.fetch_historial_periodos(lote_largo_id)
    assert len(filas) == 3

    resultado = detectar_periodos_conflictivos(list(filas))

    assert len(resultado) == 1
    entrada = resultado[0]
    assert entrada.suministro_id == suministro_id
    pares_detectados = {
        frozenset({p.consumo_id, p.conflicto_con_consumo_id}) for p in entrada.periodos
    }
    assert pares_detectados == {
        frozenset({consumo_largo_id, consumo_a_id}),
        frozenset({consumo_largo_id, consumo_b_id}),
    }
    assert frozenset({consumo_a_id, consumo_b_id}) not in pares_detectados
