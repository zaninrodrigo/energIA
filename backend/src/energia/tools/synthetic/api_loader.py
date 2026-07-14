"""HTTP import loader: posts a `SyntheticDataset`'s payload lists through the REAL EnergIA
import API (docs/03-architecture/API_SPEC.md), in FK-dependency order -- clientes, then
suministros (needs `numero_cliente`), then lotes (no FK), then lecturas (needs
`numero_suministro`), then consumos (needs `numero_suministro`/`codigo_lote`, and optionally
resolves `fecha_lectura`).

This generator's entire purpose is to be 100% importable: an ImportSummary's `rejected` list
being non-empty is always a bug in the generator (a validation rule the payload didn't
anticipate), never a legitimate outcome for synthetic data. `post_batches()` therefore FAILS
LOUDLY (`ImportRejectionError`) on the first batch with any rejection, instead of logging and
continuing -- silently swallowing a rejection would defeat the entire point of this tool (a
fixture that calibration work can trust to be complete).

Callers own the `httpx.AsyncClient` (and therefore its transport): the CLI builds one with
`base_url=<a real running server>`; tests build one with `httpx.ASGITransport(app=create_app())`
against the `energia_test` database, entirely in-process, no real network call and no separate
uvicorn process required (see `tests/integration/tools/synthetic/conftest.py`).
"""

from dataclasses import dataclass, field

import httpx

from energia.tools.synthetic.generator import SyntheticDataset

DEFAULT_BATCH_SIZE = 500

# FK-dependency order: each entry must be importable before the ones that reference its natural
# key (see this module's docstring).
_IMPORT_ORDER: tuple[str, ...] = ("clientes", "suministros", "lotes", "lecturas", "consumos")


class ImportRejectionError(RuntimeError):
    """Raised the moment any import batch reports one or more `rejected` records -- always a
    generator bug for synthetic data (see this module's docstring), never logged-and-ignored."""


@dataclass(frozen=True, slots=True)
class LoadReport:
    """Aggregate `created`/`updated`/`unchanged`/`restored` counts per entity name, summed
    across every batch of every endpoint `load_dataset()` posted to."""

    created: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    unchanged: dict[str, int] = field(default_factory=dict)
    restored: dict[str, int] = field(default_factory=dict)


def _endpoint_path(entity: str) -> str:
    return f"/api/v1/{entity}/import"


async def post_batches(
    client: httpx.AsyncClient,
    path: str,
    records: list[dict[str, object]],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[int, int, int, int]:
    """POST `records` to `path` in chunks of at most `batch_size`, returning the summed
    `(created, updated, unchanged, restored)` counts across every chunk.

    Raises `ImportRejectionError` immediately on the first chunk with any `rejected` entry --
    the response body's own reasons are included in the error message, so the failure is
    diagnosable without re-running anything.
    """
    totals = [0, 0, 0, 0]  # created, updated, unchanged, restored

    for start in range(0, len(records), batch_size):
        batch = records[start : start + batch_size]
        response = await client.post(path, json=batch)
        response.raise_for_status()
        body = response.json()

        rejected = body.get("rejected", [])
        if rejected:
            raise ImportRejectionError(
                f"{path}: {len(rejected)} record(s) rejected in the batch starting at index "
                f"{start} -- this is a generator bug, synthetic data must always be 100% "
                f"importable. First rejection(s): {rejected[:3]!r}"
            )

        totals[0] += body["created"]
        totals[1] += body["updated"]
        totals[2] += body["unchanged"]
        totals[3] += body["restored"]

    return totals[0], totals[1], totals[2], totals[3]


async def load_dataset(
    client: httpx.AsyncClient,
    dataset: SyntheticDataset,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> LoadReport:
    """Load every payload list of `dataset` through the real import API, in FK-dependency
    order, batching each endpoint's POSTs at `batch_size` records."""
    payloads: dict[str, list[dict[str, object]]] = {
        "clientes": dataset.clientes,
        "suministros": dataset.suministros,
        "lotes": dataset.lotes,
        "lecturas": dataset.lecturas,
        "consumos": dataset.consumos,
    }

    report = LoadReport()
    for entity in _IMPORT_ORDER:
        created, updated, unchanged, restored = await post_batches(
            client, _endpoint_path(entity), payloads[entity], batch_size=batch_size
        )
        report.created[entity] = created
        report.updated[entity] = updated
        report.unchanged[entity] = unchanged
        report.restored[entity] = restored

    return report
