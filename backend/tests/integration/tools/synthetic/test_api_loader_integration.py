"""Integration tests for energia.tools.synthetic.api_loader against the real `energia_test`
database (never `energia`) via an in-process ASGI transport -- see `conftest.py`. Never requires
a real running uvicorn server; a separate manual validation phase does that end-to-end.
"""

import pytest
from httpx import AsyncClient

from energia.tools.synthetic.api_loader import (
    ImportRejectionError,
    load_dataset,
    post_batches,
)
from energia.tools.synthetic.generator import generate_dataset

pytestmark = pytest.mark.integration


async def test_post_batches_imports_all_records_across_multiple_small_batches(
    client: AsyncClient,
) -> None:
    records = [{"numero_cliente": f"API-LOADER-{i}", "nombre": f"Cliente {i}"} for i in range(5)]

    created, updated, unchanged, restored = await post_batches(
        client, "/api/v1/clientes/import", records, batch_size=2
    )

    assert (created, updated, unchanged, restored) == (5, 0, 0, 0)
    listing = (await client.get("/api/v1/clientes", params={"limit": 50})).json()
    assert listing["total"] == 5


async def test_post_batches_raises_loudly_on_any_rejected_record(client: AsyncClient) -> None:
    records = [
        {"numero_cliente": "API-LOADER-OK", "nombre": "Valido"},
        {"numero_cliente": "", "nombre": "Sin numero"},  # domain rejection: blank numero_cliente
    ]

    with pytest.raises(ImportRejectionError, match="clientes/import"):
        await post_batches(client, "/api/v1/clientes/import", records, batch_size=10)


async def test_load_dataset_imports_a_full_generated_dataset_end_to_end(
    client: AsyncClient,
) -> None:
    """A generator bug would surface here as an `ImportRejectionError` -- this is the load-bearing
    proof that `generate_dataset()`'s output is 100% importable through the real API."""
    dataset = generate_dataset(seed=1, scale="small", months=2)

    report = await load_dataset(client, dataset)

    assert report.created["clientes"] == len(dataset.clientes)
    assert report.created["suministros"] == len(dataset.suministros)
    assert report.created["lotes"] == len(dataset.lotes)
    assert report.created["lecturas"] == len(dataset.lecturas)
    assert report.created["consumos"] == len(dataset.consumos)

    consumos_listing = (await client.get("/api/v1/consumos", params={"limit": 1})).json()
    assert consumos_listing["total"] == len(dataset.consumos)


async def test_load_dataset_reimport_reports_unchanged_not_created(client: AsyncClient) -> None:
    """Idempotency end-to-end: re-loading the exact same dataset must not duplicate rows."""
    dataset = generate_dataset(seed=2, scale="small", months=2)
    await load_dataset(client, dataset)

    second_report = await load_dataset(client, dataset)

    assert second_report.created["consumos"] == 0
    assert second_report.unchanged["consumos"] == len(dataset.consumos)
