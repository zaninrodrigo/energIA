"""Integration tests for the `clientes` HTTP API against the real `energia_test` database.

Covers the acceptance path for US-001: import from a JSON payload (creating and rejecting
records individually), idempotent re-import, and the paginated list endpoint. Never touches
`energia` — see tests/integration/conftest.py for the isolation strategy.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def test_import_creates_valid_records_and_rejects_invalid_ones_individually(
    clientes_client: AsyncClient,
) -> None:
    payload = [
        {"numero_cliente": "1001", "nombre": "Ana Gomez"},
        {"numero_cliente": "1002", "nombre": "Bruno Diaz"},
        {"numero_cliente": "1003", "nombre": "Carla Ruiz"},
        {"numero_cliente": "", "nombre": "Invalido"},  # missing numero_cliente -> rejected
    ]

    response = await clientes_client.post("/api/v1/clientes/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 3
    assert body["updated"] == 0
    assert body["unchanged"] == 0
    assert len(body["rejected"]) == 1
    assert any("numero_cliente" in reason for reason in body["rejected"][0]["reasons"])


async def test_reimporting_the_same_payload_is_idempotent(clientes_client: AsyncClient) -> None:
    payload = [
        {"numero_cliente": "2001", "nombre": "Ana Gomez"},
        {"numero_cliente": "2002", "nombre": "Bruno Diaz"},
        {"numero_cliente": "2003", "nombre": "Carla Ruiz"},
    ]

    first = await clientes_client.post("/api/v1/clientes/import", json=payload)
    second = await clientes_client.post("/api/v1/clientes/import", json=payload)

    assert first.json()["created"] == 3
    second_body = second.json()
    assert second_body["created"] == 0
    assert second_body["updated"] == 0
    assert second_body["unchanged"] == 3
    assert second_body["rejected"] == []


async def test_reimporting_with_changed_data_reports_updated(clientes_client: AsyncClient) -> None:
    await clientes_client.post(
        "/api/v1/clientes/import",
        json=[{"numero_cliente": "3001", "nombre": "Original Name"}],
    )

    response = await clientes_client.post(
        "/api/v1/clientes/import",
        json=[{"numero_cliente": "3001", "nombre": "Changed Name"}],
    )

    body = response.json()
    assert body["created"] == 0
    assert body["updated"] == 1
    assert body["unchanged"] == 0


async def test_import_returns_422_for_a_malformed_body(clientes_client: AsyncClient) -> None:
    """A body that isn't even a JSON array fails FastAPI/Pydantic validation before the
    use case runs at all — distinct from a domain-invalid record (missing numero_cliente),
    which is a per-record rejection reported with HTTP 200 (see the test above)."""
    response = await clientes_client.post("/api/v1/clientes/import", json={"not": "a list"})

    assert response.status_code == 422


async def test_import_rejects_a_type_broken_record_individually_instead_of_422ing_the_batch(
    clientes_client: AsyncClient,
) -> None:
    """FIX 3: one record with a wrong field type (`numero_cliente` as a number, not a string)
    no longer 422s the whole request — every record is bound to the DTO individually, so a
    structurally broken record is rejected on its own and every valid record still goes
    through, alongside a domain-invalid one, in the same 200 response."""
    payload = [
        {"numero_cliente": "7001", "nombre": "Valid Client"},
        {"numero_cliente": 7002, "nombre": "Type Broken numero_cliente"},
        {"numero_cliente": "", "nombre": "Domain Invalid: blank numero_cliente"},
        {
            "numero_cliente": "7003",
            "nombre": "Domain Invalid: oversized direccion",
            "direccion": {"calle": "x" * 9000},
        },
    ]

    response = await clientes_client.post("/api/v1/clientes/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert body["updated"] == 0
    assert body["unchanged"] == 0
    assert len(body["rejected"]) == 3
    reasons_by_record = {
        str(rejected["record"].get("numero_cliente")): rejected["reasons"]
        for rejected in body["rejected"]
    }
    assert any("numero_cliente" in reason for reason in reasons_by_record["7002"])
    assert any("numero_cliente" in reason for reason in reasons_by_record[""])
    assert any("direccion" in reason for reason in reasons_by_record["7003"])


async def test_import_upserts_over_a_duplicate_numero_cliente_within_the_same_request(
    clientes_client: AsyncClient,
) -> None:
    """FIX 2: two records sharing the same `numero_cliente` in one payload are processed
    sequentially; the second occurrence upserts over the first (ends as `updated`), not as an
    integrity conflict."""
    payload = [
        {"numero_cliente": "7101", "nombre": "First Occurrence"},
        {"numero_cliente": "7101", "nombre": "Second Occurrence"},
    ]

    response = await clientes_client.post("/api/v1/clientes/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert body["updated"] == 1
    assert body["rejected"] == []

    listing = await clientes_client.get("/api/v1/clientes")
    matching = [c for c in listing.json()["items"] if c["numero_cliente"] == "7101"]
    assert len(matching) == 1
    assert matching[0]["nombre"] == "Second Occurrence"


async def test_get_clientes_lists_only_the_imported_clients(clientes_client: AsyncClient) -> None:
    payload = [
        {"numero_cliente": "5001", "nombre": "Ana Gomez"},
        {"numero_cliente": "5002", "nombre": "Bruno Diaz"},
        {"numero_cliente": "5003", "nombre": "Carla Ruiz"},
    ]
    await clientes_client.post("/api/v1/clientes/import", json=payload)

    response = await clientes_client.get("/api/v1/clientes")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert {item["numero_cliente"] for item in body["items"]} == {"5001", "5002", "5003"}


async def test_get_clientes_honors_limit_and_offset(clientes_client: AsyncClient) -> None:
    payload = [{"numero_cliente": str(6000 + i), "nombre": f"Cliente {i}"} for i in range(5)]
    await clientes_client.post("/api/v1/clientes/import", json=payload)

    response = await clientes_client.get("/api/v1/clientes", params={"limit": 2, "offset": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 2
    assert body["offset"] == 1
    assert len(body["items"]) == 2
    assert body["total"] == 5


async def test_reimporting_a_soft_deleted_numero_cliente_creates_a_new_identity(
    clientes_client: AsyncClient, db_session: AsyncSession
) -> None:
    """FIX 4: documents the CURRENT, deliberate semantics for re-importing a soft-deleted
    `numero_cliente` -- this is NOT a bug, it is today's design, captured here so a future
    change to it is deliberate, not accidental.

    `uq_clientes_numero_cliente` is a *partial* unique index (`WHERE deleted_at IS NULL`), so
    soft-deleting a client does not reserve its `numero_cliente` forever: re-importing it
    creates a brand-new row with a NEW id -- it does not "resurrect" the original row. Any
    historical FK (e.g. `suministros.cliente_id`) pointing at the old id keeps pointing at the
    old (still soft-deleted) row, not at the new one. See `contexts/README.md` ("Comportamiento
    ante soft-delete") and `PROJECT_MASTER_SPEC.md`'s debt list for the open business question
    this raises.
    """
    await clientes_client.post(
        "/api/v1/clientes/import", json=[{"numero_cliente": "8001", "nombre": "Original"}]
    )
    listing_before = (await clientes_client.get("/api/v1/clientes")).json()
    original_id = next(c["id"] for c in listing_before["items"] if c["numero_cliente"] == "8001")

    await db_session.execute(
        text("UPDATE clientes SET deleted_at = now() WHERE numero_cliente = '8001'")
    )
    await db_session.commit()

    second = await clientes_client.post(
        "/api/v1/clientes/import", json=[{"numero_cliente": "8001", "nombre": "Reimported"}]
    )

    assert second.json()["created"] == 1  # a brand-new row, not an update of the soft-deleted one
    listing_after = (await clientes_client.get("/api/v1/clientes")).json()
    new_id = next(c["id"] for c in listing_after["items"] if c["numero_cliente"] == "8001")
    assert new_id != original_id
