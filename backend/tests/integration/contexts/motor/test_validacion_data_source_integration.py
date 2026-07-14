"""Integration tests for `SqlValidacionDataSource.fetch_chain` against real `energia_test` rows --
the set-based SQL query is the riskiest hand-written part of this slice (window function across
a suministro's full consumo history, three joins), so it is exercised directly here, not just
through the end-to-end route test.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.motor.infrastructure.validacion_data_source import SqlValidacionDataSource
from tests.integration.contexts.motor.conftest import (
    insert_consumo,
    insert_lectura,
    insert_lote,
    insert_suministro,
)

pytestmark = pytest.mark.integration


async def test_fetch_chain_returns_one_row_per_active_consumo_of_the_lote(
    db_session: AsyncSession,
) -> None:
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-CHAIN-1")
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-CHAIN-1", cantidad_registros=1)
    lectura_id = await insert_lectura(
        db_session,
        suministro_id=suministro_id,
        fecha_lectura=date(2024, 1, 31),
        lectura_anterior=Decimal("100.000"),
        lectura_actual=Decimal("200.000"),
        dias_facturados=31,
    )
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("100.000"),
        lectura_id=lectura_id,
    )

    source = SqlValidacionDataSource(db_session)
    filas = await source.fetch_chain(lote_id)

    assert len(filas) == 1
    fila = filas[0]
    assert fila.suministro_id == suministro_id
    assert fila.lectura_id == lectura_id
    assert fila.lectura_anterior == Decimal("100.000")
    assert fila.lectura_actual == Decimal("200.000")
    assert fila.lectura_dias_facturados == 31
    assert fila.prev_fecha_inicio is None
    assert fila.prev_fecha_fin is None
    assert fila.categoria_deleted_at is None


async def test_fetch_chain_leaves_lectura_fields_none_without_lectura_id(
    db_session: AsyncSession,
) -> None:
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-CHAIN-2")
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-CHAIN-2", cantidad_registros=1)
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("50.000"),
        lectura_id=None,
    )

    source = SqlValidacionDataSource(db_session)
    filas = await source.fetch_chain(lote_id)

    assert len(filas) == 1
    assert filas[0].lectura_id is None
    assert filas[0].lectura_anterior is None
    assert filas[0].lectura_actual is None
    assert filas[0].lectura_dias_facturados is None


async def test_fetch_chain_computes_prev_period_across_lotes(db_session: AsyncSession) -> None:
    """RD-017 continuity is a suministro-level invariant, not a lote-scoped one: the previous
    period used by V4/V5 must be found even when it belongs to a DIFFERENT (earlier) lote."""
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-CHAIN-3")
    lote_previo_id = await insert_lote(
        db_session, codigo_lote="LOTE-CHAIN-3-PREVIO", cantidad_registros=1
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
    lote_actual_id = await insert_lote(
        db_session, codigo_lote="LOTE-CHAIN-3-ACTUAL", cantidad_registros=1
    )
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_actual_id,
        fecha_inicio=date(2024, 2, 1),
        fecha_fin=date(2024, 2, 29),
        dias_facturados=29,
        kwh=Decimal("90.000"),
    )

    source = SqlValidacionDataSource(db_session)
    filas = await source.fetch_chain(lote_actual_id)

    assert len(filas) == 1
    assert filas[0].prev_fecha_inicio == date(2024, 1, 1)
    assert filas[0].prev_fecha_fin == date(2024, 1, 31)


async def test_fetch_chain_flags_soft_deleted_categoria(db_session: AsyncSession) -> None:
    categoria_id = uuid4()
    await db_session.execute(
        text(
            "INSERT INTO categorias_tarifarias (id, nombre, deleted_at) "
            "VALUES (:id, :nombre, now())"
        ),
        {"id": categoria_id, "nombre": "Obsoleta de prueba"},
    )
    await db_session.commit()
    suministro_id = await insert_suministro(
        db_session, numero_suministro="SUM-CHAIN-4", categoria_tarifaria_id=categoria_id
    )
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-CHAIN-4", cantidad_registros=1)
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("10.000"),
    )

    source = SqlValidacionDataSource(db_session)
    filas = await source.fetch_chain(lote_id)

    assert len(filas) == 1
    assert filas[0].categoria_deleted_at is not None


async def test_fetch_chain_only_returns_rows_of_the_requested_lote(
    db_session: AsyncSession,
) -> None:
    suministro_a = await insert_suministro(db_session, numero_suministro="SUM-CHAIN-5A")
    suministro_b = await insert_suministro(db_session, numero_suministro="SUM-CHAIN-5B")
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-CHAIN-5", cantidad_registros=1)
    otro_lote_id = await insert_lote(db_session, codigo_lote="LOTE-CHAIN-5-OTRO")
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

    source = SqlValidacionDataSource(db_session)
    filas = await source.fetch_chain(lote_id)

    assert len(filas) == 1
    assert filas[0].suministro_id == suministro_a
