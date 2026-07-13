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


async def test_import_rejects_a_typo_field_instead_of_silently_dropping_it(
    clientes_client: AsyncClient,
) -> None:
    """A typo'd key (e.g. `numero_clientee` instead of `numero_cliente`) used to be dropped
    silently by Pydantic's default `extra="ignore"` -- the record then failed only as a *domain*
    rejection (missing `numero_cliente`), with no hint the key was ever misspelled. `extra="forbid"`
    (DECISION #11, aligning `ClienteImportItem` with `LoteImportItem`/`ConsumoImportItem`) rejects
    it structurally instead, naming the offending key. The rest of the batch still goes through."""
    payload = [
        {"numero_cliente": "9001", "nombre": "Valid Client"},
        {"numero_clientee": "9002", "nombre": "Typo Field"},
    ]

    response = await clientes_client.post("/api/v1/clientes/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert len(body["rejected"]) == 1
    assert any("numero_clientee" in reason for reason in body["rejected"][0]["reasons"])


async def test_import_rejects_a_previously_silently_ignored_extra_field(
    clientes_client: AsyncClient,
) -> None:
    """Before DECISION #11, a plausible-looking but undeclared field (e.g. `telefono`) was
    silently dropped by `extra="ignore"` -- the record was created anyway, with no trace the
    field was ever sent. `extra="forbid"` closes that silent-drop path: the record is now
    rejected structurally, naming the offending key."""
    payload = [{"numero_cliente": "9101", "nombre": "Ana Gomez", "telefono": "555-1234"}]

    response = await clientes_client.post("/api/v1/clientes/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 0
    assert len(body["rejected"]) == 1
    assert any("telefono" in reason for reason in body["rejected"][0]["reasons"])


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


async def test_reimporting_a_soft_deleted_numero_cliente_resurrects_the_original_row(
    clientes_client: AsyncClient, db_session: AsyncSession
) -> None:
    """DECISION #9 (confirmed by business, 2026-07-13 -- see `PROJECT_MASTER_SPEC.md`, resolved
    items): re-importing a soft-deleted `numero_cliente` RESURRECTS the original row (same `id`,
    `deleted_at` cleared, fields merged from the re-import), instead of creating a brand-new
    identity. `uq_clientes_numero_cliente` is a *partial* unique index (`WHERE deleted_at IS
    NULL`), which is exactly why a dead row can be found and revived by natural key without
    colliding with anything -- see `contexts/README.md` ("Comportamiento ante soft-delete") for
    the full rationale.
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

    second_body = second.json()
    assert second_body["created"] == 0
    assert second_body["restored"] == 1
    listing_after = (await clientes_client.get("/api/v1/clientes")).json()
    resurrected = next(c for c in listing_after["items"] if c["numero_cliente"] == "8001")
    assert resurrected["id"] == original_id  # SAME identity, not a new row
    assert resurrected["nombre"] == "Reimported"

    row = (
        await db_session.execute(
            text("SELECT deleted_at FROM clientes WHERE numero_cliente = '8001'")
        )
    ).one()
    assert row.deleted_at is None


async def test_resurrecting_the_most_recently_deleted_of_multiple_dead_rows(
    clientes_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Two independently dead rows can share the same `numero_cliente` (e.g. one soft-deleted,
    then a direct-SQL insert creates a fresh active row reusing the natural key, which is later
    soft-deleted too -- the partial unique index only ever forbids two *active* rows). Only the
    most recently deleted one is a resurrection candidate; the older dead row stays dead."""
    await clientes_client.post(
        "/api/v1/clientes/import", json=[{"numero_cliente": "8002", "nombre": "First"}]
    )
    await db_session.execute(
        text(
            "UPDATE clientes SET deleted_at = now() - interval '2 hours' "
            "WHERE numero_cliente = '8002'"
        )
    )
    await db_session.execute(
        text(
            "INSERT INTO clientes (id, numero_cliente, nombre, deleted_at) "
            "VALUES (gen_random_uuid(), '8002', 'Second', now() - interval '1 hour')"
        )
    )
    await db_session.commit()
    older_dead_id, newer_dead_id = (
        (
            await db_session.execute(
                text(
                    "SELECT id FROM clientes WHERE numero_cliente = '8002' ORDER BY deleted_at ASC"
                )
            )
        )
        .scalars()
        .all()
    )

    response = await clientes_client.post(
        "/api/v1/clientes/import", json=[{"numero_cliente": "8002", "nombre": "Resurrected"}]
    )

    assert response.json()["restored"] == 1
    listing = (await clientes_client.get("/api/v1/clientes")).json()
    resurrected = next(c for c in listing["items"] if c["numero_cliente"] == "8002")
    assert resurrected["id"] == str(newer_dead_id)
    assert resurrected["id"] != str(older_dead_id)
    still_dead = (
        await db_session.execute(
            text("SELECT deleted_at FROM clientes WHERE id = :id"), {"id": older_dead_id}
        )
    ).one()
    assert still_dead.deleted_at is not None


async def test_reimporting_a_resurrected_record_identically_reports_unchanged(
    clientes_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Once resurrected, the row is active again: a third, identical import finds it through the
    ordinary active-row lookup, not the dead-row path -- reported as `unchanged`, not `restored`
    a second time."""
    await clientes_client.post(
        "/api/v1/clientes/import", json=[{"numero_cliente": "8003", "nombre": "Original"}]
    )
    await db_session.execute(
        text("UPDATE clientes SET deleted_at = now() WHERE numero_cliente = '8003'")
    )
    await db_session.commit()
    first_resurrection = await clientes_client.post(
        "/api/v1/clientes/import", json=[{"numero_cliente": "8003", "nombre": "Original"}]
    )
    assert first_resurrection.json()["restored"] == 1

    third = await clientes_client.post(
        "/api/v1/clientes/import", json=[{"numero_cliente": "8003", "nombre": "Original"}]
    )

    third_body = third.json()
    assert third_body["created"] == 0
    assert third_body["updated"] == 0
    assert third_body["restored"] == 0
    assert third_body["unchanged"] == 1
