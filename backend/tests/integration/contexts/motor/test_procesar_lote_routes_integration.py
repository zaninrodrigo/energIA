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
    # (d) clean lote -> empty duplicidades (US-007, AI_ENGINE_SPEC.md §5): no overlaps, no
    # near-duplicate lecturas, no drifted OTHER lotes.
    assert body["duplicidades"]["periodos_conflictivos"] == []
    assert body["duplicidades"]["lecturas_near_duplicate"] == []
    assert body["duplicidades"]["drift_lotes"] == []
    # Etapas 3-4 (US-008/US-009, AI_ENGINE_SPEC.md §6/§7): every one of the 3 suministros gets a
    # feature vector (all cold-start -- a single period each) -- a summary only, never the full
    # vectors (response-size discipline; the vectors themselves live in `feature_vectors`).
    assert body["features"]["suministros_con_vector"] == 3
    assert body["features"]["cold_starts"] == 3
    assert body["features"]["con_periodos_conflictivos"] == 0
    verificacion_vectores = await db_session.execute(
        text("SELECT count(*) FROM feature_vectors WHERE lote_id = :lote_id"),
        {"lote_id": lote_id},
    )
    assert verificacion_vectores.scalar_one() == 3
    # Etapa 5 (AI_ENGINE_SPEC.md §8, recalibrado 2026-07-15 -- calibración v1.1): none of R1-R6
    # breach here. Before the recalibration, R5 fired for all 3 on `percentile_peer` alone: with 3
    # IDENTICAL kwh values in the same cohort (fallback categoria-alone pool, DEC-008, size 3 >=
    # COHORTE_FALLBACK_MINIMA), every one of them is "at or below" the group's own maximum --
    # percentile_peer = 1.0 for all three (inclusive by design, §7 FIX 3's note). R5 now ALSO
    # requires `peer_ratio` >= 2.5 (§8.2's calibration finding: this exact percentile-tie pattern
    # was 142/144, ~98.6%, of R5's false positives) -- with 3 identical kwh values, peer_ratio =
    # kwh_actual / cohort median = 100/100 = 1.0, well below the threshold, so R5 correctly does
    # NOT fire: being tied for the cohort maximum is not, by itself, anomalous.
    assert body["reglas"]["resumen"]["suministros_evaluados"] == 3
    assert body["reglas"]["resumen"]["suministros_con_hits"] == 0
    assert body["reglas"]["resumen"]["hits_por_regla"]["R1"] == 0
    assert body["reglas"]["resumen"]["hits_por_regla"]["R2"] == 0
    assert body["reglas"]["resumen"]["hits_por_regla"]["R3"] == 0
    assert body["reglas"]["resumen"]["hits_por_regla"]["R4"] == 0
    assert body["reglas"]["resumen"]["hits_por_regla"]["R5"] == 0
    assert body["reglas"]["resumen"]["hits_por_regla"]["R6"] == 0
    assert body["reglas"]["suministros"] == []


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


# ---------------------------------------------------------------------------------------------
# Etapa 2 -- duplicidades (US-007, AI_ENGINE_SPEC.md §5)
# ---------------------------------------------------------------------------------------------


async def test_v5_exclusion_and_duplicidades_both_report_the_same_overlap(
    motor_client: AsyncClient, db_session: AsyncSession
) -> None:
    """(a) Mandatory scenario: a suministro with a near-identical, shifted period across two
    lotes gets EXCLUDED by V5 (Etapa 1) from THIS lote's scoring, AND Etapa 2's duplicidades
    still lists the conflicting pair for it -- deliberately NOT filtered by Etapa 1's own
    exclusion (`domain/duplicidades.py`'s module docstring): the mark must survive for a FUTURE
    lote's feature windows, independent of whether THIS lote's gate excluded the suministro
    today."""
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-DUP-A")
    lote_previo_id = await insert_lote(
        db_session, codigo_lote="LOTE-DUP-A-PREVIO", cantidad_registros=1
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
    lote_actual_id = await insert_lote(db_session, codigo_lote="LOTE-DUP-A", cantidad_registros=1)
    consumo_actual_id = await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_actual_id,
        # Shifted by 14 days, evading uq_consumos_suministro_periodo -- overlaps the previous
        # period without being identical (V5's exact scenario).
        fecha_inicio=date(2024, 1, 15),
        fecha_fin=date(2024, 2, 15),
        dias_facturados=31,
        kwh=Decimal("100.000"),
    )

    response = await motor_client.post("/api/v1/motor/lotes/LOTE-DUP-A/procesar")

    assert response.status_code == 200
    body = response.json()
    assert body["estado_final"] == "Error"  # the lote's only suministro is excluded -> 0% valid
    assert any(h["check"] == "V5" for h in body["informe"]["hallazgos"])
    assert body["informe"]["suministros_excluidos"] == 1

    periodos_conflictivos = body["duplicidades"]["periodos_conflictivos"]
    assert len(periodos_conflictivos) == 1
    entrada = periodos_conflictivos[0]
    assert entrada["suministro_id"] == str(suministro_id)
    consumo_ids_marcados = {p["consumo_id"] for p in entrada["periodos"]}
    assert consumo_ids_marcados == {str(consumo_previo_id), str(consumo_actual_id)}


async def test_near_duplicate_lecturas_are_annotated(
    motor_client: AsyncClient, db_session: AsyncSession
) -> None:
    """(b) Mandatory scenario: two lecturas of the same suministro 2 days apart with an
    identical `lectura_actual` are annotated in `duplicidades.lecturas_near_duplicate` --
    informational only (DEC-005), never excludes the suministro (that stays Etapa 1's job)."""
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-DUP-B", cantidad_registros=1)
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-DUP-B")
    await _import_lectura_y_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("100.000"),
    )
    # A second lectura, 2 days after the first, with the SAME lectura_actual -- near-duplicate.
    await insert_lectura(
        db_session,
        suministro_id=suministro_id,
        fecha_lectura=date(2024, 2, 2),
        lectura_anterior=Decimal("0.000"),
        lectura_actual=Decimal("100.000"),
        dias_facturados=2,
    )

    response = await motor_client.post("/api/v1/motor/lotes/LOTE-DUP-B/procesar")

    assert response.status_code == 200
    body = response.json()
    assert body["estado_final"] == "Procesado"
    near_duplicates = body["duplicidades"]["lecturas_near_duplicate"]
    assert len(near_duplicates) == 1
    assert near_duplicates[0]["suministro_id"] == str(suministro_id)


async def test_consumo_migration_between_lotes_reports_drift_on_the_previous_lote(
    motor_client: AsyncClient, db_session: AsyncSession
) -> None:
    """(c) Mandatory scenario: migrate a consumo between lotes via re-import (period from lote A
    re-imported under lote B) -> drift annotation for lote A when processing lote B. Exercises
    the REAL natural-key upsert (`POST /api/v1/consumos/import`), not a raw-SQL simulation: proves
    `consumos.uq_consumos_suministro_periodo`'s `ON CONFLICT ... DO UPDATE` migrates the SAME
    row's `lote_id` in place, exactly as `domain/duplicidades.py`'s module docstring describes."""
    numero_suministro = "SUM-DUP-C"
    suministro_id = await insert_suministro(db_session, numero_suministro=numero_suministro)
    # lote_previo declares 2 records: one that STAYS (January), one that will be re-imported
    # under lote_actual (February) -- the natural-key upsert updates that SAME row's lote_id,
    # leaving lote_previo with only 1 active consumo against its still-declared 2.
    lote_previo_id = await insert_lote(
        db_session, codigo_lote="LOTE-DUP-C-PREVIO", cantidad_registros=2
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
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_previo_id,
        fecha_inicio=date(2024, 2, 1),
        fecha_fin=date(2024, 2, 29),
        dias_facturados=29,
        kwh=Decimal("90.000"),
    )
    await insert_lote(db_session, codigo_lote="LOTE-DUP-C", cantidad_registros=1)

    import_response = await motor_client.post(
        "/api/v1/consumos/import",
        json=[
            {
                "numero_suministro": numero_suministro,
                "codigo_lote": "LOTE-DUP-C",
                "fecha_inicio": "2024-02-01",
                "fecha_fin": "2024-02-29",
                "dias_facturados": 29,
                "kwh": 90.0,
            }
        ],
    )
    assert import_response.status_code == 200
    assert import_response.json()["updated"] == 1

    response = await motor_client.post("/api/v1/motor/lotes/LOTE-DUP-C/procesar")

    assert response.status_code == 200
    body = response.json()
    drift_lotes = body["duplicidades"]["drift_lotes"]
    assert len(drift_lotes) == 1
    assert drift_lotes[0]["codigo_lote"] == "LOTE-DUP-C-PREVIO"
    assert drift_lotes[0]["cantidad_registros"] == 2
    assert drift_lotes[0]["consumos_activos"] == 1
    assert drift_lotes[0]["diferencia"] == -1


# ---------------------------------------------------------------------------------------------
# Etapas 3-4 -- feature generation + statistical indicators (US-008/US-009, AI_ENGINE_SPEC.md
# §6/§7).
# ---------------------------------------------------------------------------------------------


async def test_error_lote_retry_upserts_feature_vectors_without_duplicating(
    motor_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Mission directive #6: a lote that lands in `Error` (below DEC-004's 95% threshold) still
    gets `feature_vectors` written for its non-excluded suministros (Etapa 3-4 run
    unconditionally, `_generar_features`'s docstring) -- a retry (`Error -> Procesando`) must
    upsert the SAME rows, never duplicate them, exactly like a real "fix the data, retry" cycle
    an operator would run."""
    suministro_valido = await insert_suministro(db_session, numero_suministro="SUM-FVR-RETRY-OK")
    lote_id = await insert_lote(db_session, codigo_lote="LOTE-FVR-RETRY", cantidad_registros=2)
    await _import_lectura_y_consumo(
        db_session,
        suministro_id=suministro_valido,
        lote_id=lote_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("100.000"),
    )
    # A second consumo with NO lectura (V1) -- this suministro is excluded, so 1/2 valid (50%)
    # keeps the lote below DEC-004's 95% threshold -> Error.
    suministro_invalido = await insert_suministro(db_session, numero_suministro="SUM-FVR-RETRY-BAD")
    await insert_consumo(
        db_session,
        suministro_id=suministro_invalido,
        lote_id=lote_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("50.000"),
    )

    primera = await motor_client.post("/api/v1/motor/lotes/LOTE-FVR-RETRY/procesar")
    assert primera.status_code == 200
    assert primera.json()["estado_final"] == "Error"
    assert primera.json()["features"]["suministros_con_vector"] == 1  # only the valid one

    conteo_tras_primera = await db_session.execute(
        text("SELECT count(*) FROM feature_vectors WHERE lote_id = :lote_id"), {"lote_id": lote_id}
    )
    assert conteo_tras_primera.scalar_one() == 1

    segunda = await motor_client.post("/api/v1/motor/lotes/LOTE-FVR-RETRY/procesar")
    assert segunda.status_code == 200
    assert segunda.json()["estado_final"] == "Error"

    conteo_tras_segunda = await db_session.execute(
        text("SELECT count(*) FROM feature_vectors WHERE lote_id = :lote_id"), {"lote_id": lote_id}
    )
    assert conteo_tras_segunda.scalar_one() == 1  # upserted, not duplicated


async def test_zero_streak_persists_correctly_when_a_conflicted_period_would_bridge_it(
    motor_client: AsyncClient, db_session: AsyncSession
) -> None:
    """FIX 1 (reviewer finding, CRITICAL), end-to-end reproduction through the REAL wiring
    (`energia_test`, the full HTTP journey -- not a fake/unit-level double): a suministro billed
    Jan=0, Feb=500 (double-billed -- TWO overlapping periods, both flagged conflictive by Etapa 2
    and excluded from Etapa 3's filtered window, DEC-005), Mar=0, Apr=0 (current, this lote). The
    persisted `feature_vectors.features->>'zero_consumption_streak'` must be `2` (Mar, Apr), NOT
    `3` (the bug this fix corrects: bridging Jan+Mar+Apr as if Feb's real 500 kWh never happened,
    reaching R1's Alta-severity trigger `>= 3`, AI_ENGINE_SPEC.md §8, on a false signal)."""
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-STREAK-BRIDGE")

    lote_enero_id = await insert_lote(
        db_session, codigo_lote="LOTE-STREAK-ENE", cantidad_registros=1
    )
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_enero_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("0.000"),
    )

    # Double-billed February: two OVERLAPPING periods for the same suministro -- Etapa 2 flags
    # BOTH sides conflictive (DEC-005), so BOTH are excluded from Etapa 3's filtered window, even
    # though each individually carries real, nonzero consumption.
    lote_febrero_a_id = await insert_lote(
        db_session, codigo_lote="LOTE-STREAK-FEB-A", cantidad_registros=1
    )
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_febrero_a_id,
        fecha_inicio=date(2024, 2, 1),
        fecha_fin=date(2024, 2, 15),
        dias_facturados=15,
        kwh=Decimal("500.000"),
    )
    lote_febrero_b_id = await insert_lote(
        db_session, codigo_lote="LOTE-STREAK-FEB-B", cantidad_registros=1
    )
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_febrero_b_id,
        fecha_inicio=date(2024, 2, 10),
        fecha_fin=date(2024, 2, 29),
        dias_facturados=19,
        kwh=Decimal("500.000"),
    )

    lote_marzo_id = await insert_lote(
        db_session, codigo_lote="LOTE-STREAK-MAR", cantidad_registros=1
    )
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_marzo_id,
        fecha_inicio=date(2024, 3, 1),
        fecha_fin=date(2024, 3, 31),
        dias_facturados=31,
        kwh=Decimal("0.000"),
    )

    lote_actual_id = await insert_lote(
        db_session, codigo_lote="LOTE-STREAK-ABR", cantidad_registros=1
    )
    await _import_lectura_y_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_actual_id,
        fecha_inicio=date(2024, 4, 1),
        fecha_fin=date(2024, 4, 30),
        dias_facturados=30,
        kwh=Decimal("0.000"),
    )

    response = await motor_client.post("/api/v1/motor/lotes/LOTE-STREAK-ABR/procesar")

    assert response.status_code == 200
    body = response.json()
    assert body["features"]["suministros_con_vector"] == 1
    assert body["features"]["con_periodos_conflictivos"] == 1  # Feb's double-billing, marked

    verificacion = await db_session.execute(
        text(
            "SELECT features->>'zero_consumption_streak' FROM feature_vectors "
            "WHERE lote_id = :lote_id AND suministro_id = :suministro_id"
        ),
        {"lote_id": lote_actual_id, "suministro_id": suministro_id},
    )
    streak_persistido = verificacion.scalar_one()
    assert streak_persistido == "2"


# ---------------------------------------------------------------------------------------------
# Etapa 5 -- reglas de negocio (AI_ENGINE_SPEC.md §8). End-to-end (real HTTP + `energia_test`),
# not just the wiring-level fakes in `tests/unit/contexts/motor/application/test_procesar_lote.py`.
# ---------------------------------------------------------------------------------------------


async def test_r1_fires_end_to_end_for_a_persistent_zero_streak_on_an_active_suministro(
    motor_client: AsyncClient, db_session: AsyncSession
) -> None:
    """3 prior zero-kwh periods (racha = R1's threshold) PLUS a 4th, CURRENT zero-kwh period --
    R1 must fire in the response's `reglas` field, off the REAL vector Etapas 3-4 persisted to
    `feature_vectors`. Only the CURRENT (April) consumo needs a matching lectura (V1-V7, Etapa 1,
    only ever evaluates THIS lote's own chain, `fetch_chain(lote_id)`) -- the 3 prior periods
    never go through `/procesar` at all here: Etapa 3's `fetch_historial_kwh` reads raw `consumos`
    rows across every lote regardless of whether those OTHER lotes were ever analyzed. Periods use
    REAL month-end days (Jan 31, Feb 29 -- 2024 is a leap year --, Mar 31) so V4 (period
    continuity, RD-017) does not itself exclude April's row: V4's window looks at the suministro's
    FULL cross-lote history, not just the current lote's own chain."""
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-R1-E2E")
    periodos_previos = [
        ("LOTE-R1-ENE", date(2024, 1, 1), date(2024, 1, 31)),
        ("LOTE-R1-FEB", date(2024, 2, 1), date(2024, 2, 29)),
        ("LOTE-R1-MAR", date(2024, 3, 1), date(2024, 3, 31)),
    ]
    for codigo, fecha_inicio, fecha_fin in periodos_previos:
        lote_previo_id = await insert_lote(db_session, codigo_lote=codigo, cantidad_registros=1)
        await insert_consumo(
            db_session,
            suministro_id=suministro_id,
            lote_id=lote_previo_id,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            dias_facturados=(fecha_fin - fecha_inicio).days + 1,
            kwh=Decimal("0.000"),
        )

    lote_actual_id = await insert_lote(db_session, codigo_lote="LOTE-R1-ABR", cantidad_registros=1)
    await _import_lectura_y_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_actual_id,
        fecha_inicio=date(2024, 4, 1),
        fecha_fin=date(2024, 4, 30),
        dias_facturados=30,
        kwh=Decimal("0.000"),
    )

    response = await motor_client.post("/api/v1/motor/lotes/LOTE-R1-ABR/procesar")

    assert response.status_code == 200
    body = response.json()
    assert body["reglas"]["resumen"]["suministros_con_hits"] == 1
    assert body["reglas"]["resumen"]["hits_por_regla"]["R1"] == 1
    entrada = body["reglas"]["suministros"][0]
    assert entrada["numero_suministro"] == "SUM-R1-E2E"
    reglas_disparadas = {hit["regla"] for hit in entrada["hits"]}
    assert "R1" in reglas_disparadas
    r1 = next(hit for hit in entrada["hits"] if hit["regla"] == "R1")
    assert r1["tipo"] == "Persistencia Anómala"
    assert r1["severidad"] == "Alta"


async def test_r3_fires_end_to_end_for_a_sudden_spike(
    motor_client: AsyncClient, db_session: AsyncSession
) -> None:
    """A 300% jump vs. the previous period (>= R3's +200% threshold) fires R3 in the response.
    Only the CURRENT (February) consumo needs a matching lectura -- same reasoning as R1's test
    above."""
    suministro_id = await insert_suministro(db_session, numero_suministro="SUM-R3-E2E")
    lote_previo_id = await insert_lote(db_session, codigo_lote="LOTE-R3-ENE", cantidad_registros=1)
    await insert_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_previo_id,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("100.000"),
    )

    lote_actual_id = await insert_lote(db_session, codigo_lote="LOTE-R3-FEB", cantidad_registros=1)
    await _import_lectura_y_consumo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_actual_id,
        fecha_inicio=date(2024, 2, 1),
        fecha_fin=date(2024, 2, 29),
        dias_facturados=29,
        kwh=Decimal("400.000"),  # (400-100)/100 = +300% >= R3's +200%
    )

    response = await motor_client.post("/api/v1/motor/lotes/LOTE-R3-FEB/procesar")

    assert response.status_code == 200
    body = response.json()
    entrada = body["reglas"]["suministros"][0]
    assert entrada["numero_suministro"] == "SUM-R3-E2E"
    r3 = next(hit for hit in entrada["hits"] if hit["regla"] == "R3")
    assert r3["tipo"] == "Incremento Brusco"
    assert r3["severidad"] == "Media"
    assert "300%" in r3["descripcion"]
