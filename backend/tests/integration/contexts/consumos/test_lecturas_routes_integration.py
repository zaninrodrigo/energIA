"""Integration tests for the `lecturas` HTTP API against the real `energia_test` database.

Covers the acceptance path for US-003: import from a JSON payload (creating and rejecting
records individually, including a nonexistent suministro reference and a RD-013 violation),
idempotent re-import, and the paginated list endpoint (with the `numero_suministro` filter).
Seeds a cliente and a suministro through `POST /api/v1/clientes/import` and
`POST /api/v1/suministros/import` (not a direct DB insert) wherever it can, proving all three
vertical slices actually compose on the same app. Never touches `energia` -- see
tests/integration/conftest.py for the isolation strategy.
"""

import json

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


async def _seed_suministro(
    client: AsyncClient, *, numero_suministro: str, numero_cliente: str
) -> None:
    response = await client.post(
        "/api/v1/suministros/import",
        json=[
            {
                "numero_suministro": numero_suministro,
                "numero_cliente": numero_cliente,
                "categoria_tarifaria": "Residencial",
                "fecha_alta": "2024-01-01",
            }
        ],
    )
    assert response.status_code == 200
    assert response.json()["created"] == 1


async def test_import_creates_valid_records_and_rejects_invalid_ones_individually(
    lecturas_client: AsyncClient,
) -> None:
    await _seed_cliente(lecturas_client, "1001")
    await _seed_suministro(lecturas_client, numero_suministro="SUM-1001", numero_cliente="1001")
    payload = [
        {
            "numero_suministro": "SUM-1001",
            "fecha_lectura": "2024-01-15",
            "lectura_anterior": 100.0,
            "lectura_actual": 150.5,
            "dias_facturados": 30,
        },
        {
            "numero_suministro": "SUM-1001",
            "fecha_lectura": "2024-02-15",
            "lectura_anterior": 150.5,
            "lectura_actual": 200.0,
            "dias_facturados": 31,
        },
        {
            "numero_suministro": "does-not-exist",
            "fecha_lectura": "2024-01-15",
            "lectura_anterior": 0.0,
            "lectura_actual": 10.0,
            "dias_facturados": 30,
        },
        {
            "numero_suministro": "SUM-1001",
            "fecha_lectura": "2024-03-15",
            "lectura_anterior": 200.0,
            "lectura_actual": 100.0,  # RD-013 violation: actual < anterior
            "dias_facturados": 30,
        },
    ]

    response = await lecturas_client.post("/api/v1/lecturas/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 2
    assert body["updated"] == 0
    assert body["unchanged"] == 0
    assert len(body["rejected"]) == 2
    reasons_by_fecha = {
        rejected["record"].get("fecha_lectura"): rejected["reasons"]
        for rejected in body["rejected"]
    }
    assert any("suministro inexistente" in reason for reason in reasons_by_fecha["2024-01-15"])
    assert any("lectura_actual" in reason for reason in reasons_by_fecha["2024-03-15"])


async def test_reimporting_the_same_payload_is_idempotent(lecturas_client: AsyncClient) -> None:
    await _seed_cliente(lecturas_client, "2001")
    await _seed_suministro(lecturas_client, numero_suministro="SUM-2001", numero_cliente="2001")
    payload = [
        {
            "numero_suministro": "SUM-2001",
            "fecha_lectura": "2024-01-01",
            "lectura_anterior": 0.0,
            "lectura_actual": 100.0,
            "dias_facturados": 30,
        }
    ]

    first = await lecturas_client.post("/api/v1/lecturas/import", json=payload)
    second = await lecturas_client.post("/api/v1/lecturas/import", json=payload)

    assert first.json()["created"] == 1
    second_body = second.json()
    assert second_body["created"] == 0
    assert second_body["updated"] == 0
    assert second_body["unchanged"] == 1
    assert second_body["rejected"] == []


async def test_reimporting_with_changed_data_reports_updated(lecturas_client: AsyncClient) -> None:
    await _seed_cliente(lecturas_client, "3001")
    await _seed_suministro(lecturas_client, numero_suministro="SUM-3001", numero_cliente="3001")
    await lecturas_client.post(
        "/api/v1/lecturas/import",
        json=[
            {
                "numero_suministro": "SUM-3001",
                "fecha_lectura": "2024-01-01",
                "lectura_anterior": 0.0,
                "lectura_actual": 100.0,
                "dias_facturados": 30,
            }
        ],
    )

    response = await lecturas_client.post(
        "/api/v1/lecturas/import",
        json=[
            {
                "numero_suministro": "SUM-3001",
                "fecha_lectura": "2024-01-01",
                "lectura_anterior": 0.0,
                "lectura_actual": 120.0,
                "dias_facturados": 30,
            }
        ],
    )

    body = response.json()
    assert body["created"] == 0
    assert body["updated"] == 1
    assert body["unchanged"] == 0


async def test_import_returns_422_for_a_malformed_body(lecturas_client: AsyncClient) -> None:
    response = await lecturas_client.post("/api/v1/lecturas/import", json={"not": "a list"})

    assert response.status_code == 422


async def test_import_rejects_a_type_broken_record_individually_instead_of_422ing_the_batch(
    lecturas_client: AsyncClient,
) -> None:
    await _seed_cliente(lecturas_client, "7001")
    await _seed_suministro(lecturas_client, numero_suministro="SUM-7001", numero_cliente="7001")
    payload = [
        {
            "numero_suministro": "SUM-7001",
            "fecha_lectura": "2024-01-01",
            "lectura_anterior": 0.0,
            "lectura_actual": 10.0,
            "dias_facturados": 30,
        },
        {
            "numero_suministro": 7002,  # type-broken: should be a string
            "fecha_lectura": "2024-01-02",
            "lectura_anterior": 0.0,
            "lectura_actual": 10.0,
            "dias_facturados": 30,
        },
        {
            "numero_suministro": "SUM-7001",
            "fecha_lectura": "not-a-date",
            "lectura_anterior": 0.0,
            "lectura_actual": 10.0,
            "dias_facturados": 30,
        },
    ]

    response = await lecturas_client.post("/api/v1/lecturas/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert len(body["rejected"]) == 2
    reasons_by_fecha = {
        str(rejected["record"].get("fecha_lectura")): rejected["reasons"]
        for rejected in body["rejected"]
    }
    assert any("numero_suministro" in reason for reason in reasons_by_fecha["2024-01-02"])
    assert any("fecha_lectura" in reason for reason in reasons_by_fecha["not-a-date"])


async def test_import_rejects_a_nan_bearing_record_individually_instead_of_500ing_the_batch(
    lecturas_client: AsyncClient,
) -> None:
    """`json.dumps(float("nan"))` emits the non-standard `NaN` token, which Python's own
    `json.loads` (used by Starlette to parse the request body) accepts by default -- so a NaN
    value can reach the domain layer through an ordinary JSON request, not just through a unit
    test constructing a `float` directly. Sent as a raw body (not via httpx's `json=` kwarg,
    which would fail trying to encode a NaN the same lax way) to control the exact bytes on the
    wire.
    """
    await _seed_cliente(lecturas_client, "7201")
    await _seed_suministro(lecturas_client, numero_suministro="SUM-7201", numero_cliente="7201")
    payload = [
        {
            "numero_suministro": "SUM-7201",
            "fecha_lectura": "2024-01-01",
            "lectura_anterior": 0.0,
            "lectura_actual": 10.0,
            "dias_facturados": 30,
        },
        {
            "numero_suministro": "SUM-7201",
            "fecha_lectura": "2024-01-02",
            "lectura_anterior": 0.0,
            "lectura_actual": float("nan"),
            "dias_facturados": 30,
        },
        {
            "numero_suministro": "SUM-7201",
            "fecha_lectura": "2024-01-03",
            "lectura_anterior": 0.0,
            "lectura_actual": 20.0,
            "dias_facturados": 30,
        },
    ]
    raw_body = json.dumps(payload).encode()
    assert b"NaN" in raw_body  # sanity check: the raw wire body actually carries the NaN token

    response = await lecturas_client.post(
        "/api/v1/lecturas/import",
        content=raw_body,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 2
    assert len(body["rejected"]) == 1
    assert any("lectura_actual" in reason for reason in body["rejected"][0]["reasons"])


async def test_import_rejects_a_boolean_dias_facturados_individually(
    lecturas_client: AsyncClient,
) -> None:
    """`dias_facturados: true` would otherwise lax-coerce to `1` at the schema boundary --
    `bool` is a subclass of `int` in Python, and Pydantic's default (non-strict) mode coerces
    bool to int/float unless told not to. Rejected as a structural violation instead, before it
    ever reaches `Lectura.create()`'s own (defense-in-depth) bool guard."""
    await _seed_cliente(lecturas_client, "7301")
    await _seed_suministro(lecturas_client, numero_suministro="SUM-7301", numero_cliente="7301")
    payload = [
        {
            "numero_suministro": "SUM-7301",
            "fecha_lectura": "2024-01-01",
            "lectura_anterior": 0.0,
            "lectura_actual": 10.0,
            "dias_facturados": 30,
        },
        {
            "numero_suministro": "SUM-7301",
            "fecha_lectura": "2024-01-02",
            "lectura_anterior": 0.0,
            "lectura_actual": 10.0,
            "dias_facturados": True,
        },
    ]

    response = await lecturas_client.post("/api/v1/lecturas/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert len(body["rejected"]) == 1
    assert any("dias_facturados" in reason for reason in body["rejected"][0]["reasons"])


async def test_import_rejects_a_boolean_lectura_anterior_individually(
    lecturas_client: AsyncClient,
) -> None:
    """Same guard as `dias_facturados`, for `lectura_anterior`: `true` would otherwise
    lax-coerce to `1.0`."""
    await _seed_cliente(lecturas_client, "7401")
    await _seed_suministro(lecturas_client, numero_suministro="SUM-7401", numero_cliente="7401")
    payload = [
        {
            "numero_suministro": "SUM-7401",
            "fecha_lectura": "2024-01-01",
            "lectura_anterior": 0.0,
            "lectura_actual": 10.0,
            "dias_facturados": 30,
        },
        {
            "numero_suministro": "SUM-7401",
            "fecha_lectura": "2024-01-02",
            "lectura_anterior": True,
            "lectura_actual": 10.0,
            "dias_facturados": 30,
        },
    ]

    response = await lecturas_client.post("/api/v1/lecturas/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert len(body["rejected"]) == 1
    assert any("lectura_anterior" in reason for reason in body["rejected"][0]["reasons"])


async def test_import_rejects_a_boolean_lectura_actual_individually(
    lecturas_client: AsyncClient,
) -> None:
    """Same guard as `lectura_anterior`, for `lectura_actual`: `false` would otherwise
    lax-coerce to `0.0`."""
    await _seed_cliente(lecturas_client, "7501")
    await _seed_suministro(lecturas_client, numero_suministro="SUM-7501", numero_cliente="7501")
    payload = [
        {
            "numero_suministro": "SUM-7501",
            "fecha_lectura": "2024-01-01",
            "lectura_anterior": 0.0,
            "lectura_actual": 10.0,
            "dias_facturados": 30,
        },
        {
            "numero_suministro": "SUM-7501",
            "fecha_lectura": "2024-01-02",
            "lectura_anterior": 0.0,
            "lectura_actual": False,
            "dias_facturados": 30,
        },
    ]

    response = await lecturas_client.post("/api/v1/lecturas/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert len(body["rejected"]) == 1
    assert any("lectura_actual" in reason for reason in body["rejected"][0]["reasons"])


async def test_import_upserts_over_a_duplicate_key_within_the_same_request(
    lecturas_client: AsyncClient,
) -> None:
    await _seed_cliente(lecturas_client, "7101")
    await _seed_suministro(lecturas_client, numero_suministro="SUM-7101", numero_cliente="7101")
    payload = [
        {
            "numero_suministro": "SUM-7101",
            "fecha_lectura": "2024-01-01",
            "lectura_anterior": 0.0,
            "lectura_actual": 100.0,
            "dias_facturados": 30,
        },
        {
            "numero_suministro": "SUM-7101",
            "fecha_lectura": "2024-01-01",
            "lectura_anterior": 0.0,
            "lectura_actual": 120.0,
            "dias_facturados": 30,
        },
    ]

    response = await lecturas_client.post("/api/v1/lecturas/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert body["updated"] == 1
    assert body["rejected"] == []

    listing = await lecturas_client.get("/api/v1/lecturas")
    matching = [item for item in listing.json()["items"] if item["fecha_lectura"] == "2024-01-01"]
    assert len(matching) == 1
    assert matching[0]["lectura_actual"] == "120.000"


async def test_get_lecturas_lists_only_the_imported_lecturas(lecturas_client: AsyncClient) -> None:
    await _seed_cliente(lecturas_client, "5001")
    await _seed_suministro(lecturas_client, numero_suministro="SUM-5001", numero_cliente="5001")
    payload = [
        {
            "numero_suministro": "SUM-5001",
            "fecha_lectura": "2024-01-01",
            "lectura_anterior": 0.0,
            "lectura_actual": 10.0,
            "dias_facturados": 30,
        },
        {
            "numero_suministro": "SUM-5001",
            "fecha_lectura": "2024-02-01",
            "lectura_anterior": 10.0,
            "lectura_actual": 20.0,
            "dias_facturados": 31,
        },
    ]
    await lecturas_client.post("/api/v1/lecturas/import", json=payload)

    response = await lecturas_client.get("/api/v1/lecturas")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {item["fecha_lectura"] for item in body["items"]} == {"2024-01-01", "2024-02-01"}


async def test_get_lecturas_honors_limit_and_offset(lecturas_client: AsyncClient) -> None:
    await _seed_cliente(lecturas_client, "6001")
    await _seed_suministro(lecturas_client, numero_suministro="SUM-6001", numero_cliente="6001")
    payload = [
        {
            "numero_suministro": "SUM-6001",
            "fecha_lectura": f"2024-01-{day:02d}",
            "lectura_anterior": 0.0,
            "lectura_actual": 10.0,
            "dias_facturados": 30,
        }
        for day in range(1, 6)
    ]
    await lecturas_client.post("/api/v1/lecturas/import", json=payload)

    response = await lecturas_client.get("/api/v1/lecturas", params={"limit": 2, "offset": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 2
    assert body["offset"] == 1
    assert len(body["items"]) == 2
    assert body["total"] == 5


async def test_get_lecturas_filters_by_numero_suministro(lecturas_client: AsyncClient) -> None:
    await _seed_cliente(lecturas_client, "8001")
    await _seed_suministro(lecturas_client, numero_suministro="SUM-8001", numero_cliente="8001")
    await _seed_suministro(lecturas_client, numero_suministro="SUM-8002", numero_cliente="8001")
    await lecturas_client.post(
        "/api/v1/lecturas/import",
        json=[
            {
                "numero_suministro": "SUM-8001",
                "fecha_lectura": "2024-01-01",
                "lectura_anterior": 0.0,
                "lectura_actual": 10.0,
                "dias_facturados": 30,
            },
            {
                "numero_suministro": "SUM-8002",
                "fecha_lectura": "2024-01-01",
                "lectura_anterior": 0.0,
                "lectura_actual": 20.0,
                "dias_facturados": 30,
            },
        ],
    )

    response = await lecturas_client.get(
        "/api/v1/lecturas", params={"numero_suministro": "SUM-8001"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["lectura_actual"] == "10.000"


async def test_get_lecturas_filtered_by_a_nonexistent_numero_suministro_returns_an_empty_page(
    lecturas_client: AsyncClient,
) -> None:
    response = await lecturas_client.get(
        "/api/v1/lecturas", params={"numero_suministro": "does-not-exist"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []


async def test_reimporting_a_soft_deleted_key_creates_a_new_identity(
    lecturas_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Same current, deliberate semantics as clientes/suministros (see `contexts/README.md`,
    "Comportamiento ante soft-delete"): re-importing a soft-deleted `(suministro_id,
    fecha_lectura)` creates a brand-new row with a new id, it does not resurrect the original
    row -- because `uq_lecturas_suministro_fecha` is a partial unique index (`WHERE deleted_at
    IS NULL`).
    """
    await _seed_cliente(lecturas_client, "9001")
    await _seed_suministro(lecturas_client, numero_suministro="SUM-9001", numero_cliente="9001")
    await lecturas_client.post(
        "/api/v1/lecturas/import",
        json=[
            {
                "numero_suministro": "SUM-9001",
                "fecha_lectura": "2024-01-01",
                "lectura_anterior": 0.0,
                "lectura_actual": 10.0,
                "dias_facturados": 30,
            }
        ],
    )
    listing_before = (await lecturas_client.get("/api/v1/lecturas")).json()
    original_id = next(
        item["id"] for item in listing_before["items"] if item["fecha_lectura"] == "2024-01-01"
    )

    await db_session.execute(
        text("UPDATE lecturas SET deleted_at = now() WHERE fecha_lectura = '2024-01-01'")
    )
    await db_session.commit()

    second = await lecturas_client.post(
        "/api/v1/lecturas/import",
        json=[
            {
                "numero_suministro": "SUM-9001",
                "fecha_lectura": "2024-01-01",
                "lectura_anterior": 0.0,
                "lectura_actual": 10.0,
                "dias_facturados": 30,
            }
        ],
    )

    assert second.json()["created"] == 1
    listing_after = (await lecturas_client.get("/api/v1/lecturas")).json()
    new_id = next(
        item["id"] for item in listing_after["items"] if item["fecha_lectura"] == "2024-01-01"
    )
    assert new_id != original_id
