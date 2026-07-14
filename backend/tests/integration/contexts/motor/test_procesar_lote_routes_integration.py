"""Integration tests for `POST /api/v1/motor/lotes/{codigo_lote}/procesar` -- the full HTTP
journey against real `energia_test` rows, seeded through the REAL import endpoints
(`clientes`/`suministros`/`consumos`) wherever the mission calls for it, mirroring the manual
curl journey `AGENTS`/mission asked to be run and reported.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from energia.contexts.motor.infrastructure.validacion_data_source import SqlValidacionDataSource
from tests.integration.contexts.motor.conftest import (
    insert_consumo,
    insert_lectura,
    insert_lote,
    insert_suministro,
)

pytestmark = pytest.mark.integration


async def _import_lectura_y_consumo(
    db_session: AsyncSession,
    *,
    suministro_id: UUID,
    lote_id: UUID,
    fecha_inicio: date,
    fecha_fin: date,
    dias_facturados: int,
    kwh: Decimal,
) -> None:
    """Builds one fully-valid consumo (V1-V7 all pass): a matching lectura + a consumo whose kwh
    equals the lectura's delta and whose dias_facturados match."""
    lectura_id = await insert_lectura(
        db_session,
        suministro_id=suministro_id,
        fecha_lectura=fecha_fin,
        lectura_anterior=Decimal("0.000"),
        lectura_actual=kwh,
        dias_facturados=dias_facturados,
    )
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        dias_facturados=dias_facturados,
        kwh=kwh,
        lectura_id=lectura_id,
    )


async def test_happy_path_transitions_pendiente_lote_to_procesado(
    motor_client: AsyncClient, db_session: AsyncSession
) -> None:
    """(a) seed cliente -> suministro -> lote (cantidad_registros=3) -> 3 consumos, each with a
    matching lectura so every V1-V7 check passes; POST /procesar -> 200, estado Procesado."""
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-HAPPY", cantidad_registros=3)
    for i in range(3):
        suministro_id = await insert_suministro(db_session, numero_suministro=f"SUM-HAPPY-{i}")
        await _import_lectura_y_consumo(
            db_session,
            suministro_id=suministro_id,
            lote_id=lote_id,
            fecha_inicio=date(2024, 1, 1),
            fecha_fin=date(2024, 1, 31),
            dias_facturados=31,
            kwh=Decimal("100.000"),
        )

    response = await motor_client.post("/api/v1/motor/lotes/LOTE-HAPPY/procesar")

    assert response.status_code == 200
    body = response.json()
    assert body["estado_final"] == "Procesado"
    assert body["informe"]["total_suministros"] == 3
    assert body["informe"]["suministros_excluidos"] == 0
    assert body["informe"]["umbral_cumplido"] is True
    assert body["informe"]["hallazgos"] == []


async def test_reprocessing_an_already_procesado_lote_is_conflict(
    motor_client: AsyncClient, db_session: AsyncSession
) -> None:
    """(b) POST again on the same (now Procesado) lote -> 409 (RD-010)."""
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-HAPPY-2", cantidad_registros=1)
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-HAPPY-2")
    await _import_lectura_y_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("50.000"),
    )

    first = await motor_client.post("/api/v1/motor/lotes/LOTE-HAPPY-2/procesar")
    assert first.status_code == 200
    assert first.json()["estado_final"] == "Procesado"

    second = await motor_client.post("/api/v1/motor/lotes/LOTE-HAPPY-2/procesar")
    assert second.status_code == 409


async def test_incomplete_lote_returns_422_and_leaves_estado_pendiente(
    motor_client: AsyncClient, db_session: AsyncSession
) -> None:
    """(c) a lote with cantidad_registros=5 but only 2 consumos -> 422, estado stays Pendiente."""
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-INCOMPLETO", cantidad_registros=5)
    for i in range(2):
        suministro_id = await insert_suministro(db_session, numero_suministro=f"SUM-INCOMPLETO-{i}")
        await insert_consumo(
            db_session,
            suministro_id=suministro_id,
            lote_id=lote_id,
            fecha_inicio=date(2024, 1, 1),
            fecha_fin=date(2024, 1, 31),
            dias_facturados=31,
            kwh=Decimal("10.000"),
        )

    response = await motor_client.post("/api/v1/motor/lotes/LOTE-INCOMPLETO/procesar")

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["cantidad_registros"] == 5
    assert detail["consumos_activos"] == 2

    verificacion = await motor_client.get("/api/v1/lotes", params={"estado": "Pendiente"})
    codigos = [item["codigo_lote"] for item in verificacion.json()["items"]]
    assert "LOTE-INCOMPLETO" in codigos


async def test_unknown_lote_returns_404(motor_client: AsyncClient) -> None:
    response = await motor_client.post("/api/v1/motor/lotes/NO-EXISTE/procesar")
    assert response.status_code == 404


async def test_lote_below_threshold_lands_in_error_and_retry_stays_consistent(
    motor_client: AsyncClient, db_session: AsyncSession
) -> None:
    """(d) a lote whose single consumo period partially overlaps an earlier, already-imported
    period for the same suministro (a DIFFERENT lote) -> V5 excludes the only suministro in this
    lote -> 0% valid -> estado Error. A retry (Error -> Procesando re-entry) is 200/Error again,
    consistently, since the same overlap is still there."""
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-OVERLAP")
    lote_previo_id = await insert_lote(
        db_session, codigo_lote="LOTE-OVERLAP-PREVIO", cantidad_registros=1
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

    lote_id = await insert_lote(db_session, codigo_lote="LOTE-OVERLAP", cantidad_registros=1)
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_id,
        # Starts 2024-01-15, which is <= 2024-01-31 (the previous period's end) -> V5 overlap.
        fecha_inicio=date(2024, 1, 15),
        fecha_fin=date(2024, 2, 15),
        dias_facturados=31,
        kwh=Decimal("100.000"),
    )

    first = await motor_client.post("/api/v1/motor/lotes/LOTE-OVERLAP/procesar")
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["estado_final"] == "Error"
    assert first_body["informe"]["umbral_cumplido"] is False
    assert first_body["informe"]["suministros_excluidos"] == 1
    assert any(h["check"] == "V5" for h in first_body["informe"]["hallazgos"])

    verificacion = await motor_client.get("/api/v1/lotes", params={"estado": "Error"})
    codigos = [item["codigo_lote"] for item in verificacion.json()["items"]]
    assert "LOTE-OVERLAP" in codigos

    second = await motor_client.post("/api/v1/motor/lotes/LOTE-OVERLAP/procesar")
    assert second.status_code == 200
    assert second.json()["estado_final"] == "Error"


async def test_consumo_inserted_mid_flight_returns_409_and_retry_succeeds(
    motor_client: AsyncClient,
    db_session: AsyncSession,
    test_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FIX 1 (CRITICAL): reproduces the reviewer's race (AI_ENGINE_SPEC.md §2.5,
    `LoteModificadoError`). The completeness gate certifies `consumos_activos=1` (matching
    `cantidad_registros=1`), but a SECOND consumo for the SAME lote lands -- via an independent
    session/transaction, simulating a concurrent import -- exactly as `fetch_chain` runs: the
    analyzed cohort would silently differ from the gate-certified one. The recount right after
    `fetch_chain` (same transaction) must catch this -> 409, and the single-transaction rollback
    reverts the `Procesando` transition automatically -> lote back to `Pendiente`.

    A retry succeeds once `cantidad_registros` is corrected to 2 -- exactly what a real operator
    would do upon noticing the declared count undercounted the batch (the extra consumo the race
    inserted is now a permanent, legitimate part of the lote)."""
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-RACE", cantidad_registros=1)
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-RACE-1")
    await _import_lectura_y_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("100.000"),
    )

    original_fetch_chain = SqlValidacionDataSource.fetch_chain
    disparado = False

    async def _fetch_chain_con_insert_concurrente(self: SqlValidacionDataSource, lote_id_arg: UUID):  # type: ignore[no-untyped-def]
        nonlocal disparado
        if not disparado:
            disparado = True
            # A concurrent import landing a SECOND consumo for the SAME lote, via an
            # independent session/transaction, exactly as `fetch_chain` runs (the gate, above,
            # already ran and certified `consumos_activos=1`).
            async with test_session_factory() as segunda_sesion:
                otro_suministro_id = await insert_suministro(
                    segunda_sesion, numero_suministro="SUM-RACE-2"
                )
                await _import_lectura_y_consumo(
                    segunda_sesion,
                    suministro_id=otro_suministro_id,
                    lote_id=lote_id_arg,
                    fecha_inicio=date(2024, 2, 1),
                    fecha_fin=date(2024, 2, 29),
                    dias_facturados=29,
                    kwh=Decimal("50.000"),
                )
        return await original_fetch_chain(self, lote_id_arg)

    monkeypatch.setattr(SqlValidacionDataSource, "fetch_chain", _fetch_chain_con_insert_concurrente)

    primero = await motor_client.post("/api/v1/motor/lotes/LOTE-RACE/procesar")
    assert primero.status_code == 409
    assert "modificado" in primero.json()["detail"]

    verificacion = await motor_client.get("/api/v1/lotes", params={"estado": "Pendiente"})
    codigos = [item["codigo_lote"] for item in verificacion.json()["items"]]
    assert "LOTE-RACE" in codigos

    await db_session.execute(
        text("UPDATE lotes SET cantidad_registros = 2 WHERE id = :lote_id"), {"lote_id": lote_id}
    )
    await db_session.commit()

    segundo = await motor_client.post("/api/v1/motor/lotes/LOTE-RACE/procesar")
    assert segundo.status_code == 200
    assert segundo.json()["estado_final"] == "Procesado"
