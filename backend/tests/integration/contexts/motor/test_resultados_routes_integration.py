"""Integration tests for `GET /api/v1/motor/lotes/{codigo_lote}/resultados` -- the IRE ranking of
a processed lote (the dashboard's data source, RN-009), against real `energia_test` rows produced
by the REAL pipeline (`POST /procesar`, Etapas 1-8), never hand-inserted `resultados_ia`/`ire`/
`anomalias`/`impacto_economico` rows -- proving the whole vertical slice (write side + this new
read side) composes correctly.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.contexts.motor.conftest import (
    insert_consumo,
    insert_lectura,
    insert_lote,
    insert_suministro,
)

pytestmark = pytest.mark.integration

_CODIGO_LOTE = "LOTE-RANK-1"
_CODIGO_LOTE_HISTORIAL = "LOTE-RANK-1-HIST"

# Four sequential, non-overlapping monthly periods -- P1-P3 are "history" (a prior lote), P4 is
# the CURRENT lote under test. Mirrors `test_procesar_lote_routes_integration.py`'s own
# `lote_previo_id` pattern for building cross-lote history.
_P1 = (date(2024, 1, 1), date(2024, 1, 31), 31)
_P2 = (date(2024, 2, 1), date(2024, 2, 29), 29)
_P3 = (date(2024, 3, 1), date(2024, 3, 31), 31)
_P4 = (date(2024, 4, 1), date(2024, 4, 30), 30)


async def _seed_periodo(
    db_session: AsyncSession,
    *,
    suministro_id: UUID,
    lote_id: UUID,
    fecha_inicio: date,
    fecha_fin: date,
    dias_facturados: int,
    lectura_anterior: Decimal,
    kwh: Decimal,
) -> Decimal:
    """Inserts one lectura+consumo period; returns the new cumulative lectura_actual (the next
    period's lectura_anterior) -- a physical meter never goes backward (RD-013)."""
    lectura_actual = lectura_anterior + kwh
    lectura_id = await insert_lectura(
        db_session,
        suministro_id=suministro_id,
        fecha_lectura=fecha_fin,
        lectura_anterior=lectura_anterior,
        lectura_actual=lectura_actual,
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
    return lectura_actual


async def _seed_suministro_con_historial(
    db_session: AsyncSession,
    *,
    numero_suministro: str,
    lote_historial_id: UUID,
    lote_actual_id: UUID,
    kwh_historial: list[Decimal],
    kwh_actual: Decimal,
) -> UUID:
    """One suministro: `kwh_historial` (0-3 entries, P1-P3) lands in `lote_historial_id`
    (irrelevant to the completeness gate -- that lote is never processed), `kwh_actual` (P4) lands
    in `lote_actual_id` (the lote this test suite processes and queries)."""
    suministro_id = await insert_suministro(db_session, numero_suministro=numero_suministro)
    lectura_acumulada = Decimal("0.000")
    for (fecha_inicio, fecha_fin, dias), kwh in zip(
        (_P1, _P2, _P3)[: len(kwh_historial)], kwh_historial, strict=True
    ):
        lectura_acumulada = await _seed_periodo(
            db_session,
            suministro_id=suministro_id,
            lote_id=lote_historial_id,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            dias_facturados=dias,
            lectura_anterior=lectura_acumulada,
            kwh=kwh,
        )
    fecha_inicio, fecha_fin, dias = _P4
    await _seed_periodo(
        db_session,
        suministro_id=suministro_id,
        lote_id=lote_actual_id,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        dias_facturados=dias,
        lectura_anterior=lectura_acumulada,
        kwh=kwh_actual,
    )
    return suministro_id


async def _seed_lote_analizado(motor_client: AsyncClient, db_session: AsyncSession) -> None:
    """5 suministros, deliberately heterogeneous (a cold-start, two stable baselines, a sharp
    drop, a sharp spike) so Etapas 6-8 produce genuinely DIFFERENT IRE values -- proving the
    ranking's descending order is real, not a coincidence of identical rows. Then runs the REAL
    pipeline (`POST /procesar`) so `resultados_ia`/`ire`/`anomalias`/`impacto_economico` are
    written by the actual Etapa 7-8 code under test elsewhere, not faked here."""
    lote_actual_id = await insert_lote(db_session, codigo_lote=_CODIGO_LOTE, cantidad_registros=5)
    lote_historial_id = await insert_lote(db_session, codigo_lote=_CODIGO_LOTE_HISTORIAL)

    await _seed_suministro_con_historial(
        db_session,
        numero_suministro="SUM-RANK-COLD",
        lote_historial_id=lote_historial_id,
        lote_actual_id=lote_actual_id,
        kwh_historial=[],
        kwh_actual=Decimal("100.000"),
    )
    await _seed_suministro_con_historial(
        db_session,
        numero_suministro="SUM-RANK-STABLE-A",
        lote_historial_id=lote_historial_id,
        lote_actual_id=lote_actual_id,
        kwh_historial=[Decimal("100.000"), Decimal("100.000"), Decimal("100.000")],
        kwh_actual=Decimal("100.000"),
    )
    await _seed_suministro_con_historial(
        db_session,
        numero_suministro="SUM-RANK-STABLE-B",
        lote_historial_id=lote_historial_id,
        lote_actual_id=lote_actual_id,
        kwh_historial=[Decimal("90.000"), Decimal("90.000"), Decimal("90.000")],
        kwh_actual=Decimal("90.000"),
    )
    await _seed_suministro_con_historial(
        db_session,
        numero_suministro="SUM-RANK-DROP",
        lote_historial_id=lote_historial_id,
        lote_actual_id=lote_actual_id,
        kwh_historial=[Decimal("100.000"), Decimal("100.000"), Decimal("100.000")],
        kwh_actual=Decimal("10.000"),
    )
    await _seed_suministro_con_historial(
        db_session,
        numero_suministro="SUM-RANK-SPIKE",
        lote_historial_id=lote_historial_id,
        lote_actual_id=lote_actual_id,
        kwh_historial=[Decimal("100.000"), Decimal("100.000"), Decimal("100.000")],
        kwh_actual=Decimal("900.000"),
    )

    procesar = await motor_client.post(f"/api/v1/motor/lotes/{_CODIGO_LOTE}/procesar")
    assert procesar.status_code == 200
    assert procesar.json()["estado_final"] == "Procesado"


async def test_ranking_is_ordered_by_ire_descending(
    motor_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_lote_analizado(motor_client, db_session)

    response = await motor_client.get(f"/api/v1/motor/lotes/{_CODIGO_LOTE}/resultados")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert len(body["items"]) == 5
    ire_valores = [item["ire_valor"] for item in body["items"]]
    assert ire_valores == sorted(ire_valores, reverse=True)
    numeros = {item["numero_suministro"] for item in body["items"]}
    assert numeros == {
        "SUM-RANK-COLD",
        "SUM-RANK-STABLE-A",
        "SUM-RANK-STABLE-B",
        "SUM-RANK-DROP",
        "SUM-RANK-SPIKE",
    }


async def test_ranking_row_exposes_parsed_observaciones_and_display_fields(
    motor_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_lote_analizado(motor_client, db_session)

    response = await motor_client.get(f"/api/v1/motor/lotes/{_CODIGO_LOTE}/resultados")

    body = response.json()
    for item in body["items"]:
        assert isinstance(item["suministro_id"], str)
        assert item["ire_nivel"] in {"Muy Bajo", "Bajo", "Medio", "Alto", "Crítico"}
        assert item["clasificacion"] in {"Normal", "Atención", "Alto Riesgo", "Crítico"}
        assert item["categoria_tarifaria"]
        # DEC-016's explicability breakdown, ALREADY PARSED into structured objects -- never a
        # raw JSON string.
        assert isinstance(item["observaciones"], list)
        for factor in item["observaciones"]:
            assert set(factor.keys()) == {"factor", "contribution", "reason"}
            assert isinstance(factor["factor"], str)
            assert isinstance(factor["contribution"], float)
            assert isinstance(factor["reason"], str)
        assert isinstance(item["anomalias"], list)
        for anomalia in item["anomalias"]:
            assert set(anomalia.keys()) == {"tipo", "severidad", "descripcion"}
    # At least one factor somewhere: `resultados_ia.observaciones` is never an empty breakdown
    # for an analyzed suministro (score_ia alone always contributes something, §10.1).
    assert any(item["observaciones"] for item in body["items"])


async def test_iee_kwh_is_null_only_for_the_cold_start_suministro(
    motor_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_lote_analizado(motor_client, db_session)

    response = await motor_client.get(f"/api/v1/motor/lotes/{_CODIGO_LOTE}/resultados")

    items_by_numero = {item["numero_suministro"]: item for item in response.json()["items"]}
    assert items_by_numero["SUM-RANK-COLD"]["iee_kwh"] is None
    for numero in ("SUM-RANK-STABLE-A", "SUM-RANK-STABLE-B", "SUM-RANK-DROP", "SUM-RANK-SPIKE"):
        assert items_by_numero[numero]["iee_kwh"] is not None


async def test_resumen_counts_the_whole_lote_regardless_of_filters(
    motor_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_lote_analizado(motor_client, db_session)

    response = await motor_client.get(f"/api/v1/motor/lotes/{_CODIGO_LOTE}/resultados")

    resumen = response.json()["resumen"]
    assert resumen["total_resultados"] == 5
    assert sum(resumen["conteo_por_nivel"].values()) == 5
    assert set(resumen["conteo_por_nivel"].keys()) == {
        "Muy Bajo",
        "Bajo",
        "Medio",
        "Alto",
        "Crítico",
    }
    assert sum(resumen["conteo_por_clasificacion"].values()) == 5
    assert set(resumen["conteo_por_clasificacion"].keys()) == {
        "Normal",
        "Atención",
        "Alto Riesgo",
        "Crítico",
    }
    assert resumen["con_anomalias"] >= 0
    assert resumen["suma_iee_kwh"] >= 0.0

    # Filtering `items`/`total` must NOT change `resumen` -- it is always the whole lote's
    # UNFILTERED summary (the dashboard's KPI cards don't move when the table is filtered).
    filtrado = await motor_client.get(
        f"/api/v1/motor/lotes/{_CODIGO_LOTE}/resultados", params={"clasificacion": "Normal"}
    )
    assert filtrado.json()["resumen"] == resumen


async def test_nivel_filter_returns_only_matching_rows(
    motor_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_lote_analizado(motor_client, db_session)
    unfiltered = await motor_client.get(f"/api/v1/motor/lotes/{_CODIGO_LOTE}/resultados")
    items = unfiltered.json()["items"]
    nivel_objetivo = items[0]["ire_nivel"]  # the top (highest-IRE) row's own band
    esperados = [item for item in items if item["ire_nivel"] == nivel_objetivo]

    response = await motor_client.get(
        f"/api/v1/motor/lotes/{_CODIGO_LOTE}/resultados", params={"nivel": nivel_objetivo}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == len(esperados)
    assert all(item["ire_nivel"] == nivel_objetivo for item in body["items"])


async def test_invalid_nivel_returns_422(
    motor_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_lote_analizado(motor_client, db_session)

    response = await motor_client.get(
        f"/api/v1/motor/lotes/{_CODIGO_LOTE}/resultados", params={"nivel": "Altísimo"}
    )

    assert response.status_code == 422


async def test_invalid_clasificacion_returns_422(
    motor_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_lote_analizado(motor_client, db_session)

    response = await motor_client.get(
        f"/api/v1/motor/lotes/{_CODIGO_LOTE}/resultados", params={"clasificacion": "no-existe"}
    )

    assert response.status_code == 422


async def test_pagination_honors_limit_and_offset(
    motor_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_lote_analizado(motor_client, db_session)

    response = await motor_client.get(
        f"/api/v1/motor/lotes/{_CODIGO_LOTE}/resultados", params={"limit": 2, "offset": 1}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 2
    assert body["offset"] == 1
    assert len(body["items"]) == 2
    assert body["total"] == 5


async def test_unanalyzed_but_real_lote_returns_200_with_empty_page(
    db_session: AsyncSession, motor_client: AsyncClient
) -> None:
    """A lote that EXISTS (imported) but was never processed -- 200 with an empty page and
    `total: 0`/`resumen.total_resultados: 0`, NEVER 404 (the lote is real, just unanalyzed)."""
    await insert_lote(db_session, codigo_lote="LOTE-SIN-PROCESAR", cantidad_registros=3)

    response = await motor_client.get("/api/v1/motor/lotes/LOTE-SIN-PROCESAR/resultados")

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["resumen"]["total_resultados"] == 0
    assert body["resumen"]["conteo_por_nivel"] == {
        "Muy Bajo": 0,
        "Bajo": 0,
        "Medio": 0,
        "Alto": 0,
        "Crítico": 0,
    }


async def test_nonexistent_lote_returns_404(motor_client: AsyncClient) -> None:
    response = await motor_client.get("/api/v1/motor/lotes/NO-EXISTE/resultados")

    assert response.status_code == 404
    assert "NO-EXISTE" in response.json()["detail"]


async def test_soft_deleted_suministro_is_excluded_from_the_ranking(
    motor_client: AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_lote_analizado(motor_client, db_session)
    before = await motor_client.get(f"/api/v1/motor/lotes/{_CODIGO_LOTE}/resultados")
    assert before.json()["total"] == 5

    await db_session.execute(
        text("UPDATE suministros SET deleted_at = now() WHERE numero_suministro = 'SUM-RANK-COLD'")
    )
    await db_session.commit()

    after = await motor_client.get(f"/api/v1/motor/lotes/{_CODIGO_LOTE}/resultados")

    assert after.status_code == 200
    body = after.json()
    assert body["total"] == 4
    assert body["resumen"]["total_resultados"] == 4
    assert "SUM-RANK-COLD" not in {item["numero_suministro"] for item in body["items"]}
