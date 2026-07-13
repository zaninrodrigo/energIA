"""Integration tests for the `lotes` HTTP API against the real `energia_test` database.

Covers the acceptance path for US-005: import from a JSON payload (creating and rejecting
records individually), idempotent re-import, the estado-preservation invariant (RD-010's
intent -- an import re-run must never reset an already-`Procesado` lote back to `Pendiente`),
and the paginated list endpoint (with the `estado` filter). Never touches `energia` -- see
tests/integration/conftest.py for the isolation strategy.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def test_import_creates_valid_records_and_rejects_invalid_ones_individually(
    lotes_client: AsyncClient,
) -> None:
    payload = [
        {"codigo_lote": "LOTE-2024-01", "nombre": "Enero 2024", "cantidad_registros": 100},
        {"codigo_lote": "LOTE-2024-02", "nombre": "Febrero 2024", "cantidad_registros": 120},
        {"codigo_lote": "   ", "nombre": "Lote sin codigo", "cantidad_registros": 10},
        {"codigo_lote": "LOTE-2024-03", "nombre": "Marzo 2024", "cantidad_registros": -5},
    ]

    response = await lotes_client.post("/api/v1/lotes/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 2
    assert body["updated"] == 0
    assert body["unchanged"] == 0
    assert len(body["rejected"]) == 2
    reasons_by_nombre = {
        rejected["record"].get("nombre"): rejected["reasons"] for rejected in body["rejected"]
    }
    assert any("codigo_lote" in reason for reason in reasons_by_nombre["Lote sin codigo"])
    assert any("cantidad_registros" in reason for reason in reasons_by_nombre["Marzo 2024"])


async def test_rejected_record_echo_omits_a_field_left_unset_instead_of_leaking_the_sentinel(
    lotes_client: AsyncClient,
) -> None:
    """A domain-rejected record whose `nombre` was genuinely absent from the payload (`UNSET`,
    not sent at all) must echo back a `record` object with no `nombre` key at all -- not `null`,
    and certainly not the internal `UNSET` sentinel leaking into the HTTP response (FIX 1)."""
    payload = [{"codigo_lote": "   ", "cantidad_registros": 10}]  # blank codigo_lote: rejected

    response = await lotes_client.post("/api/v1/lotes/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert len(body["rejected"]) == 1
    echoed_record = body["rejected"][0]["record"]
    assert "nombre" not in echoed_record
    assert echoed_record["cantidad_registros"] == 10


async def test_reimporting_the_same_payload_is_idempotent(lotes_client: AsyncClient) -> None:
    payload = [{"codigo_lote": "LOTE-3001", "nombre": "Lote 3001", "cantidad_registros": 50}]

    first = await lotes_client.post("/api/v1/lotes/import", json=payload)
    second = await lotes_client.post("/api/v1/lotes/import", json=payload)

    assert first.json()["created"] == 1
    second_body = second.json()
    assert second_body["created"] == 0
    assert second_body["updated"] == 0
    assert second_body["unchanged"] == 1
    assert second_body["rejected"] == []


async def test_reimporting_with_changed_data_reports_updated(lotes_client: AsyncClient) -> None:
    await lotes_client.post(
        "/api/v1/lotes/import",
        json=[{"codigo_lote": "LOTE-4001", "nombre": "Original", "cantidad_registros": 10}],
    )

    response = await lotes_client.post(
        "/api/v1/lotes/import",
        json=[{"codigo_lote": "LOTE-4001", "nombre": "Actualizado", "cantidad_registros": 20}],
    )

    body = response.json()
    assert body["created"] == 0
    assert body["updated"] == 1
    assert body["unchanged"] == 0

    listing = (await lotes_client.get("/api/v1/lotes")).json()
    matching = next(item for item in listing["items"] if item["codigo_lote"] == "LOTE-4001")
    assert matching["nombre"] == "Actualizado"
    assert matching["cantidad_registros"] == 20


async def test_import_returns_422_for_a_malformed_body(lotes_client: AsyncClient) -> None:
    response = await lotes_client.post("/api/v1/lotes/import", json={"not": "a list"})

    assert response.status_code == 422


async def test_import_rejects_a_type_broken_record_individually_instead_of_422ing_the_batch(
    lotes_client: AsyncClient,
) -> None:
    payload = [
        {"codigo_lote": "LOTE-5001", "nombre": "Valido", "cantidad_registros": 10},
        {"codigo_lote": 5002, "nombre": "Codigo numerico", "cantidad_registros": 10},
    ]

    response = await lotes_client.post("/api/v1/lotes/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert len(body["rejected"]) == 1
    assert any("codigo_lote" in reason for reason in body["rejected"][0]["reasons"])


async def test_import_rejects_a_boolean_cantidad_registros_individually(
    lotes_client: AsyncClient,
) -> None:
    """`cantidad_registros: true` would otherwise lax-coerce to `1` at the schema boundary --
    `bool` is a subclass of `int` in Python."""
    payload = [
        {"codigo_lote": "LOTE-6001", "nombre": "Valido", "cantidad_registros": 10},
        {"codigo_lote": "LOTE-6002", "nombre": "Bool", "cantidad_registros": True},
    ]

    response = await lotes_client.post("/api/v1/lotes/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert len(body["rejected"]) == 1
    assert any("cantidad_registros" in reason for reason in body["rejected"][0]["reasons"])


async def test_import_rejects_an_out_of_range_cantidad_registros_instead_of_500ing_the_batch(
    lotes_client: AsyncClient,
) -> None:
    """Reviewer-reproduced case: `99999999999` is a plain JSON big-int literal that passes
    Pydantic (an unbounded Python int) and previously passed `Lote.create()` too, only to die at
    the database as an opaque `DBAPIError` -- a 500 for the whole batch, not a per-record
    rejection. Must be rejected here (HTTP 200), with the rest of the batch surviving."""
    payload = [
        {"codigo_lote": "LOTE-6101", "nombre": "Valido", "cantidad_registros": 10},
        {"codigo_lote": "LOTE-6102", "nombre": "Fuera de rango", "cantidad_registros": 99999999999},
    ]

    response = await lotes_client.post("/api/v1/lotes/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert len(body["rejected"]) == 1
    assert any("cantidad_registros" in reason for reason in body["rejected"][0]["reasons"])


async def test_import_defaults_cantidad_registros_to_zero_when_omitted(
    lotes_client: AsyncClient,
) -> None:
    response = await lotes_client.post("/api/v1/lotes/import", json=[{"codigo_lote": "LOTE-7001"}])

    assert response.status_code == 200
    assert response.json()["created"] == 1
    listing = (await lotes_client.get("/api/v1/lotes")).json()
    matching = next(item for item in listing["items"] if item["codigo_lote"] == "LOTE-7001")
    assert matching["cantidad_registros"] == 0
    assert matching["nombre"] is None


# -- FIX 1: omitted vs. explicitly-null fields on re-import, at the HTTP boundary ------------


async def test_reimporting_with_nombre_omitted_preserves_the_existing_value(
    lotes_client: AsyncClient,
) -> None:
    """The core FIX 1 regression, reproduced over HTTP: re-importing an existing `codigo_lote`
    with `nombre` merely *absent* from the JSON payload (not sent, not `null`) must not wipe it."""
    await lotes_client.post(
        "/api/v1/lotes/import",
        json=[{"codigo_lote": "LOTE-7101", "nombre": "Original", "cantidad_registros": 10}],
    )

    response = await lotes_client.post(
        "/api/v1/lotes/import",
        json=[{"codigo_lote": "LOTE-7101", "cantidad_registros": 20}],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["updated"] == 1
    listing = (await lotes_client.get("/api/v1/lotes")).json()
    matching = next(item for item in listing["items"] if item["codigo_lote"] == "LOTE-7101")
    assert matching["nombre"] == "Original"
    assert matching["cantidad_registros"] == 20


async def test_reimporting_with_nombre_explicitly_null_wipes_it(lotes_client: AsyncClient) -> None:
    """The other half of FIX 1's contract: `"nombre": null` (as opposed to leaving the key out
    entirely) does overwrite the stored value -- the caller genuinely asked for it to be cleared."""
    await lotes_client.post(
        "/api/v1/lotes/import",
        json=[{"codigo_lote": "LOTE-7102", "nombre": "Original", "cantidad_registros": 10}],
    )

    response = await lotes_client.post(
        "/api/v1/lotes/import",
        json=[{"codigo_lote": "LOTE-7102", "nombre": None, "cantidad_registros": 10}],
    )

    assert response.status_code == 200
    assert response.json()["updated"] == 1
    listing = (await lotes_client.get("/api/v1/lotes")).json()
    matching = next(item for item in listing["items"] if item["codigo_lote"] == "LOTE-7102")
    assert matching["nombre"] is None


async def test_reimporting_with_cantidad_registros_omitted_preserves_the_existing_value(
    lotes_client: AsyncClient,
) -> None:
    """Same regression as `nombre`, for `cantidad_registros`: omitted on re-import must not reset
    it to the domain default (`0`)."""
    await lotes_client.post(
        "/api/v1/lotes/import",
        json=[{"codigo_lote": "LOTE-7103", "nombre": "Original", "cantidad_registros": 50}],
    )

    response = await lotes_client.post(
        "/api/v1/lotes/import",
        json=[{"codigo_lote": "LOTE-7103", "nombre": "Actualizado"}],
    )

    assert response.status_code == 200
    assert response.json()["updated"] == 1
    listing = (await lotes_client.get("/api/v1/lotes")).json()
    matching = next(item for item in listing["items"] if item["codigo_lote"] == "LOTE-7103")
    assert matching["cantidad_registros"] == 50
    assert matching["nombre"] == "Actualizado"


async def test_reimporting_with_cantidad_registros_explicitly_null_is_rejected(
    lotes_client: AsyncClient,
) -> None:
    """`cantidad_registros` is a `NOT NULL` column with its own `DEFAULT 0` -- an explicit
    `"cantidad_registros": null` is rejected outright (per-record, HTTP 200) instead of silently
    defaulting it the same way an omitted field does, and the existing stored value survives
    untouched."""
    await lotes_client.post(
        "/api/v1/lotes/import",
        json=[{"codigo_lote": "LOTE-7104", "nombre": "Original", "cantidad_registros": 7}],
    )

    response = await lotes_client.post(
        "/api/v1/lotes/import",
        json=[{"codigo_lote": "LOTE-7104", "nombre": "Original", "cantidad_registros": None}],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["updated"] == 0
    assert body["unchanged"] == 0
    assert len(body["rejected"]) == 1
    assert any("cantidad_registros" in reason for reason in body["rejected"][0]["reasons"])
    listing = (await lotes_client.get("/api/v1/lotes")).json()
    matching = next(item for item in listing["items"] if item["codigo_lote"] == "LOTE-7104")
    assert matching["cantidad_registros"] == 7


async def test_reimporting_with_every_optional_field_omitted_reports_unchanged(
    lotes_client: AsyncClient,
) -> None:
    """Counting stays correct under a partial payload: re-importing with only `codigo_lote`
    (everything else omitted) reports `unchanged`, not `updated`, since nothing about the stored
    row actually changes."""
    await lotes_client.post(
        "/api/v1/lotes/import",
        json=[{"codigo_lote": "LOTE-7105", "nombre": "Original", "cantidad_registros": 9}],
    )

    response = await lotes_client.post("/api/v1/lotes/import", json=[{"codigo_lote": "LOTE-7105"}])

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 0
    assert body["updated"] == 0
    assert body["unchanged"] == 1


async def test_import_rejects_a_payload_containing_an_estado_field(
    lotes_client: AsyncClient,
) -> None:
    """RD-010's intent (domain/lote.py): there is no way for the import payload to fabricate an
    already-`Procesado` lote. `LoteImportItem` used to silently drop an unknown `estado` field
    (`extra="ignore"`); it now rejects it structurally instead (`extra="forbid"`), naming the
    offending key -- so a caller sending `estado` gets an explicit rejection reason instead of
    the field vanishing with no trace. The rest of the batch still goes through."""
    payload = [
        {"codigo_lote": "LOTE-8001", "estado": "Procesado", "cantidad_registros": 5},
        {"codigo_lote": "LOTE-8002", "cantidad_registros": 5},
    ]

    response = await lotes_client.post("/api/v1/lotes/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert len(body["rejected"]) == 1
    assert any("estado" in reason for reason in body["rejected"][0]["reasons"])
    listing = (await lotes_client.get("/api/v1/lotes")).json()
    assert {item["codigo_lote"] for item in listing["items"]} == {"LOTE-8002"}


async def test_import_rejects_a_typo_field_instead_of_silently_dropping_it(
    lotes_client: AsyncClient,
) -> None:
    """A typo'd key (e.g. `canditad_registros` instead of `cantidad_registros`) used to be
    dropped silently by `extra="ignore"` -- the exact, invisible way a data-loss re-import could
    be triggered: the typo'd payload looks identical to an intentional omission of
    `cantidad_registros`, which (per FIX 1) now preserves the existing value on update, but would
    still wipe it on a first create with no error surfaced anywhere. Rejected structurally now,
    naming the offending key."""
    payload = [{"codigo_lote": "LOTE-8101", "canditad_registros": 100}]

    response = await lotes_client.post("/api/v1/lotes/import", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 0
    assert len(body["rejected"]) == 1
    assert any("canditad_registros" in reason for reason in body["rejected"][0]["reasons"])


async def test_reimporting_after_the_lote_transitioned_to_procesado_never_resets_its_estado(
    lotes_client: AsyncClient, db_session: AsyncSession
) -> None:
    """The core estado-preservation invariant this slice exists to protect: an import re-run
    must never reset a `Procesado` lote back to `Pendiente` (RD-010's intent). Flips the row's
    `estado` directly via SQL (the processing engine that would do this through
    `Lote.transition_to()` does not exist yet -- a future user story) to simulate "this batch was
    already processed", then re-imports the same `codigo_lote` with different metadata."""
    await lotes_client.post(
        "/api/v1/lotes/import",
        json=[{"codigo_lote": "LOTE-9001", "nombre": "Original", "cantidad_registros": 10}],
    )
    await db_session.execute(
        text("UPDATE lotes SET estado = 'Procesado' WHERE codigo_lote = 'LOTE-9001'")
    )
    await db_session.commit()

    response = await lotes_client.post(
        "/api/v1/lotes/import",
        json=[{"codigo_lote": "LOTE-9001", "nombre": "Actualizado", "cantidad_registros": 99}],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 0
    assert body["updated"] == 1
    assert body["unchanged"] == 0

    listing = (await lotes_client.get("/api/v1/lotes")).json()
    matching = next(item for item in listing["items"] if item["codigo_lote"] == "LOTE-9001")
    assert matching["estado"] == "Procesado"
    assert matching["nombre"] == "Actualizado"
    assert matching["cantidad_registros"] == 99


async def test_get_lotes_lists_only_the_imported_lotes(lotes_client: AsyncClient) -> None:
    await lotes_client.post(
        "/api/v1/lotes/import",
        json=[
            {"codigo_lote": "LOTE-A1", "cantidad_registros": 1},
            {"codigo_lote": "LOTE-A2", "cantidad_registros": 2},
        ],
    )

    response = await lotes_client.get("/api/v1/lotes")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {item["codigo_lote"] for item in body["items"]} == {"LOTE-A1", "LOTE-A2"}


async def test_get_lotes_honors_limit_and_offset(lotes_client: AsyncClient) -> None:
    await lotes_client.post(
        "/api/v1/lotes/import",
        json=[{"codigo_lote": f"LOTE-B{i}", "cantidad_registros": i} for i in range(5)],
    )

    response = await lotes_client.get("/api/v1/lotes", params={"limit": 2, "offset": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 2
    assert body["offset"] == 1
    assert len(body["items"]) == 2
    assert body["total"] == 5


async def test_get_lotes_filters_by_estado(lotes_client: AsyncClient) -> None:
    await lotes_client.post(
        "/api/v1/lotes/import",
        json=[
            {"codigo_lote": "LOTE-C1", "cantidad_registros": 1},
            {"codigo_lote": "LOTE-C2", "cantidad_registros": 2},
        ],
    )
    response = await lotes_client.get("/api/v1/lotes", params={"estado": "Pendiente"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert all(item["estado"] == "Pendiente" for item in body["items"])


async def test_get_lotes_with_an_invalid_estado_returns_422(lotes_client: AsyncClient) -> None:
    response = await lotes_client.get("/api/v1/lotes", params={"estado": "Foo"})

    assert response.status_code == 422


async def test_get_lotes_orders_most_recently_imported_first(
    lotes_client: AsyncClient, db_session: AsyncSession
) -> None:
    await lotes_client.post("/api/v1/lotes/import", json=[{"codigo_lote": "LOTE-D1"}])
    await lotes_client.post("/api/v1/lotes/import", json=[{"codigo_lote": "LOTE-D2"}])
    # Force a deterministic, unambiguous ordering: back-to-back imports can land in the same
    # database `now()` tick on a fast machine, which would make this assertion flaky without it.
    await db_session.execute(
        text(
            "UPDATE lotes SET fecha_importacion = fecha_importacion - interval '1 hour' "
            "WHERE codigo_lote = 'LOTE-D1'"
        )
    )
    await db_session.commit()

    response = await lotes_client.get("/api/v1/lotes")

    body = response.json()
    codigos = [item["codigo_lote"] for item in body["items"]]
    assert codigos.index("LOTE-D2") < codigos.index("LOTE-D1")


async def test_reimporting_a_soft_deleted_codigo_lote_creates_a_new_identity(
    lotes_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Same current, deliberate semantics as clientes/suministros/lecturas (see
    `contexts/README.md`, "Comportamiento ante soft-delete"): re-importing a soft-deleted
    `codigo_lote` creates a brand-new row with a new id, it does not resurrect the original row
    -- because `uq_lotes_codigo_lote` is a partial unique index (`WHERE deleted_at IS NULL`).
    """
    await lotes_client.post(
        "/api/v1/lotes/import", json=[{"codigo_lote": "LOTE-E1", "cantidad_registros": 5}]
    )
    listing_before = (await lotes_client.get("/api/v1/lotes")).json()
    original_id = next(
        item["id"] for item in listing_before["items"] if item["codigo_lote"] == "LOTE-E1"
    )

    await db_session.execute(
        text("UPDATE lotes SET deleted_at = now() WHERE codigo_lote = 'LOTE-E1'")
    )
    await db_session.commit()

    second = await lotes_client.post(
        "/api/v1/lotes/import", json=[{"codigo_lote": "LOTE-E1", "cantidad_registros": 5}]
    )

    assert second.json()["created"] == 1
    listing_after = (await lotes_client.get("/api/v1/lotes")).json()
    new_id = next(item["id"] for item in listing_after["items"] if item["codigo_lote"] == "LOTE-E1")
    assert new_id != original_id
