"""Ports the `suministros` application layer depends on (ADR-001, Dependency Inversion; ADR-006,
modular-monolith cross-context lookups).

All are `Protocol`s: domain and application code depend only on these shapes, never on a
concrete infrastructure implementation. That is what lets `ImportSuministros` (application/
import_suministros.py) be unit-tested with plain in-memory fakes, and what lets a brand-new
adapter plug in later without touching this layer at all.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from energia.contexts.suministros.domain.suministro import Suministro


@dataclass(frozen=True, slots=True)
class SuministroSourceRecord:
    """One raw, not-yet-validated suministro record as it arrives from a `SuministroSource`.

    `numero_cliente` and `categoria_tarifaria` are natural keys, not UUIDs: turning them into
    `cliente_id`/`categoria_tarifaria_id` is `ImportSuministros`' job, via `ClienteDirectory` and
    `CategoriaTarifariaDirectory` below. Turning the rest of the record into a valid `Suministro`
    (or rejecting it) is `Suministro.create()`'s job, called once per record inside
    `ImportSuministros`. That is what lets one malformed record be rejected without aborting the
    rest of the batch.
    """

    numero_suministro: str | None
    numero_cliente: str | None
    categoria_tarifaria: str | None
    localidad: str | None = None
    barrio: str | None = None
    estado: str | None = None
    fecha_alta: str | None = None


class SuministroSource(Protocol):
    """Port for `ImportSuministros`'s input: "where do the suministro records to import come
    from". Mirrors `contexts.clientes.domain.ports.ClienteSource` exactly -- see that port's
    docstring for the adapters this enables now (`JsonSuministroSource`, the HTTP payload) and
    later (a file adapter, the eventual Oracle ETL adapter per ADR-004).
    """

    def fetch(self) -> Iterable[SuministroSourceRecord]:
        """Yield every suministro record to import, in no particular required order."""
        ...  # pragma: no cover — Protocol stub, never executed directly


class SuministroConflictError(Exception):
    """Raised by `SuministroRepository.save()` when persisting `suministro` violates a
    database-level integrity constraint despite already passing every `Suministro.create()`
    invariant -- e.g. an unforeseen natural-key conflict from a concurrent write this
    application could not detect ahead of time. `ImportSuministros` catches this per record so
    one conflicting write rejects only that record instead of aborting the rest of the batch.

    Infrastructure-defined exception types (e.g. `sqlalchemy.exc.IntegrityError`) never cross
    into `domain`/`application` (ADR-001): `SqlAlchemySuministroRepository.save()` translates
    them into this port-level exception instead.
    """


class SuministroRepository(Protocol):
    """Port for Suministro persistence — the application layer's only view of storage."""

    async def get_by_numero_suministro(self, numero_suministro: str) -> Suministro | None:
        """Return the (non-soft-deleted) suministro with this natural key, or None."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def get_most_recently_deleted_by_numero_suministro(
        self, numero_suministro: str
    ) -> Suministro | None:
        """Return the most recently soft-deleted suministro with this natural key (the greatest
        `deleted_at` among dead rows), or None if none is soft-deleted at all. Mirrors
        `contexts.clientes.domain.ports.ClienteRepository.get_most_recently_deleted_by_
        numero_cliente` exactly -- see that method's docstring for DECISION #9's full rationale.
        """
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def save(self, suministro: Suministro) -> None:
        """Insert or update `suministro`, keyed by its natural key (`numero_suministro`, scoped
        to non-soft-deleted rows — see `ImportSuministros` for how the create-vs-update decision
        is made). Does not commit: the caller (use case/route boundary) controls the
        transaction.

        Raises `SuministroConflictError` if the write violates a database integrity constraint;
        the underlying write is rolled back to a savepoint first, so the rest of the caller's
        transaction is unaffected.
        """
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def resurrect(self, suministro: Suministro) -> None:
        """Revive a soft-deleted row identified by `suministro.id`: clear `deleted_at` and write
        every mutable field from `suministro`, already merged onto that same identity by the
        caller (`ImportSuministros`). Mirrors `ClienteRepository.resurrect()` exactly -- see that
        method's docstring for why this cannot reuse `save()`'s `ON CONFLICT` upsert.

        Does not commit; raises `SuministroConflictError` on a database integrity conflict.
        """
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def list_active(
        self, *, limit: int, offset: int, cliente_id: UUID | None = None
    ) -> list[Suministro]:
        """Return non-soft-deleted suministros ordered by numero_suministro, page
        (limit/offset), optionally filtered to a single `cliente_id`."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def count_active(self, *, cliente_id: UUID | None = None) -> int:
        """Count non-soft-deleted suministros (for pagination metadata), optionally filtered to
        a single `cliente_id`."""
        ...  # pragma: no cover — Protocol stub, never executed directly


class ClienteDirectory(Protocol):
    """Cross-context lookup port (ADR-006 modular-monolith shortcut): resolves a `numero_cliente`
    natural key to that client's `id`, without `suministros` importing anything from
    `contexts.clientes` (ADR-001 module boundaries stay intact even for this same-database
    shortcut). The infrastructure implementation runs a direct SQL query against the `clientes`
    table instead of going through `contexts.clientes`' own repository/ORM model — see
    `contexts/README.md` ("cross-context directory-port pattern") for the full rationale and the
    convention this establishes for future contexts (lecturas, consumos, lotes, ...).
    """

    async def resolve(self, numero_cliente: str) -> UUID | None:
        """Return the `id` of the (non-soft-deleted) client with this natural key, or None."""
        ...  # pragma: no cover — Protocol stub, never executed directly


class CategoriaTarifariaDirectory(Protocol):
    """Lookup port for `categoria_tarifaria`'s natural key (`nombre`) -> `id`.

    Unlike `ClienteDirectory`, this is *not* a cross-context boundary: `CategoriaTarifaria`
    belongs to the same bounded context as `Suministro` (DOMAIN_MODEL.md §4.2, "Gestión de
    Suministros"), so its infrastructure implementation queries `categorias_tarifarias` directly
    through the ORM, exactly like `SuministroRepository` does for `suministros`. Kept as its own
    small port anyway (rather than folding it into `SuministroRepository`) purely so
    `ImportSuministros` stays unit-testable against a plain in-memory fake, the same reason
    `ClienteDirectory` is its own port.
    """

    async def resolve(self, nombre: str) -> UUID | None:
        """Return the `id` of the (non-soft-deleted) categoria tarifaria with this `nombre`, or
        None."""
        ...  # pragma: no cover — Protocol stub, never executed directly
