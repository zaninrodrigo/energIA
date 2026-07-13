"""Lote entity and invariants (DOMAIN_MODEL.md §7.4, docker/postgres/init/01_schema.sql).

Persistence-ignorant on purpose (ADR-001), mirroring `contexts/consumos/domain/lectura.py`'s
pattern exactly: no SQLAlchemy or any other infrastructure import belongs in this module.
`Lote.create()` is the only way to build a `Lote` from untrusted input -- it enforces every
invariant and raises `LoteValidationError` (with every violation found, not just the first)
instead of silently accepting bad data. Reconstructing a `Lote` from a trusted source (a database
row already known to satisfy these invariants) can call the dataclass constructor directly, e.g.
from `infrastructure/lote_repository.py`.

Unlike every other entity in this codebase, `Lote` has no natural-key foreign key to resolve
(`lotes` references no other table) -- so `Lote.create()` has no directory-port dependency, and
`ImportLotes` (application/import_lotes.py) has no cross-context resolution step at all.

**`estado` is deliberately not a `create()` parameter, at all.** `Lote.create()` always returns a
lote born `EstadoLote.PENDIENTE`, with no way to override that from the outside -- not even by
passing `estado="Procesado"` explicitly. This is a direct implementation of RD-010 ("un lote no
puede ejecutarse dos veces", DOMAIN_MODEL.md §7.4): state transitions (`Pendiente` -> `Procesando`
-> `Procesado`/`Error`) are the future processing engine's job, a use case that does not exist
yet. If the import payload could set `estado` freely, a single crafted import request could
fabricate an already-`Procesado` lote -- one that the processing engine would then skip, or one
whose `Procesando` state could race a real run -- without ever having gone through the pipeline
that state is supposed to represent. Keeping `estado` out of `create()`'s signature entirely means
this cannot happen by construction, not just by convention at the HTTP schema layer (see
`presentation/schemas.py`'s `LoteImportItem`, which mirrors the same omission).

This has one direct consequence for `ImportLotes`' update path: an existing lote's `estado` (and
`fecha_importacion`, its original import timestamp) must be carried forward via
`dataclasses.replace()`, never rebuilt through `create()` -- see that module's docstring.
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

# Schema-derived length limits (docker/postgres/init/01_schema.sql, table `lotes`). Kept in sync
# with the DB's varchar sizes so a rejected record is caught here instead of surfacing as an
# opaque database error later.
_CODIGO_LOTE_MAX_LENGTH = 50
_NOMBRE_MAX_LENGTH = 150

_CANTIDAD_REGISTROS_DEFAULT = 0

# `lotes.cantidad_registros` is a plain Postgres `integer` (docker/postgres/init/01_schema.sql),
# not a `bigint` -- 2_147_483_647 is the largest value that column can hold. A larger value (e.g.
# a big-int JSON literal like `99999999999`) passes every check above and every Pydantic/domain
# type check today, since Python `int`s are arbitrary-precision, and only fails once the write
# actually reaches the database, as an opaque `DBAPIError` (not `IntegrityError`, so it is not
# even translated into `LoteConflictError`) -- a 500 for the whole batch instead of a per-record
# rejection. Bounded here so it is caught before it ever reaches the repository.
_CANTIDAD_REGISTROS_MAX = 2_147_483_647


class EstadoLote(StrEnum):
    """The four states DOMAIN_MODEL.md §7.4 ("Estados") enumerates for a Lote de Facturación --
    a closed set, matching `ck_lotes_estado` (docker/postgres/init/01_schema.sql) exactly.

    `StrEnum` (not a plain `Enum`) so a value serializes/compares as its own literal (e.g.
    `EstadoLote.PENDIENTE == "Pendiente"` is `True`), the same way `Cliente`/`Suministro` keep
    `estado` as a plain `str` field -- the difference here is a *closed* set gets a real enum
    instead of an open varchar, per DOMAIN_MODEL.md's own "Estados" heading for this entity (see
    `docker/postgres/init/01_schema.sql`'s top-of-file convention comment on when an enum vs. a
    free varchar is used).
    """

    PENDIENTE = "Pendiente"
    PROCESANDO = "Procesando"
    PROCESADO = "Procesado"
    ERROR = "Error"


# RD-010 ("un lote no puede ejecutarse dos veces"): a lote may only ever move forward through
# this pipeline, never sideways or backward, and there is no transition directly from `Pendiente`
# to `Procesado`/`Error` (it must actually go through `Procesando` first). `Procesado` is terminal
# by design -- RD-010 explicitly rules out re-running a lote that already succeeded.
#
# `Error` -> `Procesando` (retrying a failed lote) was confirmed by business (Rodrigo Zanin,
# 2026-07-13): a failed lote may be retried by moving back into `Procesando`. This is no longer an
# unreviewed assumption -- `Error` is the only non-terminal failure state in this map; `Procesado`
# remains terminal, unaffected by this decision.
#
# Nothing calls these transitions yet (the processing engine that would is a future user story),
# but the invariant belongs in the domain now, not bolted onto that future use case as an
# afterthought -- see `Lote.transition_to()` below.
#
# RD-011 ("un lote debe finalizar antes de iniciar otro") is a *cross-aggregate* invariant -- it
# constrains which lote may enter `Procesando` relative to every *other* lote's current state, not
# a single lote's own transition legality. It is out of scope for this map (and for
# `transition_to()`, which only ever sees one `Lote` at a time): enforcing it is the future
# processing engine's responsibility, once that use case exists.
ALLOWED_TRANSITIONS: dict[EstadoLote, frozenset[EstadoLote]] = {
    EstadoLote.PENDIENTE: frozenset({EstadoLote.PROCESANDO}),
    EstadoLote.PROCESANDO: frozenset({EstadoLote.PROCESADO, EstadoLote.ERROR}),
    EstadoLote.PROCESADO: frozenset(),
    EstadoLote.ERROR: frozenset({EstadoLote.PROCESANDO}),
}


class LoteValidationError(ValueError):
    """Raised by `Lote.create()` when one or more invariants are violated.

    `errors` holds every individual violation message (not just the first), so `ImportLotes` can
    report a granular rejection reason for each malformed record without aborting the rest of the
    batch.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


class LoteEstadoTransitionError(ValueError):
    """Raised by `Lote.transition_to()` when the requested transition is not in
    `ALLOWED_TRANSITIONS` -- RD-010."""


@dataclass(frozen=True, slots=True)
class Lote:
    """Domain entity: Lote de Facturación (DOMAIN_MODEL.md §7.4). Aggregate Root of the Consumos
    bounded context (`contexts/README.md`, §4.3 "Gestión de Consumos"), alongside `Lectura`.

    Field names mirror the `lotes` table exactly (docker/postgres/init/01_schema.sql) rather than
    the domain doc's camelCase, per this project's naming convention (see `contexts/README.md`).

    Identity is the natural key `codigo_lote` (one row per business batch code,
    `uq_lotes_codigo_lote`) -- the DB's own business identifier for the batch/run being imported,
    not a mirror of any external system's primary key.
    """

    id: UUID
    codigo_lote: str
    nombre: str | None
    fecha_importacion: datetime
    cantidad_registros: int
    estado: EstadoLote

    @staticmethod
    def create(
        *,
        codigo_lote: str | None,
        nombre: str | None = None,
        cantidad_registros: int | str | None = None,
        id: UUID | None = None,
        fecha_importacion: datetime | None = None,
    ) -> "Lote":
        """Validate and build a `Lote`; raises `LoteValidationError` on any violation.

        `id` is optional: a fresh one is generated for a brand-new lote. `fecha_importacion` is
        optional too, for the same reason: a fresh "now" is generated for a brand-new lote (the
        moment it was actually imported), mirroring how `id` defaults to a fresh `uuid4()` --
        neither value can come from the DB's own `DEFAULT` clause before the row is written, since
        `Lote.create()` must hand back a fully valid, ready-to-persist entity. Passing an
        existing `id`/`fecha_importacion` (as `ImportLotes` does indirectly via
        `dataclasses.replace()` when updating a row found by `codigo_lote` -- see that module)
        keeps the row's identity and original import timestamp stable across re-imports.

        There is deliberately no `estado` parameter -- see this module's docstring.
        """
        errors: list[str] = []

        codigo_lote_limpio = (codigo_lote or "").strip()
        if not codigo_lote_limpio:
            # RD-010's precondition: a lote needs a stable business identifier before it can be
            # looked up for "no puede ejecutarse dos veces" to mean anything.
            errors.append("codigo_lote es obligatorio")
        elif len(codigo_lote_limpio) > _CODIGO_LOTE_MAX_LENGTH:
            errors.append(f"codigo_lote no puede superar {_CODIGO_LOTE_MAX_LENGTH} caracteres")

        if nombre is not None and len(nombre) > _NOMBRE_MAX_LENGTH:
            errors.append(f"nombre no puede superar {_NOMBRE_MAX_LENGTH} caracteres")

        cantidad_registros_valida = _parse_cantidad_registros(cantidad_registros, errors)

        if errors:
            raise LoteValidationError(errors)

        # Narrows `cantidad_registros_valida` from `| None` to the dataclass field's
        # non-optional type, for mypy. Safe: it would already have appended to `errors` above if
        # invalid, and `create()` already raised and returned above whenever `errors` is
        # non-empty.
        assert cantidad_registros_valida is not None

        return Lote(
            id=id or uuid4(),
            codigo_lote=codigo_lote_limpio,
            nombre=nombre,
            fecha_importacion=fecha_importacion or datetime.now(UTC),
            cantidad_registros=cantidad_registros_valida,
            estado=EstadoLote.PENDIENTE,
        )

    def transition_to(self, nuevo_estado: EstadoLote) -> "Lote":
        """Return a new `Lote` with `estado` moved to `nuevo_estado`, if that transition is
        allowed (`ALLOWED_TRANSITIONS`); raises `LoteEstadoTransitionError` otherwise (RD-010).

        Nothing calls this yet -- the processing engine that would (Pendiente -> Procesando ->
        Procesado/Error) is a future user story -- but the transition rule is a domain invariant
        that must exist from the moment `estado` does, not be re-derived ad hoc by whichever use
        case calls it first.
        """
        permitido = ALLOWED_TRANSITIONS[self.estado]
        if nuevo_estado not in permitido:
            raise LoteEstadoTransitionError(
                f"transición de estado no permitida: {self.estado.value} -> {nuevo_estado.value}"
            )
        return replace(self, estado=nuevo_estado)


def _parse_cantidad_registros(value: int | str | None, errors: list[str]) -> int | None:
    """Parse and validate `cantidad_registros`: optional, defaults to 0 when omitted (matching
    the field's documented default), an integer, and never negative
    (`ck_lotes_cantidad_registros_no_negativa`). No NaN/Infinity guard is needed here (unlike
    `Lectura.lectura_anterior`/`lectura_actual`): this is an integer count, not a float parsed
    from a numeric literal, so those float-only edge cases do not apply.
    """
    if value is None:
        return _CANTIDAD_REGISTROS_DEFAULT

    cantidad: int
    if isinstance(value, bool):
        # bool is a subclass of int in Python -- True/False must not silently pass as 1/0.
        errors.append(f"cantidad_registros inválido: {value!r} (debe ser un entero)")
        return None
    if isinstance(value, int):
        cantidad = value
    elif isinstance(value, str):
        try:
            cantidad = int(value.strip())
        except ValueError:
            errors.append(f"cantidad_registros inválido: {value!r} (debe ser un entero)")
            return None
    else:
        errors.append(f"cantidad_registros inválido: {value!r} (debe ser un entero)")
        return None

    if cantidad < 0:
        errors.append(f"cantidad_registros no puede ser negativo: {cantidad}")
        return None

    if cantidad > _CANTIDAD_REGISTROS_MAX:
        errors.append(f"cantidad_registros no puede superar {_CANTIDAD_REGISTROS_MAX}: {cantidad}")
        return None

    return cantidad
