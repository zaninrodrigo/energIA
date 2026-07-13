"""Integration tests for the `consumos` HTTP API against the real `energia_test` database.

Covers the acceptance path for US-004: import from a JSON payload (creating and rejecting records
individually, including both directory resolutions and the optional lectura resolution), the
`consumo_promedio_diario`/`fecha_lectura` UNSET/null/value semantics, idempotent re-import,
partition routing for an out-of-range `fecha_inicio`, soft-delete re-import behavior, and the
paginated list endpoint (with both natural-key filters). Never touches `energia` -- see
tests/integration/conftest.py for the isolation strategy.
"""

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.contexts.consumos.conftest import insert_lote, insert_suministro

pytestmark = pytest.mark.integration


@pytest.fixture
async def consumo_fk_ids(db_session: AsyncSession) -> tuple[str, str]:
    """A ready-to-use `(numero_suministro, codigo_lote)` pair for consumo import payloads."""
    await insert_suministro(db_session, numero_suministro="CON-SUM-1")
    await insert_lote(db_session, codigo_lote="CON-LOTE-1")
    return "CON-SUM-1", "CON-LOTE-1"


async def test_import_creates_valid_records_and_rejects_invalid_ones_individually(
    consumos_client: AsyncClient, consumo_fk_ids: tuple[str, str]
) -> None:
    numero_suministro, codigo_lote = consumo_fk_ids
    payload = [
        {
            "numero_suministro": numero_suministro,
            "codigo_lote": codigo_lote,
            "fecha_inicio": "2024-01-01",
            "fecha_fin": "2024-01-31",
            "dias_facturados": 31,
            "kwh": 310.0,
        },
        {
            "numero_suministro": numero_suministro,
            "codigo_lote": codigo_lote,
            "fecha_inicio": "2024-02-01",
            "fecha_fin": "2024-02-29",
            "dias_facturados": 29,
            "kwh": 290.0,
        },
        {
            "numero_suministro": "does-not-exist",
            "codigo_lote": codigo_lote,
            "fecha_inicio": "2024-03-01",
            "fecha_fin": "2024-03-31",
            "dias_facturados": 31,
            "kwh": 100.0,
        },
        {
            "numero_suministro": numero_suministro,
            "codigo_lote": "does-not-exist",
            "fecha_inicio": "2024-04-01",
            "fecha_fin": "2024-04-30",
            "dias_facturados": 30,
            "kwh": 100.0,
        },
        {
            "numero_suministro": numero_suministro,
            "codigo_lote": codigo_lote,
            "fecha_inicio": "2024-05-31",
            "fecha_fin": "2024-05-01",
            "dias_facturados": 30,
            "kwh": 100.0,
        },
    ]

    response = await consumos_client.post("/api/v1/consumos/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 2
    assert body["updated"] == 0
    assert body["unchanged"] == 0
    assert len(body["rejected"]) == 3
    all_reasons = " ".join(
        reason for rejected in body["rejected"] for reason in rejected["reasons"]
    )
    assert "suministro inexistente" in all_reasons
    assert "lote inexistente" in all_reasons
    assert "fecha_fin" in all_reasons


async def test_import_returns_422_for_a_malformed_body(consumos_client: AsyncClient) -> None:
    response = await consumos_client.post("/api/v1/consumos/import", json={"not": "a list"})

    assert response.status_code == 422


async def test_import_rejects_a_payload_with_an_unknown_field(
    consumos_client: AsyncClient, consumo_fk_ids: tuple[str, str]
) -> None:
    numero_suministro, codigo_lote = consumo_fk_ids
    payload = [
        {
            "numero_suministro": numero_suministro,
            "codigo_lote": codigo_lote,
            "fecha_inicio": "2024-01-01",
            "fecha_fin": "2024-01-31",
            "dias_facturados": 31,
            "kwh": 100.0,
            "estado": "Procesado",
        }
    ]

    response = await consumos_client.post("/api/v1/consumos/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 0
    assert any("estado" in reason for reason in body["rejected"][0]["reasons"])


async def test_import_rejects_a_boolean_kwh_individually(
    consumos_client: AsyncClient, consumo_fk_ids: tuple[str, str]
) -> None:
    numero_suministro, codigo_lote = consumo_fk_ids
    payload = [
        {
            "numero_suministro": numero_suministro,
            "codigo_lote": codigo_lote,
            "fecha_inicio": "2024-01-01",
            "fecha_fin": "2024-01-31",
            "dias_facturados": 31,
            "kwh": True,
        }
    ]

    response = await consumos_client.post("/api/v1/consumos/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 0
    assert any("booleano" in reason for reason in body["rejected"][0]["reasons"])


async def test_import_rejects_an_out_of_range_dias_facturados_instead_of_500ing_the_batch(
    consumos_client: AsyncClient, consumo_fk_ids: tuple[str, str]
) -> None:
    numero_suministro, codigo_lote = consumo_fk_ids
    payload = [
        {
            "numero_suministro": numero_suministro,
            "codigo_lote": codigo_lote,
            "fecha_inicio": "2024-01-01",
            "fecha_fin": "2024-01-31",
            "dias_facturados": 99999999999,
            "kwh": 100.0,
        }
    ]

    response = await consumos_client.post("/api/v1/consumos/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 0
    assert any("dias_facturados" in reason for reason in body["rejected"][0]["reasons"])


async def test_import_rejects_a_nan_kwh_instead_of_500ing_the_batch(
    consumos_client: AsyncClient, consumo_fk_ids: tuple[str, str]
) -> None:
    numero_suministro, codigo_lote = consumo_fk_ids
    payload_text = (
        "["
        f'{{"numero_suministro": "{numero_suministro}", "codigo_lote": "{codigo_lote}", '
        '"fecha_inicio": "2024-01-01", "fecha_fin": "2024-01-31", "dias_facturados": 31, '
        '"kwh": NaN}]'
    )

    response = await consumos_client.post(
        "/api/v1/consumos/import",
        content=payload_text,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 0
    assert any("NaN" in reason for reason in body["rejected"][0]["reasons"])


async def test_import_rejects_a_nan_consumo_promedio_diario_instead_of_500ing_the_batch(
    consumos_client: AsyncClient, consumo_fk_ids: tuple[str, str]
) -> None:
    """Mirrors `test_import_rejects_a_nan_kwh_instead_of_500ing_the_batch` exactly, for the other
    `numeric(12,3)` field validated the same way (`domain/consumo.py`'s `_parse_numeric_12_3`,
    shared by `kwh` and an explicit `consumo_promedio_diario`)."""
    numero_suministro, codigo_lote = consumo_fk_ids
    payload_text = (
        "["
        f'{{"numero_suministro": "{numero_suministro}", "codigo_lote": "{codigo_lote}", '
        '"fecha_inicio": "2024-01-01", "fecha_fin": "2024-01-31", "dias_facturados": 31, '
        '"kwh": 100.0, "consumo_promedio_diario": NaN}]'
    )

    response = await consumos_client.post(
        "/api/v1/consumos/import",
        content=payload_text,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 0
    assert any(
        "consumo_promedio_diario" in reason and "NaN" in reason
        for reason in body["rejected"][0]["reasons"]
    )


# -- consumo_promedio_diario: computed / preserved / null ----------------------------------------


async def test_omitted_consumo_promedio_diario_is_computed_and_returned_by_get(
    consumos_client: AsyncClient, consumo_fk_ids: tuple[str, str]
) -> None:
    numero_suministro, codigo_lote = consumo_fk_ids
    payload = [
        {
            "numero_suministro": numero_suministro,
            "codigo_lote": codigo_lote,
            "fecha_inicio": "2024-01-01",
            "fecha_fin": "2024-01-04",
            "dias_facturados": 4,
            "kwh": 100.0,
        }
    ]

    response = await consumos_client.post("/api/v1/consumos/import", json=payload)
    assert response.json()["created"] == 1

    listing = (await consumos_client.get("/api/v1/consumos")).json()
    matching = next(item for item in listing["items"] if item["fecha_inicio"] == "2024-01-01")
    assert matching["consumo_promedio_diario"] == "25.000"


async def test_reimporting_with_consumo_promedio_diario_omitted_recomputes_it(
    consumos_client: AsyncClient, consumo_fk_ids: tuple[str, str]
) -> None:
    """FIX 1: an omitted `consumo_promedio_diario` on re-import must **recompute** from the
    record's own `kwh`/`dias_facturados`, never preserve whatever average was stored before --
    see `application/import_consumos.py`'s module docstring."""
    numero_suministro, codigo_lote = consumo_fk_ids
    base = {
        "numero_suministro": numero_suministro,
        "codigo_lote": codigo_lote,
        "fecha_inicio": "2024-01-01",
        "fecha_fin": "2024-01-31",
        "dias_facturados": 31,
    }
    await consumos_client.post(
        "/api/v1/consumos/import", json=[{**base, "kwh": 100.0, "consumo_promedio_diario": 9.5}]
    )

    response = await consumos_client.post("/api/v1/consumos/import", json=[{**base, "kwh": 200.0}])

    assert response.json()["updated"] == 1
    listing = (await consumos_client.get("/api/v1/consumos")).json()
    matching = next(item for item in listing["items"] if item["fecha_inicio"] == "2024-01-01")
    # 200 / 31 = 6.451612... -> quantized to numeric(12,3).
    assert matching["consumo_promedio_diario"] == "6.452"
    assert matching["kwh"] == "200.000"


async def test_reimporting_reproduces_the_reviewer_scenario_kwh_100_to_295_recomputes_promedio(
    consumos_client: AsyncClient, consumo_fk_ids: tuple[str, str]
) -> None:
    """Exact reviewer-reported reproduction of the FIX 1 bug: `kwh` 100 -> 295 (same
    `dias_facturados`, 31), `consumo_promedio_diario` omitted on the re-import. Before the fix
    this returned 3.226 (the first import's stored average, frozen); it must now recompute to
    9.516 from the record's own (fresh) `kwh`/`dias_facturados`."""
    numero_suministro, codigo_lote = consumo_fk_ids
    base = {
        "numero_suministro": numero_suministro,
        "codigo_lote": codigo_lote,
        "fecha_inicio": "2024-01-01",
        "fecha_fin": "2024-01-31",
        "dias_facturados": 31,
    }
    await consumos_client.post("/api/v1/consumos/import", json=[{**base, "kwh": 100.0}])

    response = await consumos_client.post("/api/v1/consumos/import", json=[{**base, "kwh": 295.0}])

    assert response.json()["updated"] == 1
    listing = (await consumos_client.get("/api/v1/consumos")).json()
    matching = next(item for item in listing["items"] if item["fecha_inicio"] == "2024-01-01")
    assert matching["consumo_promedio_diario"] == "9.516"
    assert matching["kwh"] == "295.000"


async def test_reimporting_with_consumo_promedio_diario_explicitly_null_clears_it(
    consumos_client: AsyncClient, consumo_fk_ids: tuple[str, str]
) -> None:
    numero_suministro, codigo_lote = consumo_fk_ids
    base = {
        "numero_suministro": numero_suministro,
        "codigo_lote": codigo_lote,
        "fecha_inicio": "2024-01-01",
        "fecha_fin": "2024-01-31",
        "dias_facturados": 31,
        "kwh": 100.0,
    }
    await consumos_client.post(
        "/api/v1/consumos/import", json=[{**base, "consumo_promedio_diario": 9.5}]
    )

    response = await consumos_client.post(
        "/api/v1/consumos/import", json=[{**base, "consumo_promedio_diario": None}]
    )

    assert response.json()["updated"] == 1
    listing = (await consumos_client.get("/api/v1/consumos")).json()
    matching = next(item for item in listing["items"] if item["fecha_inicio"] == "2024-01-01")
    assert matching["consumo_promedio_diario"] is None


# -- fecha_lectura resolution ----------------------------------------------------------------------


async def test_import_resolves_fecha_lectura_to_a_lectura_id(
    consumos_client: AsyncClient, db_session: AsyncSession, consumo_fk_ids: tuple[str, str]
) -> None:
    numero_suministro, codigo_lote = consumo_fk_ids
    await consumos_client.post(
        "/api/v1/lecturas/import",
        json=[
            {
                "numero_suministro": numero_suministro,
                "fecha_lectura": "2024-01-31",
                "lectura_anterior": 0,
                "lectura_actual": 310,
                "dias_facturados": 31,
            }
        ],
    )

    response = await consumos_client.post(
        "/api/v1/consumos/import",
        json=[
            {
                "numero_suministro": numero_suministro,
                "codigo_lote": codigo_lote,
                "fecha_inicio": "2024-01-01",
                "fecha_fin": "2024-01-31",
                "dias_facturados": 31,
                "kwh": 310.0,
                "fecha_lectura": "2024-01-31",
            }
        ],
    )

    assert response.json()["created"] == 1
    listing = (await consumos_client.get("/api/v1/consumos")).json()
    matching = next(item for item in listing["items"] if item["fecha_inicio"] == "2024-01-01")
    assert matching["lectura_id"] is not None


async def test_import_rejects_a_record_whose_fecha_lectura_does_not_resolve(
    consumos_client: AsyncClient, consumo_fk_ids: tuple[str, str]
) -> None:
    numero_suministro, codigo_lote = consumo_fk_ids
    payload = [
        {
            "numero_suministro": numero_suministro,
            "codigo_lote": codigo_lote,
            "fecha_inicio": "2024-01-01",
            "fecha_fin": "2024-01-31",
            "dias_facturados": 31,
            "kwh": 100.0,
            "fecha_lectura": "2024-01-31",
        }
    ]

    response = await consumos_client.post("/api/v1/consumos/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 0
    assert any("lectura inexistente" in reason for reason in body["rejected"][0]["reasons"])


async def test_import_with_fecha_lectura_omitted_leaves_lectura_id_null(
    consumos_client: AsyncClient, consumo_fk_ids: tuple[str, str]
) -> None:
    numero_suministro, codigo_lote = consumo_fk_ids
    payload = [
        {
            "numero_suministro": numero_suministro,
            "codigo_lote": codigo_lote,
            "fecha_inicio": "2024-01-01",
            "fecha_fin": "2024-01-31",
            "dias_facturados": 31,
            "kwh": 100.0,
        }
    ]

    response = await consumos_client.post("/api/v1/consumos/import", json=payload)

    assert response.json()["created"] == 1
    listing = (await consumos_client.get("/api/v1/consumos")).json()
    assert listing["items"][0]["lectura_id"] is None


async def test_import_rejects_a_fecha_lectura_that_belongs_to_a_different_suministro(
    consumos_client: AsyncClient, db_session: AsyncSession, consumo_fk_ids: tuple[str, str]
) -> None:
    """`get_by_suministro_and_fecha` is scoped by `(suministro_id, fecha_lectura)` together --
    a lectura that exists for that exact date but for a *different* suministro must not resolve,
    mirroring `test_import_rejects_a_record_whose_fecha_lectura_does_not_resolve` but proving the
    suministro half of the composite key is actually enforced, not just the date."""
    numero_suministro, codigo_lote = consumo_fk_ids
    other_suministro_id = await insert_suministro(db_session, numero_suministro="CON-SUM-OTHER")
    assert isinstance(other_suministro_id, UUID)  # sanity: FK helper
    await consumos_client.post(
        "/api/v1/lecturas/import",
        json=[
            {
                "numero_suministro": "CON-SUM-OTHER",
                "fecha_lectura": "2024-01-31",
                "lectura_anterior": 0,
                "lectura_actual": 310,
                "dias_facturados": 31,
            }
        ],
    )

    response = await consumos_client.post(
        "/api/v1/consumos/import",
        json=[
            {
                "numero_suministro": numero_suministro,
                "codigo_lote": codigo_lote,
                "fecha_inicio": "2024-01-01",
                "fecha_fin": "2024-01-31",
                "dias_facturados": 31,
                "kwh": 310.0,
                "fecha_lectura": "2024-01-31",
            }
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 0
    assert any("lectura inexistente" in reason for reason in body["rejected"][0]["reasons"])


async def test_import_rejects_a_fecha_lectura_pointing_at_a_soft_deleted_lectura(
    consumos_client: AsyncClient, db_session: AsyncSession, consumo_fk_ids: tuple[str, str]
) -> None:
    """`get_by_suministro_and_fecha` filters `deleted_at IS NULL` (`infrastructure/
    lectura_repository.py`) -- a soft-deleted lectura for this exact `(suministro_id,
    fecha_lectura)` must not resolve, the same rejection reason as a lectura that never
    existed."""
    numero_suministro, codigo_lote = consumo_fk_ids
    await consumos_client.post(
        "/api/v1/lecturas/import",
        json=[
            {
                "numero_suministro": numero_suministro,
                "fecha_lectura": "2024-01-31",
                "lectura_anterior": 0,
                "lectura_actual": 310,
                "dias_facturados": 31,
            }
        ],
    )
    await db_session.execute(
        text("UPDATE lecturas SET deleted_at = now() WHERE fecha_lectura = '2024-01-31'")
    )
    await db_session.commit()

    response = await consumos_client.post(
        "/api/v1/consumos/import",
        json=[
            {
                "numero_suministro": numero_suministro,
                "codigo_lote": codigo_lote,
                "fecha_inicio": "2024-01-01",
                "fecha_fin": "2024-01-31",
                "dias_facturados": 31,
                "kwh": 310.0,
                "fecha_lectura": "2024-01-31",
            }
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 0
    assert any("lectura inexistente" in reason for reason in body["rejected"][0]["reasons"])


# -- idempotency / update / duplicate-within-payload ---------------------------------------------


async def test_reimporting_the_same_payload_is_idempotent(
    consumos_client: AsyncClient, consumo_fk_ids: tuple[str, str]
) -> None:
    numero_suministro, codigo_lote = consumo_fk_ids
    payload = [
        {
            "numero_suministro": numero_suministro,
            "codigo_lote": codigo_lote,
            "fecha_inicio": "2024-01-01",
            "fecha_fin": "2024-01-31",
            "dias_facturados": 31,
            "kwh": 100.0,
        }
    ]

    first = await consumos_client.post("/api/v1/consumos/import", json=payload)
    second = await consumos_client.post("/api/v1/consumos/import", json=payload)

    assert first.json()["created"] == 1
    second_body = second.json()
    assert second_body["created"] == 0
    assert second_body["updated"] == 0
    assert second_body["unchanged"] == 1


async def test_import_upserts_over_a_duplicate_key_within_the_same_payload(
    consumos_client: AsyncClient, consumo_fk_ids: tuple[str, str]
) -> None:
    numero_suministro, codigo_lote = consumo_fk_ids
    base = {
        "numero_suministro": numero_suministro,
        "codigo_lote": codigo_lote,
        "fecha_inicio": "2024-01-01",
        "fecha_fin": "2024-01-31",
        "dias_facturados": 31,
    }
    payload = [{**base, "kwh": 100.0}, {**base, "kwh": 200.0}]

    response = await consumos_client.post("/api/v1/consumos/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert body["updated"] == 1
    listing = (await consumos_client.get("/api/v1/consumos")).json()
    assert listing["items"][0]["kwh"] == "200.000"


# -- DECISION #9: soft-delete re-import resurrects the original row --------------------------


async def test_reimporting_a_soft_deleted_consumo_resurrects_the_original_row(
    consumos_client: AsyncClient, db_session: AsyncSession, consumo_fk_ids: tuple[str, str]
) -> None:
    """DECISION #9 (confirmed by business, 2026-07-13): same resurrection semantics as
    clientes/suministros/lecturas/lotes (`contexts/README.md`), now also true for `Consumo`
    thanks to debt #10 being paid: `uq_consumos_suministro_periodo` is a partial unique index
    (`WHERE deleted_at IS NULL`), which is exactly why the dead row can be found and revived
    without colliding with anything -- re-importing a soft-deleted period revives the original
    row (same `id`) instead of creating a brand-new identity."""
    numero_suministro, codigo_lote = consumo_fk_ids
    payload = [
        {
            "numero_suministro": numero_suministro,
            "codigo_lote": codigo_lote,
            "fecha_inicio": "2024-01-01",
            "fecha_fin": "2024-01-31",
            "dias_facturados": 31,
            "kwh": 100.0,
        }
    ]
    await consumos_client.post("/api/v1/consumos/import", json=payload)
    listing_before = (await consumos_client.get("/api/v1/consumos")).json()
    original_id = listing_before["items"][0]["id"]

    await db_session.execute(
        text("UPDATE consumos SET deleted_at = now() WHERE fecha_inicio = '2024-01-01'")
    )
    await db_session.commit()

    reimport_payload = [{**payload[0], "kwh": 620.0}]  # 620/31 = 20.000
    second = await consumos_client.post("/api/v1/consumos/import", json=reimport_payload)

    second_body = second.json()
    assert second_body["created"] == 0
    assert second_body["restored"] == 1
    listing_after = (await consumos_client.get("/api/v1/consumos")).json()
    resurrected = listing_after["items"][0]
    assert resurrected["id"] == original_id  # SAME identity, not a new row
    assert resurrected["kwh"] == "620.000"
    assert resurrected["consumo_promedio_diario"] == "20.000"


# -- partition routing ------------------------------------------------------------------------


async def test_a_fecha_inicio_outside_2022_2026_imports_successfully(
    consumos_client: AsyncClient, db_session: AsyncSession, consumo_fk_ids: tuple[str, str]
) -> None:
    numero_suministro, codigo_lote = consumo_fk_ids
    payload = [
        {
            "numero_suministro": numero_suministro,
            "codigo_lote": codigo_lote,
            "fecha_inicio": "2019-06-01",
            "fecha_fin": "2019-06-30",
            "dias_facturados": 29,
            "kwh": 50.0,
        }
    ]

    response = await consumos_client.post("/api/v1/consumos/import", json=payload)

    assert response.status_code == 200
    assert response.json()["created"] == 1
    result = await db_session.execute(
        text("SELECT tableoid::regclass::text FROM consumos WHERE fecha_inicio = '2019-06-01'")
    )
    assert result.scalar_one() == "consumos_default"


# -- GET filters and ordering ---------------------------------------------------------------------


async def test_get_consumos_filters_by_numero_suministro_and_codigo_lote(
    consumos_client: AsyncClient, db_session: AsyncSession
) -> None:
    suministro_a = await insert_suministro(db_session, numero_suministro="CON-FILT-A")
    suministro_b = await insert_suministro(db_session, numero_suministro="CON-FILT-B")
    lote_a = await insert_lote(db_session, codigo_lote="LOTE-FILT-A")
    lote_b = await insert_lote(db_session, codigo_lote="LOTE-FILT-B")
    assert isinstance(suministro_a, UUID) and isinstance(lote_a, UUID)  # sanity: FK helpers
    await consumos_client.post(
        "/api/v1/consumos/import",
        json=[
            {
                "numero_suministro": "CON-FILT-A",
                "codigo_lote": "LOTE-FILT-A",
                "fecha_inicio": "2024-01-01",
                "fecha_fin": "2024-01-31",
                "dias_facturados": 31,
                "kwh": 100.0,
            },
            {
                "numero_suministro": "CON-FILT-B",
                "codigo_lote": "LOTE-FILT-B",
                "fecha_inicio": "2024-01-01",
                "fecha_fin": "2024-01-31",
                "dias_facturados": 31,
                "kwh": 200.0,
            },
        ],
    )
    del suministro_b, lote_b

    by_suministro = await consumos_client.get(
        "/api/v1/consumos", params={"numero_suministro": "CON-FILT-A"}
    )
    by_lote = await consumos_client.get("/api/v1/consumos", params={"codigo_lote": "LOTE-FILT-B"})

    assert by_suministro.json()["total"] == 1
    assert by_suministro.json()["items"][0]["kwh"] == "100.000"
    assert by_lote.json()["total"] == 1
    assert by_lote.json()["items"][0]["kwh"] == "200.000"


async def test_get_consumos_with_a_numero_suministro_that_does_not_resolve_returns_an_empty_page(
    consumos_client: AsyncClient,
) -> None:
    response = await consumos_client.get(
        "/api/v1/consumos", params={"numero_suministro": "does-not-exist"}
    )

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


async def test_get_consumos_with_a_codigo_lote_that_does_not_resolve_returns_an_empty_page(
    consumos_client: AsyncClient,
) -> None:
    response = await consumos_client.get(
        "/api/v1/consumos", params={"codigo_lote": "does-not-exist"}
    )

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


async def test_get_consumos_orders_by_fecha_inicio_desc(
    consumos_client: AsyncClient, consumo_fk_ids: tuple[str, str]
) -> None:
    numero_suministro, codigo_lote = consumo_fk_ids
    base = {
        "numero_suministro": numero_suministro,
        "codigo_lote": codigo_lote,
        "dias_facturados": 31,
        "kwh": 100.0,
    }
    await consumos_client.post(
        "/api/v1/consumos/import",
        json=[
            {**base, "fecha_inicio": "2024-01-01", "fecha_fin": "2024-01-31"},
            {**base, "fecha_inicio": "2024-03-01", "fecha_fin": "2024-03-31"},
            {**base, "fecha_inicio": "2024-02-01", "fecha_fin": "2024-02-29"},
        ],
    )

    response = await consumos_client.get("/api/v1/consumos")

    body = response.json()
    fechas = [item["fecha_inicio"] for item in body["items"]]
    assert fechas == ["2024-03-01", "2024-02-01", "2024-01-01"]


async def test_get_consumos_honors_limit_and_offset(
    consumos_client: AsyncClient, consumo_fk_ids: tuple[str, str]
) -> None:
    numero_suministro, codigo_lote = consumo_fk_ids
    await consumos_client.post(
        "/api/v1/consumos/import",
        json=[
            {
                "numero_suministro": numero_suministro,
                "codigo_lote": codigo_lote,
                "fecha_inicio": f"2024-0{i}-01",
                "fecha_fin": f"2024-0{i}-28",
                "dias_facturados": 27,
                "kwh": 100.0,
            }
            for i in range(1, 6)
        ],
    )

    response = await consumos_client.get("/api/v1/consumos", params={"limit": 2, "offset": 1})

    body = response.json()
    assert body["limit"] == 2
    assert body["offset"] == 1
    assert len(body["items"]) == 2
    assert body["total"] == 5


# -- DECISION #13: DELETE /api/v1/consumos/{id} -- soft-delete correction path -----------------


async def test_delete_consumo_returns_204_soft_deletes_and_get_excludes_it(
    consumos_client: AsyncClient, db_session: AsyncSession, consumo_fk_ids: tuple[str, str]
) -> None:
    numero_suministro, codigo_lote = consumo_fk_ids
    await consumos_client.post(
        "/api/v1/consumos/import",
        json=[
            {
                "numero_suministro": numero_suministro,
                "codigo_lote": codigo_lote,
                "fecha_inicio": "2024-01-01",
                "fecha_fin": "2024-01-31",
                "dias_facturados": 31,
                "kwh": 100.0,
            }
        ],
    )
    listing = (await consumos_client.get("/api/v1/consumos")).json()
    consumo_id = listing["items"][0]["id"]

    response = await consumos_client.delete(f"/api/v1/consumos/{consumo_id}")

    assert response.status_code == 204
    row = (
        await db_session.execute(
            text("SELECT deleted_at FROM consumos WHERE id = :id"), {"id": consumo_id}
        )
    ).one()
    assert row.deleted_at is not None
    listing_after = (await consumos_client.get("/api/v1/consumos")).json()
    assert listing_after["total"] == 0


async def test_delete_consumo_twice_returns_404_the_second_time(
    consumos_client: AsyncClient, consumo_fk_ids: tuple[str, str]
) -> None:
    numero_suministro, codigo_lote = consumo_fk_ids
    await consumos_client.post(
        "/api/v1/consumos/import",
        json=[
            {
                "numero_suministro": numero_suministro,
                "codigo_lote": codigo_lote,
                "fecha_inicio": "2024-02-01",
                "fecha_fin": "2024-02-29",
                "dias_facturados": 29,
                "kwh": 50.0,
            }
        ],
    )
    listing = (await consumos_client.get("/api/v1/consumos")).json()
    consumo_id = listing["items"][0]["id"]
    first = await consumos_client.delete(f"/api/v1/consumos/{consumo_id}")
    assert first.status_code == 204

    second = await consumos_client.delete(f"/api/v1/consumos/{consumo_id}")

    assert second.status_code == 404


async def test_delete_consumo_with_an_unknown_id_returns_404(
    consumos_client: AsyncClient,
) -> None:
    response = await consumos_client.delete(f"/api/v1/consumos/{uuid4()}")

    assert response.status_code == 404


async def test_delete_then_reimporting_the_same_period_resurrects_the_same_consumo(
    consumos_client: AsyncClient, consumo_fk_ids: tuple[str, str]
) -> None:
    """DECISION #13's interplay with DECISION #9: DELETE is the correction path for a wrongly
    imported period, and re-importing the corrected data lands back on the SAME identity
    (`restored: 1`), not a duplicate -- closing the correction loop end-to-end."""
    numero_suministro, codigo_lote = consumo_fk_ids
    payload = [
        {
            "numero_suministro": numero_suministro,
            "codigo_lote": codigo_lote,
            "fecha_inicio": "2024-03-01",
            "fecha_fin": "2024-03-31",
            "dias_facturados": 31,
            "kwh": 999.0,  # the "wrong" value that motivates the correction
        }
    ]
    await consumos_client.post("/api/v1/consumos/import", json=payload)
    listing = (await consumos_client.get("/api/v1/consumos")).json()
    original_id = listing["items"][0]["id"]

    delete_response = await consumos_client.delete(f"/api/v1/consumos/{original_id}")
    assert delete_response.status_code == 204

    corrected_payload = [{**payload[0], "kwh": 310.0}]
    reimport = await consumos_client.post("/api/v1/consumos/import", json=corrected_payload)

    reimport_body = reimport.json()
    assert reimport_body["created"] == 0
    assert reimport_body["restored"] == 1
    listing_after = (await consumos_client.get("/api/v1/consumos")).json()
    resurrected = listing_after["items"][0]
    assert resurrected["id"] == original_id
    assert resurrected["kwh"] == "310.000"
