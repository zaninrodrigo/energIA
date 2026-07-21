"""POST the mapped payloads to EnergIA's real import API, in FK-dependency order.

Unlike the synthetic loader (which fails loudly on any rejection, since synthetic data must be
100% importable), real data may have problematic rows -- so this COLLECTS and REPORTS rejections
per entity instead of crashing, and keeps going. Writing goes only through the public import
endpoints, never straight to the database (REAL_DATA_IMPORT_SPEC.md §6).
"""

from dataclasses import dataclass, field
from typing import Any

import httpx

from energia.tools.real_import.mapper import ImportPayloads

_DEFAULT_BATCH_SIZE = 200
_ENTITY_ORDER = ("clientes", "suministros", "lotes", "consumos")


@dataclass(slots=True)
class EntityResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    restored: int = 0
    rejected: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ImportReport:
    por_entidad: dict[str, EntityResult] = field(default_factory=dict)


async def _post_entity(
    client: httpx.AsyncClient, entity: str, records: list[dict[str, Any]], batch_size: int
) -> EntityResult:
    result = EntityResult()
    path = f"/api/v1/{entity}/import"
    for start in range(0, len(records), batch_size):
        batch = records[start : start + batch_size]
        response = await client.post(path, json=batch)
        response.raise_for_status()
        body = response.json()
        result.created += body["created"]
        result.updated += body["updated"]
        result.unchanged += body["unchanged"]
        result.restored += body["restored"]
        result.rejected.extend(body.get("rejected", []))
    return result


async def import_payloads(
    client: httpx.AsyncClient,
    payloads: ImportPayloads,
    *,
    batch_size: int = _DEFAULT_BATCH_SIZE,
) -> ImportReport:
    """POST every entity in FK-dependency order, returning a per-entity report (counts +
    rejections). Does not raise on rejected records -- they are reported, not fatal."""
    records_by_entity = {
        "clientes": payloads.clientes,
        "suministros": payloads.suministros,
        "lotes": payloads.lotes,
        "consumos": payloads.consumos,
    }
    report = ImportReport()
    for entity in _ENTITY_ORDER:
        report.por_entidad[entity] = await _post_entity(
            client, entity, records_by_entity[entity], batch_size
        )
    return report
