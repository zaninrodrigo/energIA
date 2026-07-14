"""Integration tests for `SqlLoteProcesamientoPort` against real `energia_test` rows -- the
optimistic `UPDATE ... WHERE estado IN (...)` concurrency guarantee is the riskiest hand-written
part of this adapter, so it is exercised directly here (including the race outcome), not just
through the end-to-end route test.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.motor.domain.lote_estado import EstadoLote
from energia.contexts.motor.infrastructure.lote_procesamiento import SqlLoteProcesamientoPort
from tests.integration.contexts.motor.conftest import insert_consumo, insert_lote, insert_suministro

pytestmark = pytest.mark.integration


async def test_obtener_estado_actual_returns_none_for_unknown_codigo_lote(
    db_session: AsyncSession,
) -> None:
    port = SqlLoteProcesamientoPort(db_session)
    assert await port.obtener_estado_actual("NO-EXISTE") is None


async def test_obtener_estado_actual_reads_estado_and_cantidad_registros(
    db_session: AsyncSession,
) -> None:
    lote_id = await insert_lote(
        db_session, codigo_lote="LOTE-PORT-1", estado="Pendiente", cantidad_registros=7
    )

    port = SqlLoteProcesamientoPort(db_session)
    actual = await port.obtener_estado_actual("LOTE-PORT-1")

    assert actual is not None
    assert actual.id == lote_id
    assert actual.codigo_lote == "LOTE-PORT-1"
    assert actual.estado is EstadoLote.PENDIENTE
    assert actual.cantidad_registros == 7


async def test_contar_consumos_activos_counts_only_non_soft_deleted_rows(
    db_session: AsyncSession,
) -> None:
    from datetime import date
    from decimal import Decimal

    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-PORT-1")
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-PORT-2", cantidad_registros=2)
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("10.000"),
    )
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio=date(2024, 2, 1),
        fecha_fin=date(2024, 2, 29),
        dias_facturados=29,
        kwh=Decimal("20.000"),
    )

    port = SqlLoteProcesamientoPort(db_session)
    assert await port.contar_consumos_activos(lote_id) == 2


async def test_transicionar_estado_succeeds_from_an_allowed_state(db_session: AsyncSession) -> None:
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-PORT-3", estado="Pendiente")

    port = SqlLoteProcesamientoPort(db_session)
    moved = await port.transicionar_estado(
        lote_id,
        desde=frozenset({EstadoLote.PENDIENTE, EstadoLote.ERROR}),
        hacia=EstadoLote.PROCESANDO,
    )

    assert moved is True
    actual = await port.obtener_estado_actual("LOTE-PORT-3")
    assert actual is not None
    assert actual.estado is EstadoLote.PROCESANDO


async def test_transicionar_estado_fails_when_current_estado_is_not_in_desde(
    db_session: AsyncSession,
) -> None:
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-PORT-4", estado="Procesando")

    port = SqlLoteProcesamientoPort(db_session)
    moved = await port.transicionar_estado(
        lote_id,
        desde=frozenset({EstadoLote.PENDIENTE, EstadoLote.ERROR}),
        hacia=EstadoLote.PROCESANDO,
    )

    assert moved is False
    actual = await port.obtener_estado_actual("LOTE-PORT-4")
    assert actual is not None
    assert actual.estado is EstadoLote.PROCESANDO  # unchanged


async def test_transicionar_estado_does_not_commit(db_session: AsyncSession) -> None:
    """The port must never commit -- the caller (route boundary) controls the transaction."""
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-PORT-5", estado="Pendiente")

    port = SqlLoteProcesamientoPort(db_session)
    await port.transicionar_estado(
        lote_id, desde=frozenset({EstadoLote.PENDIENTE}), hacia=EstadoLote.PROCESANDO
    )
    await db_session.rollback()

    actual = await port.obtener_estado_actual("LOTE-PORT-5")
    assert actual is not None
    assert actual.estado is EstadoLote.PENDIENTE  # rolled back, never committed
