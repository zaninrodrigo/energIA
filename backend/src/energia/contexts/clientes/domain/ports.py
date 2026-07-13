"""Ports the `clientes` application layer depends on (ADR-001, Dependency Inversion).

Both are `Protocol`s: domain and application code depend only on these shapes, never on a
concrete infrastructure implementation. That is what lets `ImportClientes` (application/
import_clientes.py) be unit-tested with a plain in-memory fake, and what lets a brand-new
`ClienteSource` adapter plug in later without touching this layer at all.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from energia.contexts.clientes.domain.cliente import Cliente


@dataclass(frozen=True, slots=True)
class ClienteSourceRecord:
    """One raw, not-yet-validated client record as it arrives from a `ClienteSource`.

    Fields are deliberately untyped/optional at this stage: turning a record into a valid
    `Cliente` (or rejecting it) is `Cliente.create()`'s job, called once per record inside
    `ImportClientes`. That is what lets one malformed record be rejected without aborting the
    rest of the batch.
    """

    numero_cliente: str | None
    nombre: str | None
    estado: str | None = None
    documento: str | None = None
    localidad: str | None = None
    barrio: str | None = None
    direccion: dict[str, Any] | None = None


class ClienteSource(Protocol):
    """Port for `ImportClientes`'s input: "where do the client records to import come from".

    US-001 ("importar automáticamente los clientes desde Oracle") has no Oracle access yet
    (ADR-004: Oracle is a read-only source reached via ETL, someday). Today's only adapter is
    the HTTP request payload — a JSON array parsed by the presentation layer and wrapped by
    `infrastructure.json_cliente_source.JsonClienteSource`. Because `ImportClientes` depends on
    this `Protocol` and not on that concrete adapter, a file adapter (CSV/Excel upload) and,
    eventually, the real Oracle ETL adapter can plug in later by implementing this same port —
    with zero changes to domain or application code.
    """

    def fetch(self) -> Iterable[ClienteSourceRecord]:
        """Yield every client record to import, in no particular required order."""
        ...  # pragma: no cover — Protocol stub, never executed directly


class ClienteConflictError(Exception):
    """Raised by `ClienteRepository.save()` when persisting `cliente` violates a database-level
    integrity constraint despite already passing every `Cliente.create()` invariant — e.g. an
    unforeseen natural-key conflict from a concurrent write this application could not detect
    ahead of time. `ImportClientes` catches this per record so one conflicting write rejects
    only that record instead of aborting the rest of the batch.

    Infrastructure-defined exception types (e.g. `sqlalchemy.exc.IntegrityError`) never cross
    into `domain`/`application` (ADR-001): `SqlAlchemyClienteRepository.save()` translates them
    into this port-level exception instead.
    """


class ClienteRepository(Protocol):
    """Port for Cliente persistence — the application layer's only view of storage."""

    async def get_by_numero_cliente(self, numero_cliente: str) -> Cliente | None:
        """Return the (non-soft-deleted) client with this natural key, or None."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def get_most_recently_deleted_by_numero_cliente(
        self, numero_cliente: str
    ) -> Cliente | None:
        """Return the most recently soft-deleted client with this natural key (the one with the
        greatest `deleted_at` among dead rows), or None if none is soft-deleted at all.

        DECISION #9 (resurrection, confirmed by business 2026-07-13 — see
        `PROJECT_MASTER_SPEC.md`, resolved items): re-importing a soft-deleted `numero_cliente`
        revives this row instead of creating a brand-new identity. Only ever consulted by
        `ImportClientes` *after* `get_by_numero_cliente` already returned `None` — an active row
        always wins over any dead one. If more than one dead row shares this natural key (the
        client was imported, soft-deleted, re-imported, soft-deleted again, ...), older ones stay
        dead: only the latest is ever a resurrection candidate.
        """
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def save(self, cliente: Cliente) -> None:
        """Insert or update `cliente`, keyed by its natural key (`numero_cliente`, scoped to
        non-soft-deleted rows — see `ImportClientes` for how the create-vs-update decision is
        made). Does not commit: the caller (use case/route boundary) controls the transaction.

        Raises `ClienteConflictError` if the write violates a database integrity constraint;
        the underlying write is rolled back to a savepoint first, so the rest of the caller's
        transaction is unaffected.
        """
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def resurrect(self, cliente: Cliente) -> None:
        """Revive a soft-deleted row identified by `cliente.id` (the id returned by a prior
        `get_most_recently_deleted_by_numero_cliente` call): clear `deleted_at` and write every
        mutable field from `cliente` — already merged onto that same identity by the caller
        (`ImportClientes`), the same way a normal update is.

        Deliberately not the same code path as `save()`'s `INSERT ... ON CONFLICT`: a soft-deleted
        row is excluded from `uq_clientes_numero_cliente`'s partial index (`WHERE deleted_at IS
        NULL`), so that upsert's `ON CONFLICT` target cannot match it at all — inserting
        `cliente.id` again would collide with the primary key instead, a different, unhandled
        constraint. This method updates the dead row directly, by `id`.

        Does not commit: the caller (use case/route boundary) controls the transaction. Raises
        `ClienteConflictError` if the write violates a database integrity constraint (e.g. a race
        against a concurrent insert/resurrection claiming the same natural key in the meantime) —
        the underlying write is rolled back to a savepoint first, so the rest of the caller's
        transaction is unaffected, and the record is rejected instead of aborting the batch.
        """
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def list_active(self, *, limit: int, offset: int) -> list[Cliente]:
        """Return non-soft-deleted clients ordered by numero_cliente, page (limit/offset)."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def count_active(self) -> int:
        """Count non-soft-deleted clients (for pagination metadata)."""
        ...  # pragma: no cover — Protocol stub, never executed directly
