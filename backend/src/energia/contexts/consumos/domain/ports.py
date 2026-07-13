"""Ports the `consumos` application layer depends on (ADR-001, Dependency Inversion; ADR-006,
modular-monolith cross-context lookups).

All are `Protocol`s: domain and application code depend only on these shapes, never on a
concrete infrastructure implementation. That is what lets `ImportLecturas` (application/
import_lecturas.py) be unit-tested with plain in-memory fakes, and what lets a brand-new adapter
plug in later without touching this layer at all. Mirrors `contexts/suministros/domain/ports.py`
exactly.

`Lote`'s ports (middle of this file) follow the same shape as `Lectura`'s, minus a source/
directory port for any foreign key: `lotes` references no other table, so `ImportLotes` (US-005)
needs only a `LoteSource` and a `LoteRepository`, no cross-context (or same-context) resolution
step at all.

`Consumo`'s ports (bottom of this file, US-004) need *two* resolution steps `ImportConsumos` must
run before `Consumo.create()` can even be attempted: `numero_suministro` -> `suministro_id` (the
existing `SuministroDirectory` above, a cross-context lookup, reused unchanged) and `codigo_lote`
-> `lote_id` (the new `LoteDirectory` below). `LoteDirectory` is a *same-context* lookup --
`Lote` lives in this same `consumos` package -- so, per `contexts/README.md`'s "cross-context
directory-port pattern" ("when a directory port is *not* needed"), it is implemented with an
ordinary ORM query (`SqlAlchemyLoteDirectory`, infrastructure/lote_directory.py) against
`LoteModel` (already mapped in infrastructure/models.py), the same way `CategoriaTarifariaDirectory`
is for `suministros`/`CategoriaTarifaria` -- not a `sqlalchemy.text(...)` raw query like
`SuministroDirectory`. It is still its own small port (not folded into `LoteRepository`) purely so
`ImportConsumos` stays unit-testable against a plain fake, exactly `contexts/README.md`'s stated
reason for `CategoriaTarifariaDirectory`'s own separation from `SuministroRepository`.

A third resolution, `fecha_lectura` -> `lectura_id`, needs no new port at all: it is resolved via
the existing `LecturaRepository.get_by_suministro_and_fecha` (also same-context), reused directly
by `ImportConsumos` the same way `ImportLecturas` itself uses it.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

from energia.contexts.consumos.domain.consumo import Consumo
from energia.contexts.consumos.domain.lectura import Lectura
from energia.contexts.consumos.domain.lote import EstadoLote, Lote

# Re-exported for backward compatibility: every existing call site
# (`application/import_lotes.py`, `presentation/routes.py`) imports these two names from
# `domain.ports`, not from `domain.sentinels` directly -- see `sentinels.py`'s module docstring
# for why the sentinel itself now lives there instead of here.
from energia.contexts.consumos.domain.sentinels import UNSET, UnsetType

__all__ = [
    "UNSET",
    "UnsetType",
    "LecturaSourceRecord",
    "LecturaSource",
    "LecturaConflictError",
    "LecturaRepository",
    "SuministroDirectory",
    "LoteSourceRecord",
    "LoteSource",
    "LoteConflictError",
    "LoteRepository",
    "LoteDirectory",
    "ConsumoSourceRecord",
    "ConsumoSource",
    "ConsumoConflictError",
    "ConsumoRepository",
]


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

    async def get_most_recently_deleted_by_suministro_and_fecha(
        self, suministro_id: UUID, fecha_lectura: date
    ) -> Lectura | None:
        """Return the most recently soft-deleted lectura for this composite natural key (the
        greatest `deleted_at` among dead rows), or None if none is soft-deleted at all. Mirrors
        `contexts.clientes.domain.ports.ClienteRepository.get_most_recently_deleted_by_
        numero_cliente` exactly -- see that method's docstring for DECISION #9's full rationale.
        """
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

    async def resurrect(self, lectura: Lectura) -> None:
        """Revive a soft-deleted row identified by `lectura.id`: clear `deleted_at` and write
        every mutable field from `lectura`, already merged onto that same identity by the caller
        (`ImportLecturas`). Mirrors `ClienteRepository.resurrect()` exactly -- see that method's
        docstring for why this cannot reuse `save()`'s `ON CONFLICT` upsert.

        Does not commit; raises `LecturaConflictError` on a database integrity conflict.
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

    async def get_most_recently_deleted_by_codigo_lote(self, codigo_lote: str) -> Lote | None:
        """Return the most recently soft-deleted lote with this natural key (the greatest
        `deleted_at` among dead rows), or None if none is soft-deleted at all. Mirrors
        `contexts.clientes.domain.ports.ClienteRepository.get_most_recently_deleted_by_
        numero_cliente` exactly -- see that method's docstring for DECISION #9's full rationale.
        """
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

    async def resurrect(self, lote: Lote) -> None:
        """Revive a soft-deleted row identified by `lote.id`: clear `deleted_at` and write
        `nombre`/`cantidad_registros` from `lote`, already merged onto that same identity by the
        caller (`ImportLotes`). Mirrors `ClienteRepository.resurrect()` for the "update by id, not
        `ON CONFLICT`" rationale, with one addition of its own: exactly like `save()`, `estado`
        and `fecha_importacion` are never written by this method either — a resurrected lote
        keeps the `estado` it had when it was soft-deleted (RD-010's intent extends to
        resurrection: reviving a row must not silently reset its processing state any more than
        an ordinary update may).

        Does not commit; raises `LoteConflictError` on a database integrity conflict.
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


class LoteDirectory(Protocol):
    """Same-context lookup port (see this module's docstring, and `contexts/README.md`'s "when a
    directory port is *not* needed"): resolves a `codigo_lote` natural key to that lote's `id`,
    via an ordinary ORM query -- `Lote` and `Consumo` both belong to `consumos`, so there is no
    cross-context boundary to route around here, unlike `SuministroDirectory`.
    """

    async def resolve(self, codigo_lote: str) -> UUID | None:
        """Return the `id` of the (non-soft-deleted) lote with this natural key, or None. "Active"
        means non-soft-deleted only (`deleted_at IS NULL`), the same meaning `SuministroDirectory`
        uses -- not filtered by `Lote.estado` (`Pendiente`/`Procesando`/`Procesado`/`Error`): a
        `Consumo` may reference a lote in any processing state, the same way the `fk_consumos_lote`
        foreign key itself places no restriction on `estado`.
        """
        ...  # pragma: no cover — Protocol stub, never executed directly


@dataclass(frozen=True, slots=True)
class ConsumoSourceRecord:
    """One raw, not-yet-validated consumo record as it arrives from a `ConsumoSource`.

    `numero_suministro`/`codigo_lote` are natural keys, not UUIDs: resolving them to
    `suministro_id`/`lote_id` is `ImportConsumos`' job, via `SuministroDirectory`/`LoteDirectory`
    below. Turning the rest of the record into a valid `Consumo` (or rejecting it) is
    `Consumo.create()`'s job, called once per record inside `ImportConsumos` -- exactly
    `LecturaSourceRecord`'s shape, extended with a second natural-key reference.

    `consumo_promedio_diario`/`fecha_lectura` are three-state fields (FIX 1's pattern,
    `LoteSourceRecord` above), not two: each can be `UNSET` (genuinely absent from the payload),
    `None` (explicitly sent as `null`), or an actual value -- `Consumo.create()` and
    `ImportConsumos` treat those three states differently, both on create and on update. See
    `domain/consumo.py`'s
    module docstring for `consumo_promedio_diario`'s full rationale (it is the first field in this
    codebase where `create()` itself, not just the update-merge step, needs the distinction: an
    omitted value is *computed* -- DOMAIN_MODEL.md §7.6's `calcularPromedioDiario()` -- while an
    explicit `null` is *stored as null*, skipping computation). `fecha_lectura` mirrors the same
    three states for consistency, resolved by `ImportConsumos` via `LecturaRepository` instead of
    validated inside `Consumo.create()`: `UNSET` leaves `lectura_id` alone on an update (or unset on
    a create), an explicit `null` clears it, and a given date is resolved to a `lectura_id` or the
    whole record is rejected ("lectura inexistente") if it does not resolve.
    """

    numero_suministro: str | None
    codigo_lote: str | None
    fecha_inicio: str | None
    fecha_fin: str | None
    dias_facturados: int | str | None = None
    kwh: float | int | str | None = None
    consumo_promedio_diario: float | int | str | None | UnsetType = UNSET
    fecha_lectura: str | None | UnsetType = UNSET


class ConsumoSource(Protocol):
    """Port for `ImportConsumos`'s input: "where do the consumo records to import come from".
    Mirrors `LecturaSource`/`LoteSource` exactly -- see `LecturaSource`'s docstring for the
    adapters this enables now (`JsonConsumoSource`, the HTTP payload) and later (a file adapter,
    the eventual Oracle ETL adapter per ADR-004).
    """

    def fetch(self) -> Iterable[ConsumoSourceRecord]:
        """Yield every consumo record to import, in no particular required order."""
        ...  # pragma: no cover — Protocol stub, never executed directly


class ConsumoConflictError(Exception):
    """Raised by `ConsumoRepository.save()` when persisting `consumo` violates a database-level
    integrity constraint despite already passing every `Consumo.create()` invariant. Mirrors
    `LecturaConflictError`/`LoteConflictError` exactly -- see `LecturaConflictError`'s docstring.
    """


class ConsumoRepository(Protocol):
    """Port for Consumo persistence — the application layer's only view of storage."""

    async def get_by_suministro_and_periodo(
        self, suministro_id: UUID, fecha_inicio: date, fecha_fin: date
    ) -> Consumo | None:
        """Return the (non-soft-deleted) consumo for this `(suministro_id, fecha_inicio,
        fecha_fin)` composite natural key, or None."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def get_most_recently_deleted_by_suministro_and_periodo(
        self, suministro_id: UUID, fecha_inicio: date, fecha_fin: date
    ) -> Consumo | None:
        """Return the most recently soft-deleted consumo for this composite natural key (the
        greatest `deleted_at` among dead rows), or None if none is soft-deleted at all. Mirrors
        `contexts.clientes.domain.ports.ClienteRepository.get_most_recently_deleted_by_
        numero_cliente` exactly -- see that method's docstring for DECISION #9's full rationale.
        """
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def save(self, consumo: Consumo) -> None:
        """Insert or update `consumo`, keyed by its composite natural key `(suministro_id,
        fecha_inicio, fecha_fin)`, scoped to non-soft-deleted rows — see `ImportConsumos` for how
        the create-vs-update decision is made. Does not commit: the caller (route boundary)
        controls the transaction.

        Raises `ConsumoConflictError` if the write violates a database integrity constraint; the
        underlying write is rolled back to a savepoint first, so the rest of the caller's
        transaction is unaffected.
        """
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def resurrect(self, consumo: Consumo) -> None:
        """Revive a soft-deleted row identified by `(consumo.id, consumo.fecha_inicio)` (the
        composite primary key `consumos` actually has, being partitioned by RANGE on
        `fecha_inicio` -- see `infrastructure/models.py`'s `ConsumoModel`): clear `deleted_at` and
        write every mutable field from `consumo`, already merged onto that same identity by the
        caller (`ImportConsumos`). Mirrors `ClienteRepository.resurrect()` for the "update by
        identity, not `ON CONFLICT`" rationale.

        Does not commit; raises `ConsumoConflictError` on a database integrity conflict.
        """
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def soft_delete(self, consumo_id: UUID) -> bool:
        """Soft-delete the ACTIVE consumo with this `id` (DECISION #13, the correction path for a
        wrongly imported billing period): set `deleted_at`, return whether a row was actually
        found and updated. `False` covers both an unknown id and an already-soft-deleted one --
        `DeleteConsumo`/the route surface both as the same 404.

        Located by `id` alone, not the full composite primary key `(id, fecha_inicio)`: the HTTP
        boundary (`DELETE /api/v1/consumos/{id}`) only ever supplies `id`. This assumes `id` is
        unique in practice even though the schema's actual primary key is composite -- reasonable
        since `id` is generated via `uuid4()` (domain/consumo.py) and never reused, but not a
        database-enforced guarantee the way a plain `id`-only primary key would be.

        Does not commit: the caller (route boundary) controls the transaction.
        """
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def list_active(
        self,
        *,
        limit: int,
        offset: int,
        suministro_id: UUID | None = None,
        lote_id: UUID | None = None,
    ) -> list[Consumo]:
        """Return non-soft-deleted consumos ordered by `(fecha_inicio desc, id)` — most recent
        period first, see `presentation/routes.py`'s `list_consumos` for why — page
        (limit/offset), optionally filtered to a single `suministro_id` and/or `lote_id`."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def count_active(
        self, *, suministro_id: UUID | None = None, lote_id: UUID | None = None
    ) -> int:
        """Count non-soft-deleted consumos (for pagination metadata), optionally filtered to a
        single `suministro_id` and/or `lote_id`."""
        ...  # pragma: no cover — Protocol stub, never executed directly
