"""DeleteConsumo: the DECISION #13 use case -- a correction path for a wrongly imported consumo
(a billing period ingested with a wrong `fecha_inicio`/`fecha_fin`, `kwh`, or any other field).

Before this decision, the only way to retract a bad consumo record was a manual soft-delete by
direct SQL (see PROJECT_MASTER_SPEC.md's now-resolved item #13 and API_SPEC.md's rewritten
"Limitación conocida" paragraph, `docs/03-architecture/API_SPEC.md`, "Contexto: Gestión de
Consumos"). This use case exposes that same soft-delete as a proper API operation
(`DELETE /api/v1/consumos/{id}`): delete the wrong record, then re-import the corrected data for
the same period -- which DECISION #9's resurrection turns back into the SAME row (`restored: 1`),
not a duplicate, closing the loop cleanly.
"""

from uuid import UUID

from energia.contexts.consumos.domain.ports import ConsumoRepository


class DeleteConsumo:
    """Soft-delete the ACTIVE consumo with the given `id`.

    Idempotent-looking, but not idempotent in outcome: a second call for the same `id` returns
    `False` (nothing left to delete), not `True` again -- the caller (route boundary) maps `True`
    to `204 No Content` and `False` to `404 Not Found`, the same status for "already deleted" and
    "never existed" (see `presentation/routes.py`).
    """

    def __init__(self, repository: ConsumoRepository) -> None:
        self._repository = repository

    async def execute(self, consumo_id: UUID) -> bool:
        """Return whether an active consumo was found and soft-deleted. Does not commit: the
        caller (route boundary) controls the transaction -- a `False` result commits nothing.
        """
        return await self._repository.soft_delete(consumo_id)
