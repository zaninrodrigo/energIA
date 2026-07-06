"""ImportClientes: the US-001 use case — import clients from any ClienteSource, idempotently.

"Como Administrador, quiero importar automáticamente los clientes desde Oracle, para mantener
la información actualizada" (docs/02-requirements/USER_STORIES.md). There is no Oracle access
yet (ADR-004), so this use case depends on the `ClienteSource` port (domain/ports.py) rather
than on Oracle or any other concrete source — see that port's docstring for the adapters this
enables now and later.
"""

from dataclasses import dataclass, field

from energia.contexts.clientes.domain.cliente import Cliente, ClienteValidationError
from energia.contexts.clientes.domain.ports import (
    ClienteConflictError,
    ClienteRepository,
    ClienteSource,
    ClienteSourceRecord,
)

# Fields compared to decide whether an existing client's data actually changed. `id` is
# intentionally excluded: identity never changes across an update.
_COMPARABLE_FIELDS = ("nombre", "estado", "documento", "localidad", "barrio", "direccion")


@dataclass(frozen=True, slots=True)
class RejectedRecord:
    """A source record that failed one or more Cliente invariants; the batch continues anyway."""

    record: ClienteSourceRecord
    reasons: list[str]


@dataclass(frozen=True, slots=True)
class ImportSummary:
    """Outcome of one `ImportClientes.execute()` run."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: list[RejectedRecord] = field(default_factory=list)


class ImportClientes:
    """Consume a `ClienteSource` and upsert every valid record by `numero_cliente`.

    Idempotent by design: each record is looked up by its natural key (`numero_cliente`)
    before deciding whether to create, update, or leave it alone, so re-running the exact same
    import reports `unchanged` counts instead of duplicate rows or spurious writes.
    """

    def __init__(self, repository: ClienteRepository) -> None:
        self._repository = repository

    async def execute(self, source: ClienteSource) -> ImportSummary:
        """Process every record sequentially against `self._repository`, in one unit of work.

        Neither this method nor `self._repository.save()` commits: the caller (the HTTP route
        today) commits once after `execute()` returns, so the whole batch is one all-or-nothing
        transaction from the database's point of view. Records are still rejected
        individually — a domain-invalid record (`ClienteValidationError`) or a record whose
        write hits a database integrity conflict (`ClienteConflictError`, e.g. an unforeseen
        concurrent write) is added to `rejected` and the loop continues; only an *unexpected*
        exception (anything else) propagates and aborts the whole batch uncommitted.

        Sequential per-record processing is also what makes a duplicate `numero_cliente`
        *within* the same payload safe: the second occurrence's `get_by_numero_cliente` lookup
        sees the first occurrence's already-`save()`d row (same transaction, read-your-own-
        write), so it upserts over it (`updated`/`unchanged`) instead of conflicting.
        """
        created = updated = unchanged = 0
        rejected: list[RejectedRecord] = []

        for record in source.fetch():
            try:
                candidate = Cliente.create(
                    numero_cliente=record.numero_cliente,
                    nombre=record.nombre,
                    estado=record.estado,
                    documento=record.documento,
                    localidad=record.localidad,
                    barrio=record.barrio,
                    direccion=record.direccion,
                )
            except ClienteValidationError as error:
                rejected.append(RejectedRecord(record=record, reasons=error.errors))
                continue

            existing = await self._repository.get_by_numero_cliente(candidate.numero_cliente)
            if existing is None:
                to_save = candidate
                is_new = True
            elif _differs(existing, candidate):
                # Re-validated through Cliente.create() (not a raw replace()) so the merged
                # record honors every invariant too, keeping the existing row's identity (id).
                to_save = Cliente.create(
                    id=existing.id,
                    numero_cliente=candidate.numero_cliente,
                    nombre=candidate.nombre,
                    estado=candidate.estado.value,
                    documento=candidate.documento,
                    localidad=candidate.localidad,
                    barrio=candidate.barrio,
                    direccion=candidate.direccion,
                )
                is_new = False
            else:
                unchanged += 1
                continue

            try:
                await self._repository.save(to_save)
            except ClienteConflictError as error:
                rejected.append(RejectedRecord(record=record, reasons=[str(error)]))
                continue

            if is_new:
                created += 1
            else:
                updated += 1

        return ImportSummary(
            created=created, updated=updated, unchanged=unchanged, rejected=rejected
        )


def _differs(existing: Cliente, candidate: Cliente) -> bool:
    return any(
        getattr(existing, field_name) != getattr(candidate, field_name)
        for field_name in _COMPARABLE_FIELDS
    )
