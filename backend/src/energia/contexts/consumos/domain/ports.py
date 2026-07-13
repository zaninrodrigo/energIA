"""Ports the `consumos` application layer depends on (ADR-001, Dependency Inversion; ADR-006,
modular-monolith cross-context lookups).

All are `Protocol`s: domain and application code depend only on these shapes, never on a
concrete infrastructure implementation. That is what lets `ImportLecturas` (application/
import_lecturas.py) be unit-tested with plain in-memory fakes, and what lets a brand-new adapter
plug in later without touching this layer at all. Mirrors `contexts/suministros/domain/ports.py`
exactly.

`Lote`'s ports (bottom of this file) follow the same shape as `Lectura`'s, minus a source/
directory port for any foreign key: `lotes` references no other table, so `ImportLotes` (US-005)
needs only a `LoteSource` and a `LoteRepository`, no cross-context (or same-context) resolution
step at all.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

from energia.contexts.consumos.domain.lectura import Lectura
from energia.contexts.consumos.domain.lote import EstadoLote, Lote


class UnsetType:
    """Sentinel type for a `LoteSourceRecord` field genuinely *absent* from the source payload --
    see that dataclass's docstring for why this must be distinguishable from an explicit `None`.
    A dedicated, public type (not a bare `object()`, and not name-mangled private) so it is
    nameable both in type hints and in `isinstance()` checks: callers narrow with
    `isinstance(value, UnsetType)` rather than `value is UNSET` -- mypy narrows a `Union` type on
    `isinstance()` but not on an identity (`is`) check against an arbitrary object instance, only
    against `None`/literals/enum members. Still only ever one instance in practice (the singleton
    `UNSET` below); `isinstance()` is used here purely for mypy's benefit, not because more than
    one instance could ever meaningfully exist.
    """

    def __repr__(self) -> str:
        return "UNSET"

    def __deepcopy__(self, memo: dict[int, object]) -> "UnsetType":
        # `dataclasses.asdict()` (used by `presentation/routes.py` to echo a rejected record back
        # in the HTTP response body) deep-copies every field value by default; without this, the
        # singleton identity `value is UNSET` checks (still used where only a boolean "was this
        # provided" comparison is needed, not narrowing) would not survive that copy.
        return self


UNSET = UnsetType()


@dataclass(frozen=True, slots=True)
class LecturaSourceRecord:
    """One raw, not-yet-validated lectura record as it arrives from a `LecturaSource`.

    `numero_suministro` is a natural key, not a UUID: turning it into `suministro_id` is
    `ImportLecturas`' job, via `SuministroDirectory` below. Turning the rest of the record into a
    valid `Lectura` (or rejecting it) is `Lectura.create()`'s job, called once per record inside
    `ImportLecturas`. That is what lets one malformed record be rejected without aborting the
    rest of the batch.
    """

    numero_suministro: str | None
    fecha_lectura: str | None
    lectura_anterior: float | int | str | None = None
    lectura_actual: float | int | str | None = None
    dias_facturados: int | str | None = None


class LecturaSource(Protocol):
    """Port for `ImportLecturas`'s input: "where do the lectura records to import come from".
    Mirrors `contexts.suministros.domain.ports.SuministroSource` exactly -- see that port's
    docstring for the adapters this enables now (`JsonLecturaSource`, the HTTP payload) and later
    (a file adapter, the eventual Oracle ETL adapter per ADR-004).
    """

    def fetch(self) -> Iterable[LecturaSourceRecord]:
        """Yield every lectura record to import, in no particular required order."""
        ...  # pragma: no cover — Protocol stub, never executed directly


class LecturaConflictError(Exception):
    """Raised by `LecturaRepository.save()` when persisting `lectura` violates a database-level
    integrity constraint despite already passing every `Lectura.create()` invariant -- e.g. an
    unforeseen natural-key conflict from a concurrent write this application could not detect
    ahead of time. `ImportLecturas` catches this per record so one conflicting write rejects only
    that record instead of aborting the rest of the batch.

    Infrastructure-defined exception types (e.g. `sqlalchemy.exc.IntegrityError`) never cross
    into `domain`/`application` (ADR-001): `SqlAlchemyLecturaRepository.save()` translates them
    into this port-level exception instead.
    """


class LecturaRepository(Protocol):
    """Port for Lectura persistence — the application layer's only view of storage."""

    async def get_by_suministro_and_fecha(
        self, suministro_id: UUID, fecha_lectura: date
    ) -> Lectura | None:
        """Return the (non-soft-deleted) lectura for this `(suministro_id, fecha_lectura)`
        composite natural key, or None."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def save(self, lectura: Lectura) -> None:
        """Insert or update `lectura`, keyed by its composite natural key `(suministro_id,
        fecha_lectura)`, scoped to non-soft-deleted rows — see `ImportLecturas` for how the
        create-vs-update decision is made. Does not commit: the caller (use case/route boundary)
        controls the transaction.

        Raises `LecturaConflictError` if the write violates a database integrity constraint; the
        underlying write is rolled back to a savepoint first, so the rest of the caller's
        transaction is unaffected.
        """
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def list_active(
        self, *, limit: int, offset: int, suministro_id: UUID | None = None
    ) -> list[Lectura]:
        """Return non-soft-deleted lecturas ordered by `(fecha_lectura, id)`, page
        (limit/offset), optionally filtered to a single `suministro_id`."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def count_active(self, *, suministro_id: UUID | None = None) -> int:
        """Count non-soft-deleted lecturas (for pagination metadata), optionally filtered to a
        single `suministro_id`."""
        ...  # pragma: no cover — Protocol stub, never executed directly


class SuministroDirectory(Protocol):
    """Cross-context lookup port (ADR-006 modular-monolith shortcut): resolves a
    `numero_suministro` natural key to that suministro's `id`, without `consumos` importing
    anything from `contexts.suministros` (ADR-001 module boundaries stay intact even for this
    same-database shortcut). The infrastructure implementation runs a direct SQL query against
    the `suministros` table instead of going through `contexts.suministros`' own repository/ORM
    model — see `contexts/README.md` ("cross-context directory-port pattern"), which this
    context follows exactly, the same way `suministros` already did for `ClienteDirectory`.
    """

    async def resolve(self, numero_suministro: str) -> UUID | None:
        """Return the `id` of the (non-soft-deleted) suministro with this natural key, or
        None."""
        ...  # pragma: no cover — Protocol stub, never executed directly


@dataclass(frozen=True, slots=True)
class LoteSourceRecord:
    """One raw, not-yet-validated lote record as it arrives from a `LoteSource`.

    No natural-key foreign key to resolve here (unlike `LecturaSourceRecord`'s
    `numero_suministro`): `lotes` references no other table, so this record only ever needs
    `Lote.create()`'s own validation, no directory-port lookup first. There is deliberately no
    `estado` field -- see `domain/lote.py`'s module docstring.

    **`nombre`/`cantidad_registros` are a three-state field, not two** (FIX 1): each can be
    `UNSET` (this field was genuinely absent from the source payload), `None` (the payload
    explicitly sent a null for it), or an actual value -- and `ImportLotes` (application/
    import_lotes.py) treats those three states differently on an update: `UNSET` preserves
    whatever `existing` already has stored, while `None` and a real value both overwrite it (a
    real record-level rejection, for `cantidad_registros`'s `None` case -- see that module).
    Collapsing `UNSET` and `None` into the same value (as a plain `... | None = None` default
    used to) is exactly the bug FIX 1 fixes: re-importing an existing `codigo_lote` with a field
    merely *omitted* from the payload (e.g. a client only ever sends `codigo_lote` on
    re-imports) would wipe that field to its domain default instead of leaving the stored value
    alone, because nothing distinguished "the client didn't say" from "the client said none".
    `codigo_lote` has no such ambiguity: it is always required, so there is no legitimate
    "omitted" state to preserve anything against.
    """

    codigo_lote: str | None
    nombre: str | None | UnsetType = UNSET
    cantidad_registros: int | str | None | UnsetType = UNSET


class LoteSource(Protocol):
    """Port for `ImportLotes`'s input: "where do the lote records to import come from". Mirrors
    `LecturaSource` exactly -- see that port's docstring for the adapters this enables now
    (`JsonLoteSource`, the HTTP payload) and later (a file adapter, the eventual Oracle ETL
    adapter per ADR-004).
    """

    def fetch(self) -> Iterable[LoteSourceRecord]:
        """Yield every lote record to import, in no particular required order."""
        ...  # pragma: no cover — Protocol stub, never executed directly


class LoteConflictError(Exception):
    """Raised by `LoteRepository.save()` when persisting `lote` violates a database-level
    integrity constraint despite already passing every `Lote.create()` invariant. Mirrors
    `LecturaConflictError` exactly -- see that exception's docstring.
    """


class LoteRepository(Protocol):
    """Port for Lote persistence — the application layer's only view of storage."""

    async def get_by_codigo_lote(self, codigo_lote: str) -> Lote | None:
        """Return the (non-soft-deleted) lote for this `codigo_lote` natural key, or None."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def save(self, lote: Lote) -> None:
        """Insert or update `lote`, keyed by its natural key `codigo_lote`, scoped to
        non-soft-deleted rows — see `ImportLotes` for how the create-vs-update decision is made.
        Does not commit: the caller (route boundary) controls the transaction.

        Raises `LoteConflictError` if the write violates a database integrity constraint; the
        underlying write is rolled back to a savepoint first, so the rest of the caller's
        transaction is unaffected.
        """
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def list_active(
        self, *, limit: int, offset: int, estado: EstadoLote | None = None
    ) -> list[Lote]:
        """Return non-soft-deleted lotes ordered by `(fecha_importacion desc, id)` — most
        recently imported first, see `presentation/routes.py`'s `list_lotes` for why — page
        (limit/offset), optionally filtered to a single `estado`."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def count_active(self, *, estado: EstadoLote | None = None) -> int:
        """Count non-soft-deleted lotes (for pagination metadata), optionally filtered to a
        single `estado`."""
        ...  # pragma: no cover — Protocol stub, never executed directly
