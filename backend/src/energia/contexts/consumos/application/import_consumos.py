"""ImportConsumos: the US-004 use case -- import consumos históricos from any ConsumoSource,
idempotently, to train the AI model ("Como Administrador, quiero importar los consumos históricos,
para entrenar el modelo de IA", docs/02-requirements/USER_STORIES.md).

Mirrors `contexts.consumos.application.import_lecturas.ImportLecturas`'s overall shape, with two
genuine differences:

1. **Two resolution steps, not one.** `Consumo` references both a suministro (`numero_suministro`
   -> `suministro_id`, via `SuministroDirectory`, exactly as `ImportLecturas` already does) and a
   lote (`codigo_lote` -> `lote_id`, via the new `LoteDirectory`) before `Consumo.create()` can
   even be attempted. A third, optional resolution (`fecha_lectura` -> `lectura_id`, via
   `LecturaRepository.get_by_suministro_and_fecha`, reused directly -- no new port) only runs when
   the record actually provides a `fecha_lectura` at all.

2. **The update-merge path is a hybrid of `ImportLecturas`' and `ImportLotes`' patterns, but
   `consumo_promedio_diario` needs neither.** Every *required* field (`kwh`, `dias_facturados`,
   `fecha_inicio`, `fecha_fin`, `suministro_id`, `lote_id`) is always given fresh on every import
   (never omittable), so a `candidate` built via `Consumo.create()` is always fully re-validated
   (Lectura-style) -- there is no risk of losing a required field's stored value. Only
   `lectura_id` (resolved from `fecha_lectura`) needs the `UNSET`-aware preserve-vs-overwrite
   decision `ImportLotes` established (FIX 1) -- applied here via `dataclasses.replace()` on the
   already-validated `candidate`, not a from-scratch `Lote`-style `replace()` on `existing` (there
   is nothing else to protect the way `Lote.estado` is protected -- see `import_lotes.py`'s
   docstring for that contrast).

   **`consumo_promedio_diario` is deliberately given the opposite treatment (FIX 1 -- corrected a
   real bug here).** It is a DERIVED field, not an opaque one like `Lote.nombre`/
   `cantidad_registros`: `candidate.consumo_promedio_diario` is already the right value in every
   one of the three `UNSET`/`None`/value states by the time `Consumo.create()` returns it (see
   `domain/consumo.py`'s module docstring) -- omitted means recomputed from *this* record's own
   `kwh`/`dias_facturados` (always required, always fresh, always validated first), explicit
   `null` means `None`, an explicit value means that value, verbatim. Because those inputs are
   always present and validated, recompute-on-omission is always safe -- unlike
   `Lote.nombre`/`cantidad_registros`, which have no derived inputs of their own to recompute
   from and therefore *must* preserve `existing`'s stored value on omission (their only fallback
   otherwise is a domain default, `None`/`0`, that would silently wipe real data). Before this
   fix, the merge step here preserved `existing.consumo_promedio_diario` on omission the exact
   same way `ImportLotes` does for its opaque fields -- which left a stale average on the row
   whenever `kwh`/`dias_facturados` changed without repeating `consumo_promedio_diario` too.
   Reproduced case: `kwh` 100 -> 295 (same `dias_facturados`, 31), `consumo_promedio_diario`
   omitted on the re-import -- the correct recomputed average, 9.516, was silently replaced by
   whatever the *first* import had computed/stored (3.226), contradicting the row's own `kwh`.
"""

from dataclasses import dataclass, field, replace
from datetime import date

from energia.contexts.consumos.domain.consumo import Consumo, ConsumoValidationError
from energia.contexts.consumos.domain.ports import (
    ConsumoConflictError,
    ConsumoRepository,
    ConsumoSource,
    ConsumoSourceRecord,
    LecturaRepository,
    LoteDirectory,
    SuministroDirectory,
)
from energia.contexts.consumos.domain.sentinels import UnsetType

# Fields compared to decide whether an existing consumo's data actually changed. `id`,
# `suministro_id`, `fecha_inicio` and `fecha_fin` are intentionally excluded: the latter three form
# the identity a record is looked up by (never change across an update by construction); `id`
# never changes across an update either.
_COMPARABLE_FIELDS = ("lote_id", "lectura_id", "dias_facturados", "kwh", "consumo_promedio_diario")


@dataclass(frozen=True, slots=True)
class RejectedRecord:
    """A source record rejected during import -- for a missing/nonexistent suministro or lote
    reference, an unresolvable lectura reference, or a violated `Consumo` invariant. The batch
    continues anyway."""

    record: ConsumoSourceRecord
    reasons: list[str]


@dataclass(frozen=True, slots=True)
class ImportSummary:
    """Outcome of one `ImportConsumos.execute()` run."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: list[RejectedRecord] = field(default_factory=list)


class ImportConsumos:
    """Consume a `ConsumoSource` and upsert every valid record by `(suministro_id, fecha_inicio,
    fecha_fin)`.

    Idempotent by design: each record is looked up by its composite natural key before deciding
    whether to create, update, or leave it alone, so re-running the exact same import reports
    `unchanged` counts instead of duplicate rows or spurious writes.
    """

    def __init__(
        self,
        repository: ConsumoRepository,
        suministro_directory: SuministroDirectory,
        lote_directory: LoteDirectory,
        lectura_repository: LecturaRepository,
    ) -> None:
        self._repository = repository
        self._suministro_directory = suministro_directory
        self._lote_directory = lote_directory
        self._lectura_repository = lectura_repository

    async def execute(self, source: ConsumoSource) -> ImportSummary:
        """Process every record sequentially against `self._repository`, in one unit of work.

        Neither this method nor `self._repository.save()` commits: the caller (the HTTP route
        today) commits once after `execute()` returns, so the whole batch is one all-or-nothing
        transaction from the database's point of view. Records are still rejected individually --
        a missing/nonexistent `numero_suministro`/`codigo_lote` reference, an unresolvable
        `fecha_lectura`, a domain-invalid record (`ConsumoValidationError`), or a record whose
        write hits a database integrity conflict (`ConsumoConflictError`) is added to `rejected`
        and the loop continues; only an *unexpected* exception (anything else) propagates and
        aborts the whole batch uncommitted.

        Sequential per-record processing is also what makes a duplicate `(numero_suministro,
        fecha_inicio, fecha_fin)` *within* the same payload safe: the second occurrence's
        `get_by_suministro_and_periodo` lookup sees the first occurrence's already-`save()`d row
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

            codigo_lote = (record.codigo_lote or "").strip()
            if not codigo_lote:
                rejected.append(
                    RejectedRecord(record=record, reasons=["codigo_lote es obligatorio"])
                )
                continue

            lote_id = await self._lote_directory.resolve(codigo_lote)
            if lote_id is None:
                rejected.append(
                    RejectedRecord(
                        record=record, reasons=[f"lote inexistente: codigo_lote={codigo_lote!r}"]
                    )
                )
                continue

            raw_fecha_lectura = record.fecha_lectura
            if isinstance(raw_fecha_lectura, UnsetType) or raw_fecha_lectura is None:
                # Omitted (`UNSET`) or explicitly `null`: per the schema comment on
                # `consumos.lectura_id`, historical files may not carry lectura detail per
                # period -- both cases mean "no lectura for this record", not a rejection.
                lectura_id_for_create = None
            else:
                fecha_lectura_parsed = _parse_iso_date(raw_fecha_lectura)
                if fecha_lectura_parsed is None:
                    rejected.append(
                        RejectedRecord(
                            record=record,
                            reasons=[
                                f"fecha_lectura inválida: {raw_fecha_lectura!r} "
                                "(formato esperado: YYYY-MM-DD)"
                            ],
                        )
                    )
                    continue
                found_lectura = await self._lectura_repository.get_by_suministro_and_fecha(
                    suministro_id, fecha_lectura_parsed
                )
                if found_lectura is None:
                    rejected.append(
                        RejectedRecord(
                            record=record,
                            reasons=[
                                "lectura inexistente: "
                                f"numero_suministro={numero_suministro!r}, "
                                f"fecha_lectura={raw_fecha_lectura!r}"
                            ],
                        )
                    )
                    continue
                lectura_id_for_create = found_lectura.id

            try:
                candidate = Consumo.create(
                    suministro_id=suministro_id,
                    lote_id=lote_id,
                    lectura_id=lectura_id_for_create,
                    fecha_inicio=record.fecha_inicio,
                    fecha_fin=record.fecha_fin,
                    dias_facturados=record.dias_facturados,
                    kwh=record.kwh,
                    consumo_promedio_diario=record.consumo_promedio_diario,
                )
            except ConsumoValidationError as error:
                rejected.append(RejectedRecord(record=record, reasons=error.errors))
                continue

            existing = await self._repository.get_by_suministro_and_periodo(
                candidate.suministro_id, candidate.fecha_inicio, candidate.fecha_fin
            )
            if existing is None:
                to_save = candidate
                is_new = True
            else:
                # `lectura_id`: a field left `UNSET` (genuinely absent from `record`) preserves
                # `existing`'s already-stored value instead of being overwritten by `candidate`'s
                # -- see this module's docstring for why only `lectura_id` needs this.
                merged_lectura_id = (
                    existing.lectura_id
                    if isinstance(record.fecha_lectura, UnsetType)
                    else candidate.lectura_id
                )
                # `consumo_promedio_diario` deliberately does NOT get the same preserve-on-`UNSET`
                # treatment (FIX 1): `candidate.consumo_promedio_diario` is already correct in all
                # three states (recomputed from THIS record's own `kwh`/`dias_facturados` when
                # omitted, `None` when explicit `null`, given value when explicit) -- see this
                # module's docstring and `domain/consumo.py`'s. Taken from `candidate` as-is,
                # never `existing`'s.
                merged = replace(candidate, id=existing.id, lectura_id=merged_lectura_id)
                if _differs(existing, merged):
                    to_save = merged
                    is_new = False
                else:
                    unchanged += 1
                    continue

            try:
                await self._repository.save(to_save)
            except ConsumoConflictError as error:
                rejected.append(RejectedRecord(record=record, reasons=[str(error)]))
                continue

            if is_new:
                created += 1
            else:
                updated += 1

        return ImportSummary(
            created=created, updated=updated, unchanged=unchanged, rejected=rejected
        )


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _differs(existing: Consumo, candidate: Consumo) -> bool:
    return any(
        getattr(existing, field_name) != getattr(candidate, field_name)
        for field_name in _COMPARABLE_FIELDS
    )
