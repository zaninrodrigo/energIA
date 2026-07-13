"""Ports the `consumos` application layer depends on (ADR-001, Dependency Inversion; ADR-006,
modular-monolith cross-context lookups).

All are `Protocol`s: domain and application code depend only on these shapes, never on a
concrete infrastructure implementation. That is what lets `ImportLecturas` (application/
import_lecturas.py) be unit-tested with plain in-memory fakes, and what lets a brand-new adapter
plug in later without touching this layer at all. Mirrors `contexts/suministros/domain/ports.py`
exactly.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

from energia.contexts.consumos.domain.lectura import Lectura


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
