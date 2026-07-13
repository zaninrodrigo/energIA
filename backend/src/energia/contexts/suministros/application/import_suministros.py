"""ImportSuministros: the US-002 use case -- import suministros from any SuministroSource,
idempotently.

"Como Administrador, quiero importar automáticamente los suministros, para evitar cargas
manuales" (docs/02-requirements/USER_STORIES.md). Mirrors `contexts/clientes/application/
import_clientes.py`'s pattern exactly, with one genuine difference: a `SuministroSourceRecord`
references its cliente and categoria tarifaria by natural key (`numero_cliente`,
`categoria_tarifaria`'s `nombre`), not by UUID, so this use case must resolve both to UUIDs
*before* it can even attempt `Suministro.create()` -- that resolution is exactly what
`ClienteDirectory` and `CategoriaTarifariaDirectory` (domain/ports.py) are for.
"""

from dataclasses import dataclass, field

from energia.contexts.suministros.domain.ports import (
    CategoriaTarifariaDirectory,
    ClienteDirectory,
    SuministroConflictError,
    SuministroRepository,
    SuministroSource,
    SuministroSourceRecord,
)
from energia.contexts.suministros.domain.suministro import Suministro, SuministroValidationError

# Fields compared to decide whether an existing suministro's data actually changed. `id` is
# intentionally excluded: identity never changes across an update.
_COMPARABLE_FIELDS = (
    "cliente_id",
    "categoria_tarifaria_id",
    "localidad",
    "barrio",
    "estado",
    "fecha_alta",
)


@dataclass(frozen=True, slots=True)
class RejectedRecord:
    """A source record rejected during import -- for a missing/nonexistent cliente or categoria
    reference, or a violated `Suministro` invariant. The batch continues anyway."""

    record: SuministroSourceRecord
    reasons: list[str]


@dataclass(frozen=True, slots=True)
class ImportSummary:
    """Outcome of one `ImportSuministros.execute()` run."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: list[RejectedRecord] = field(default_factory=list)


class ImportSuministros:
    """Consume a `SuministroSource` and upsert every valid record by `numero_suministro`.

    Idempotent by design: each record is looked up by its natural key (`numero_suministro`)
    before deciding whether to create, update, or leave it alone, so re-running the exact same
    import reports `unchanged` counts instead of duplicate rows or spurious writes.
    """

    def __init__(
        self,
        repository: SuministroRepository,
        cliente_directory: ClienteDirectory,
        categoria_directory: CategoriaTarifariaDirectory,
    ) -> None:
        self._repository = repository
        self._cliente_directory = cliente_directory
        self._categoria_directory = categoria_directory

    async def execute(self, source: SuministroSource) -> ImportSummary:
        """Process every record sequentially against `self._repository`, in one unit of work.

        Neither this method nor `self._repository.save()` commits: the caller (the HTTP route
        today) commits once after `execute()` returns, so the whole batch is one all-or-nothing
        transaction from the database's point of view. Records are still rejected individually
        -- a missing/nonexistent `cliente`/`categoria_tarifaria` reference, a domain-invalid
        record (`SuministroValidationError`), or a record whose write hits a database integrity
        conflict (`SuministroConflictError`) is added to `rejected` and the loop continues; only
        an *unexpected* exception (anything else) propagates and aborts the whole batch
        uncommitted.

        Sequential per-record processing is also what makes a duplicate `numero_suministro`
        *within* the same payload safe: the second occurrence's `get_by_numero_suministro`
        lookup sees the first occurrence's already-`save()`d row (same transaction,
        read-your-own-write), so it upserts over it (`updated`/`unchanged`) instead of
        conflicting.
        """
        created = updated = unchanged = 0
        rejected: list[RejectedRecord] = []

        for record in source.fetch():
            numero_cliente = (record.numero_cliente or "").strip()
            if not numero_cliente:
                rejected.append(
                    RejectedRecord(record=record, reasons=["numero_cliente es obligatorio"])
                )
                continue

            cliente_id = await self._cliente_directory.resolve(numero_cliente)
            if cliente_id is None:
                rejected.append(
                    RejectedRecord(
                        record=record,
                        reasons=[f"cliente inexistente: numero_cliente={numero_cliente!r}"],
                    )
                )
                continue

            categoria_tarifaria = (record.categoria_tarifaria or "").strip()
            if not categoria_tarifaria:
                rejected.append(
                    RejectedRecord(record=record, reasons=["categoria_tarifaria es obligatoria"])
                )
                continue

            categoria_tarifaria_id = await self._categoria_directory.resolve(categoria_tarifaria)
            if categoria_tarifaria_id is None:
                rejected.append(
                    RejectedRecord(
                        record=record,
                        reasons=[f"categoria tarifaria inexistente: {categoria_tarifaria!r}"],
                    )
                )
                continue

            try:
                candidate = Suministro.create(
                    numero_suministro=record.numero_suministro,
                    cliente_id=cliente_id,
                    categoria_tarifaria_id=categoria_tarifaria_id,
                    fecha_alta=record.fecha_alta,
                    estado=record.estado,
                    localidad=record.localidad,
                    barrio=record.barrio,
                )
            except SuministroValidationError as error:
                rejected.append(RejectedRecord(record=record, reasons=error.errors))
                continue

            existing = await self._repository.get_by_numero_suministro(candidate.numero_suministro)
            if existing is None:
                to_save = candidate
                is_new = True
            elif _differs(existing, candidate):
                # Re-validated through Suministro.create() (not a raw replace()) so the merged
                # record honors every invariant too, keeping the existing row's identity (id).
                to_save = Suministro.create(
                    id=existing.id,
                    numero_suministro=candidate.numero_suministro,
                    cliente_id=candidate.cliente_id,
                    categoria_tarifaria_id=candidate.categoria_tarifaria_id,
                    fecha_alta=candidate.fecha_alta,
                    estado=candidate.estado,
                    localidad=candidate.localidad,
                    barrio=candidate.barrio,
                )
                is_new = False
            else:
                unchanged += 1
                continue

            try:
                await self._repository.save(to_save)
            except SuministroConflictError as error:
                rejected.append(RejectedRecord(record=record, reasons=[str(error)]))
                continue

            if is_new:
                created += 1
            else:
                updated += 1

        return ImportSummary(
            created=created, updated=updated, unchanged=unchanged, rejected=rejected
        )


def _differs(existing: Suministro, candidate: Suministro) -> bool:
    return any(
        getattr(existing, field_name) != getattr(candidate, field_name)
        for field_name in _COMPARABLE_FIELDS
    )
