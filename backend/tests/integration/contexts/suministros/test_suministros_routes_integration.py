"""Integration tests for the `suministros` HTTP API against the real `energia_test` database.

Covers the acceptance path for US-002: import from a JSON payload (creating and rejecting
records individually, including nonexistent cliente/categoria references), idempotent
re-import, and the paginated list endpoint (with the `numero_cliente` filter). Seeds clientes
through `POST /api/v1/clientes/import` (not a direct DB insert) wherever it can, proving the two
vertical slices actually compose on the same app. Never touches `energia` -- see
tests/integration/conftest.py for the isolation strategy.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _seed_cliente(client: AsyncClient, numero_cliente: str) -> None:
    response = await client.post(
        "/api/v1/clientes/import",
        json=[{"numero_cliente": numero_cliente, "nombre": f"Cliente {numero_cliente}"}],
    )
    assert response.status_code == 200
    assert response.json()["created"] == 1


async def test_import_creates_valid_records_and_rejects_invalid_ones_individually(
    suministros_client: AsyncClient,
) -> None:
    await _seed_cliente(suministros_client, "1001")
    payload = [
        {
            "numero_suministro": "SUM-1",
            "numero_cliente": "1001",
            "categoria_tarifaria": "Residencial",
            "fecha_alta": "2024-01-15",
        },
        {
            "numero_suministro": "SUM-2",
            "numero_cliente": "1001",
            "categoria_tarifaria": "Comercial",
            "fecha_alta": "2024-02-01",
        },
        {
            "numero_suministro": "SUM-3",
            "numero_cliente": "does-not-exist",
            "categoria_tarifaria": "Residencial",
            "fecha_alta": "2024-02-01",
        },
        {
            "numero_suministro": "SUM-4",
            "numero_cliente": "1001",
            "categoria_tarifaria": "does-not-exist",
            "fecha_alta": "2024-02-01",
        },
    ]

    response = await suministros_client.post("/api/v1/suministros/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 2
    assert body["updated"] == 0
    assert body["unchanged"] == 0
    assert len(body["rejected"]) == 2
    reasons_by_numero = {
        rejected["record"].get("numero_suministro"): rejected["reasons"]
        for rejected in body["rejected"]
    }
    assert any("cliente inexistente" in reason for reason in reasons_by_numero["SUM-3"])
    assert any("categoria tarifaria inexistente" in reason for reason in reasons_by_numero["SUM-4"])


async def test_reimporting_the_same_payload_is_idempotent(suministros_client: AsyncClient) -> None:
    await _seed_cliente(suministros_client, "2001")
    payload = [
        {
            "numero_suministro": "SUM-2001",
            "numero_cliente": "2001",
            "categoria_tarifaria": "Residencial",
            "fecha_alta": "2024-01-01",
        }
    ]

    first = await suministros_client.post("/api/v1/suministros/import", json=payload)
    second = await suministros_client.post("/api/v1/suministros/import", json=payload)

    assert first.json()["created"] == 1
    second_body = second.json()
    assert second_body["created"] == 0
    assert second_body["updated"] == 0
    assert second_body["unchanged"] == 1
    assert second_body["rejected"] == []


async def test_reimporting_with_changed_data_reports_updated(
    suministros_client: AsyncClient,
) -> None:
    await _seed_cliente(suministros_client, "3001")
    await suministros_client.post(
        "/api/v1/suministros/import",
        json=[
            {
                "numero_suministro": "SUM-3001",
                "numero_cliente": "3001",
                "categoria_tarifaria": "Residencial",
                "fecha_alta": "2024-01-01",
                "localidad": "Original",
            }
        ],
    )

    response = await suministros_client.post(
        "/api/v1/suministros/import",
        json=[
            {
                "numero_suministro": "SUM-3001",
                "numero_cliente": "3001",
                "categoria_tarifaria": "Residencial",
                "fecha_alta": "2024-01-01",
                "localidad": "Changed",
            }
        ],
    )

    body = response.json()
    assert body["created"] == 0
    assert body["updated"] == 1
    assert body["unchanged"] == 0


async def test_import_returns_422_for_a_malformed_body(suministros_client: AsyncClient) -> None:
    response = await suministros_client.post("/api/v1/suministros/import", json={"not": "a list"})

    assert response.status_code == 422


async def test_import_rejects_a_type_broken_record_individually_instead_of_422ing_the_batch(
    suministros_client: AsyncClient,
) -> None:
    await _seed_cliente(suministros_client, "7001")
    payload = [
        {
            "numero_suministro": "SUM-7001",
            "numero_cliente": "7001",
            "categoria_tarifaria": "Residencial",
            "fecha_alta": "2024-01-01",
        },
        {
            "numero_suministro": 7002,  # type-broken: should be a string
            "numero_cliente": "7001",
            "categoria_tarifaria": "Residencial",
            "fecha_alta": "2024-01-01",
        },
        {
            "numero_suministro": "SUM-7003",
            "numero_cliente": "7001",
            "categoria_tarifaria": "Residencial",
            "fecha_alta": "not-a-date",
        },
    ]

    response = await suministros_client.post("/api/v1/suministros/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert len(body["rejected"]) == 2
    reasons_by_record = {
        str(rejected["record"].get("numero_suministro")): rejected["reasons"]
        for rejected in body["rejected"]
    }
    assert any("numero_suministro" in reason for reason in reasons_by_record["7002"])
    assert any("fecha_alta" in reason for reason in reasons_by_record["SUM-7003"])


async def test_import_upserts_over_a_duplicate_numero_suministro_within_the_same_request(
    suministros_client: AsyncClient,
) -> None:
    await _seed_cliente(suministros_client, "7101")
    payload = [
        {
            "numero_suministro": "SUM-7101",
            "numero_cliente": "7101",
            "categoria_tarifaria": "Residencial",
            "fecha_alta": "2024-01-01",
            "localidad": "First",
        },
        {
            "numero_suministro": "SUM-7101",
            "numero_cliente": "7101",
            "categoria_tarifaria": "Residencial",
            "fecha_alta": "2024-01-01",
            "localidad": "Second",
        },
    ]

    response = await suministros_client.post("/api/v1/suministros/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert body["updated"] == 1
    assert body["rejected"] == []

    listing = await suministros_client.get("/api/v1/suministros")
    matching = [s for s in listing.json()["items"] if s["numero_suministro"] == "SUM-7101"]
    assert len(matching) == 1
    assert matching[0]["localidad"] == "Second"


async def test_get_suministros_lists_only_the_imported_suministros(
    suministros_client: AsyncClient,
) -> None:
    await _seed_cliente(suministros_client, "5001")
    payload = [
        {
            "numero_suministro": "SUM-5001",
            "numero_cliente": "5001",
            "categoria_tarifaria": "Residencial",
            "fecha_alta": "2024-01-01",
        },
        {
            "numero_suministro": "SUM-5002",
            "numero_cliente": "5001",
            "categoria_tarifaria": "Comercial",
            "fecha_alta": "2024-01-01",
        },
    ]
    await suministros_client.post("/api/v1/suministros/import", json=payload)

    response = await suministros_client.get("/api/v1/suministros")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {item["numero_suministro"] for item in body["items"]} == {"SUM-5001", "SUM-5002"}


async def test_get_suministros_honors_limit_and_offset(suministros_client: AsyncClient) -> None:
    await _seed_cliente(suministros_client, "6001")
    payload = [
        {
            "numero_suministro": f"SUM-6{i}",
            "numero_cliente": "6001",
            "categoria_tarifaria": "Residencial",
            "fecha_alta": "2024-01-01",
        }
        for i in range(5)
    ]
    await suministros_client.post("/api/v1/suministros/import", json=payload)

    response = await suministros_client.get("/api/v1/suministros", params={"limit": 2, "offset": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 2
    assert body["offset"] == 1
    assert len(body["items"]) == 2
    assert body["total"] == 5


async def test_get_suministros_filters_by_numero_cliente(suministros_client: AsyncClient) -> None:
    await _seed_cliente(suministros_client, "8001")
    await _seed_cliente(suministros_client, "8002")
    await suministros_client.post(
        "/api/v1/suministros/import",
        json=[
            {
                "numero_suministro": "SUM-8001",
                "numero_cliente": "8001",
                "categoria_tarifaria": "Residencial",
                "fecha_alta": "2024-01-01",
            },
            {
                "numero_suministro": "SUM-8002",
                "numero_cliente": "8002",
                "categoria_tarifaria": "Residencial",
                "fecha_alta": "2024-01-01",
            },
        ],
    )

    response = await suministros_client.get(
        "/api/v1/suministros", params={"numero_cliente": "8001"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["numero_suministro"] == "SUM-8001"


async def test_get_suministros_filtered_by_a_nonexistent_numero_cliente_returns_an_empty_page(
    suministros_client: AsyncClient,
) -> None:
    response = await suministros_client.get(
        "/api/v1/suministros", params={"numero_cliente": "does-not-exist"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []


async def test_reimporting_a_soft_deleted_numero_suministro_creates_a_new_identity(
    suministros_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Same current, deliberate semantics as clientes (see `contexts/README.md`,
    "Comportamiento ante soft-delete"): re-importing a soft-deleted `numero_suministro` creates a
    brand-new row with a new id, it does not resurrect the original row -- because
    `uq_suministros_numero_suministro` is a partial unique index (`WHERE deleted_at IS NULL`).
    """
    await _seed_cliente(suministros_client, "9001")
    await suministros_client.post(
        "/api/v1/suministros/import",
        json=[
            {
                "numero_suministro": "SUM-9001",
                "numero_cliente": "9001",
                "categoria_tarifaria": "Residencial",
                "fecha_alta": "2024-01-01",
            }
        ],
    )
    listing_before = (await suministros_client.get("/api/v1/suministros")).json()
    original_id = next(
        s["id"] for s in listing_before["items"] if s["numero_suministro"] == "SUM-9001"
    )

    await db_session.execute(
        text("UPDATE suministros SET deleted_at = now() WHERE numero_suministro = 'SUM-9001'")
    )
    await db_session.commit()

    second = await suministros_client.post(
        "/api/v1/suministros/import",
        json=[
            {
                "numero_suministro": "SUM-9001",
                "numero_cliente": "9001",
                "categoria_tarifaria": "Residencial",
                "fecha_alta": "2024-01-01",
            }
        ],
    )

    assert second.json()["created"] == 1
    listing_after = (await suministros_client.get("/api/v1/suministros")).json()
    new_id = next(s["id"] for s in listing_after["items"] if s["numero_suministro"] == "SUM-9001")
    assert new_id != original_id
