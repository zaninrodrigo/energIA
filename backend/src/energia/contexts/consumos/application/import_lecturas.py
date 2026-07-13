"""ImportLecturas: the US-003 use case -- import lecturas from any LecturaSource, idempotently.

"Como Administrador, quiero importar las lecturas históricas, para disponer del histórico
completo" (docs/02-requirements/USER_STORIES.md). Mirrors `contexts/suministros/application/
import_suministros.py`'s pattern exactly, with one genuine difference: identity is the composite
natural key `(suministro_id, fecha_lectura)`, not a single natural key -- a `LecturaSourceRecord`
references its suministro by natural key (`numero_suministro`), not by UUID, so this use case
must resolve it to a UUID *before* it can even attempt `Lectura.create()`, exactly what
`SuministroDirectory` (domain/ports.py) is for.
"""

from dataclasses import dataclass, field

from energia.contexts.consumos.domain.lectura import Lectura, LecturaValidationError
from energia.contexts.consumos.domain.ports import (
    LecturaConflictError,
    LecturaRepository,
    LecturaSource,
    LecturaSourceRecord,
    SuministroDirectory,
)

# Fields compared to decide whether an existing lectura's data actually changed. `id`,
# `suministro_id` and `fecha_lectura` are intentionally excluded: the latter two form the
# identity a record is looked up by, and never change across an update by construction; `id`
# never changes across an update either.
_COMPARABLE_FIELDS = ("lectura_anterior", "lectura_actual", "dias_facturados")


@dataclass(frozen=True, slots=True)
class RejectedRecord:
    """A source record rejected during import -- for a missing/nonexistent suministro reference,
    or a violated `Lectura` invariant. The batch continues anyway."""

    record: LecturaSourceRecord
    reasons: list[str]


@dataclass(frozen=True, slots=True)
class ImportSummary:
    """Outcome of one `ImportLecturas.execute()` run."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: list[RejectedRecord] = field(default_factory=list)


class ImportLecturas:
    """Consume a `LecturaSource` and upsert every valid record by `(suministro_id,
    fecha_lectura)`.

    Idempotent by design: each record is looked up by its composite natural key before deciding
    whether to create, update, or leave it alone, so re-running the exact same import reports
    `unchanged` counts instead of duplicate rows or spurious writes.
    """

    def __init__(
        self,
        repository: LecturaRepository,
        suministro_directory: SuministroDirectory,
    ) -> None:
        self._repository = repository
        self._suministro_directory = suministro_directory

    async def execute(self, source: LecturaSource) -> ImportSummary:
        """Process every record sequentially against `self._repository`, in one unit of work.

        Neither this method nor `self._repository.save()` commits: the caller (the HTTP route
        today) commits once after `execute()` returns, so the whole batch is one all-or-nothing
        transaction from the database's point of view. Records are still rejected individually
        -- a missing/nonexistent `numero_suministro` reference, a domain-invalid record
        (`LecturaValidationError`), or a record whose write hits a database integrity conflict
        (`LecturaConflictError`) is added to `rejected` and the loop continues; only an
        *unexpected* exception (anything else) propagates and aborts the whole batch uncommitted.

        Sequential per-record processing is also what makes a duplicate `(numero_suministro,
        fecha_lectura)` *within* the same payload safe: the second occurrence's
        `get_by_suministro_and_fecha` lookup sees the first occurrence's already-`save()`d row
        (same transaction, read-your-own-write), so it upserts over it (`updated`/`unchanged`)
        instead of conflicting.
        """
        created = updated = unchanged = 0
        rejected: list[RejectedRecord] = []

        for record in source.fetch():
            numero_suministro = (record.numero_suministro or "").strip()
            if not numero_suministro:
                rejected.append(
                    RejectedRecord(record=record, reasons=["numero_suministro es obligatorio"])
                )
                continue

            suministro_id = await self._suministro_directory.resolve(numero_suministro)
            if suministro_id is None:
                rejected.append(
                    RejectedRecord(
                        record=record,
                        reasons=[
                            f"suministro inexistente: numero_suministro={numero_suministro!r}"
                        ],
                    )
                )
                continue

            try:
                candidate = Lectura.create(
                    suministro_id=suministro_id,
                    fecha_lectura=record.fecha_lectura,
                    lectura_anterior=record.lectura_anterior,
                    lectura_actual=record.lectura_actual,
                    dias_facturados=record.dias_facturados,
                )
            except LecturaValidationError as error:
                rejected.append(RejectedRecord(record=record, reasons=error.errors))
                continue

            existing = await self._repository.get_by_suministro_and_fecha(
                candidate.suministro_id, candidate.fecha_lectura
            )
            if existing is None:
                to_save = candidate
                is_new = True
            elif _differs(existing, candidate):
                # Re-validated through Lectura.create() (not a raw replace()) so the merged
                # record honors every invariant too, keeping the existing row's identity (id).
                to_save = Lectura.create(
                    id=existing.id,
                    suministro_id=candidate.suministro_id,
                    fecha_lectura=candidate.fecha_lectura,
                    lectura_anterior=candidate.lectura_anterior,
                    lectura_actual=candidate.lectura_actual,
                    dias_facturados=candidate.dias_facturados,
                )
                is_new = False
            else:
                unchanged += 1
                continue

            try:
                await self._repository.save(to_save)
            except LecturaConflictError as error:
                rejected.append(RejectedRecord(record=record, reasons=[str(error)]))
                continue

            if is_new:
                created += 1
            else:
                updated += 1

        return ImportSummary(
            created=created, updated=updated, unchanged=unchanged, rejected=rejected
        )


def _differs(existing: Lectura, candidate: Lectura) -> bool:
    return any(
        getattr(existing, field_name) != getattr(candidate, field_name)
        for field_name in _COMPARABLE_FIELDS
    )
