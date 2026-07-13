"""ImportLotes: the US-005 use case -- import lotes de facturación from any LoteSource,
idempotently.

"Como Administrador, quiero importar nuevos lotes de facturación, para procesar automáticamente
cada período" (docs/02-requirements/USER_STORIES.md). Mirrors `contexts.consumos.application.
import_lecturas.ImportLecturas`'s overall shape, with three genuine differences:

1. No directory-port resolution step at all. `lotes` references no other table, so there is no
   natural key to resolve to a UUID before `Lote.create()` can even be attempted -- unlike
   `ImportLecturas`/`ImportSuministros`, which both depend on a `<Entity>Directory` port.

2. The update-merge path uses `dataclasses.replace()`, not `Lote.create()` again. `Lectura`/
   `Suministro` re-validate their merged record through `create()` on update (see
   `import_lecturas.py`'s docstring) because `create()` can express "keep every field, including
   the ones that came from the existing row". `Lote.create()` cannot: it has no `estado`
   parameter at all (see `domain/lote.py`), and always returns `EstadoLote.PENDIENTE`. Routing the
   merge through `create()` would therefore silently reset an existing `Procesado` (or
   `Procesando`/`Error`) lote back to `Pendiente` on every re-import -- exactly the RD-010
   violation `Lote.create()`'s missing `estado` parameter exists to prevent. `dataclasses.
   replace()` sidesteps this: it starts from the *existing*, already-persisted `Lote` (whose
   `estado` and `fecha_importacion` are already trusted) and only overrides the fields an import
   is actually allowed to change (`nombre`, `cantidad_registros`), so neither ever needs
   re-validation -- both already went through `Lote.create()`'s invariants when the *candidate*
   was built.

3. **FIX 1 -- `nombre`/`cantidad_registros` genuinely omitted from a record must be preserved on
   update, not wiped.** `LoteSourceRecord.nombre`/`cantidad_registros` are `UNSET` (`domain/
   ports.py`) when the source payload never mentioned that field at all, distinct from an
   explicit `None`. On an update, a field left `UNSET` keeps `existing`'s stored value; only an
   explicitly-provided value (including an explicit `None` for `nombre`) overrides it. Before
   this fix, an omitted field defaulted through `Lote.create()` (`None`/`0`) exactly like an
   explicit one, so `dataclasses.replace(existing, nombre=candidate.nombre, ...)` always
   overwrote `existing`'s value with that domain default -- silently wiping `nombre`/
   `cantidad_registros` on every re-import that did not repeat them. `cantidad_registros` has one
   more wrinkle `nombre` does not: it is a `NOT NULL` column with its own `DEFAULT 0`, so an
   *explicit* `None` (as opposed to `UNSET`) is not a value it can ever hold -- rejected outright
   as a per-record violation instead of being silently treated the same as an omission.
"""

from dataclasses import dataclass, field, replace

from energia.contexts.consumos.domain.lote import Lote, LoteValidationError
from energia.contexts.consumos.domain.ports import (
    UNSET,
    LoteConflictError,
    LoteRepository,
    LoteSource,
    LoteSourceRecord,
    UnsetType,
)

# Fields compared to decide whether an existing lote's data actually changed. `estado` and
# `fecha_importacion` are intentionally excluded -- neither is ever touched by an update (see this
# module's docstring); `id` and `codigo_lote` never change across an update either.
_COMPARABLE_FIELDS = ("nombre", "cantidad_registros")


@dataclass(frozen=True, slots=True)
class RejectedRecord:
    """A source record rejected during import -- for a violated `Lote` invariant. The batch
    continues anyway."""

    record: LoteSourceRecord
    reasons: list[str]


@dataclass(frozen=True, slots=True)
class ImportSummary:
    """Outcome of one `ImportLotes.execute()` run."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: list[RejectedRecord] = field(default_factory=list)


class ImportLotes:
    """Consume a `LoteSource` and upsert every valid record by `codigo_lote`.

    Idempotent by design: each record is looked up by its natural key (`codigo_lote`) before
    deciding whether to create, update, or leave it alone, so re-running the exact same import
    reports `unchanged` counts instead of duplicate rows or spurious writes. Re-running an import
    also never touches an existing lote's `estado` -- see this module's docstring.
    """

    def __init__(self, repository: LoteRepository) -> None:
        self._repository = repository

    async def execute(self, source: LoteSource) -> ImportSummary:
        """Process every record sequentially against `self._repository`, in one unit of work.

        Neither this method nor `self._repository.save()` commits: the caller (the HTTP route
        today) commits once after `execute()` returns, so the whole batch is one all-or-nothing
        transaction from the database's point of view. Records are still rejected individually
        -- a domain-invalid record (`LoteValidationError`) or a record whose write hits a
        database integrity conflict (`LoteConflictError`) is added to `rejected` and the loop
        continues; only an *unexpected* exception (anything else) propagates and aborts the whole
        batch uncommitted.

        Sequential per-record processing is also what makes a duplicate `codigo_lote` *within*
        the same payload safe: the second occurrence's `get_by_codigo_lote` lookup sees the first
        occurrence's already-`save()`d row (same transaction, read-your-own-write), so it upserts
        over it (`updated`/`unchanged`) instead of conflicting.
        """
        created = updated = unchanged = 0
        rejected: list[RejectedRecord] = []

        for record in source.fetch():
            if record.cantidad_registros is None:
                # Explicit `null`, not omission (`UNSET`): `cantidad_registros` is a `NOT NULL`
                # column with its own `DEFAULT 0` -- an omitted field legitimately means "apply
                # that default on create / leave the existing value alone on update" (handled
                # below), but nothing backing this column can ever actually be null. Rejected
                # outright instead of silently defaulting it the same way an omission does,
                # which would mask a genuine client mistake (FIX 1).
                rejected.append(
                    RejectedRecord(
                        record=record,
                        reasons=["cantidad_registros no puede ser null explícito"],
                    )
                )
                continue

            # `UNSET` -> `None`: `Lote.create()` only knows the two-state `None` ("apply the
            # domain default"), not the three-state `UNSET`/`None`/value `LoteSourceRecord`
            # carries -- collapsing `UNSET` to `None` here is exactly what a from-scratch create
            # wants (the "apply defaults" half of FIX 1). The update-merge step below still
            # checks `record.nombre`/`record.cantidad_registros` directly (not `candidate`'s
            # already-collapsed fields) to tell an omission apart from an explicit value.
            # `isinstance()`, not `is UNSET`: mypy narrows a `Union` on an `isinstance()` check,
            # but not on an identity check against an arbitrary object instance (only against
            # `None`/literals/enum members) -- see `UnsetType`'s docstring (`domain/ports.py`).
            raw_nombre = record.nombre
            nombre_for_create: str | None = (
                None if isinstance(raw_nombre, UnsetType) else raw_nombre
            )

            raw_cantidad_registros = record.cantidad_registros
            cantidad_registros_for_create: int | str | None = (
                None if isinstance(raw_cantidad_registros, UnsetType) else raw_cantidad_registros
            )

            try:
                candidate = Lote.create(
                    codigo_lote=record.codigo_lote,
                    nombre=nombre_for_create,
                    cantidad_registros=cantidad_registros_for_create,
                )
            except LoteValidationError as error:
                rejected.append(RejectedRecord(record=record, reasons=error.errors))
                continue

            existing = await self._repository.get_by_codigo_lote(candidate.codigo_lote)
            if existing is None:
                to_save = candidate
                is_new = True
            else:
                # A field left `UNSET` (genuinely absent from `record`) preserves `existing`'s
                # already-stored value instead of being overwritten by `candidate`'s -- for an
                # omitted field, `candidate`'s value is only ever the domain default (`None`/
                # `0`), never real data to merge in (FIX 1). `replace()`, not `Lote.create()`
                # again -- see this module's docstring for why: carries `existing`'s `id`,
                # `fecha_importacion` and (crucially) `estado` forward unchanged, overriding
                # only the two fields an import may update.
                merged_nombre = existing.nombre if record.nombre is UNSET else candidate.nombre
                merged_cantidad_registros = (
                    existing.cantidad_registros
                    if record.cantidad_registros is UNSET
                    else candidate.cantidad_registros
                )
                merged = replace(
                    existing, nombre=merged_nombre, cantidad_registros=merged_cantidad_registros
                )
                if _differs(existing, merged):
                    to_save = merged
                    is_new = False
                else:
                    unchanged += 1
                    continue

            try:
                await self._repository.save(to_save)
            except LoteConflictError as error:
                rejected.append(RejectedRecord(record=record, reasons=[str(error)]))
                continue

            if is_new:
                created += 1
            else:
                updated += 1

        return ImportSummary(
            created=created, updated=updated, unchanged=unchanged, rejected=rejected
        )


def _differs(existing: Lote, candidate: Lote) -> bool:
    return any(
        getattr(existing, field_name) != getattr(candidate, field_name)
        for field_name in _COMPARABLE_FIELDS
    )
