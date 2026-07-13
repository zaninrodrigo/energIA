"""Consumo entity and invariants (DOMAIN_MODEL.md §7.6, docker/postgres/init/01_schema.sql).

Persistence-ignorant on purpose (ADR-001), mirroring `contexts/consumos/domain/lectura.py`'s
pattern exactly: no SQLAlchemy or any other infrastructure import belongs in this module.
`Consumo.create()` is the only way to build a `Consumo` from untrusted input -- it enforces every
invariant and raises `ConsumoValidationError` (with every violation found, not just the first)
instead of silently accepting bad data. Reconstructing a `Consumo` from a trusted source (a
database row already known to satisfy these invariants) can call the dataclass constructor
directly, e.g. from `infrastructure/consumo_repository.py`.

`suministro_id`/`lote_id`/`lectura_id` are plain, already-resolved UUIDs here -- resolving the
natural keys a source record actually carries (`numero_suministro`, `codigo_lote`, `fecha_lectura`)
is `ImportConsumos`' job (application layer), via the `SuministroDirectory`/`LoteDirectory`/
`LecturaRepository` ports (domain/ports.py). This entity only enforces that suministro/lote were
resolved (RD-004, RD-012/RD-019) -- not that the referenced rows exist, which the application
layer already guarantees by construction before calling `create()`.

**`consumo_promedio_diario` is the first field in this codebase whose `create()` parameter itself
needs the three-state `UNSET`/`None`/value distinction** (`domain/sentinels.py`), not just the
application layer's update-merge step (contrast `LoteSourceRecord.nombre`/`cantidad_registros`,
where `create()` only ever sees a *collapsed* two-state `None`-or-value -- see
`application/import_lotes.py`'s docstring). The reason is DOMAIN_MODEL.md §7.6 itself:
`calcularPromedioDiario()` is listed as a domain method, so deriving the average from `kwh` /
`dias_facturados` when the source omits it is a genuine domain computation, not application-layer
plumbing -- and `create()` is the one place that already has both `kwh` and `dias_facturados`
validated and in hand. The three states this module resolves, uniformly on both create and
update (the update-merge decision itself lives in `application/import_consumos.py`):

- `UNSET` (the source payload never mentioned this field): **compute** the derived average
  (`kwh / dias_facturados`, quantized to `numeric(12,3)`) on a fresh `create()` call. On an
  *update*, `ImportConsumos` computes it exactly the same way -- from the record's own `kwh`/
  `dias_facturados`, which are always required and freshly validated on every import, never
  omittable themselves -- so recompute-on-omission is always well-defined and always at least as
  current as the row it would otherwise leave stale (FIX 1: this used to instead substitute the
  existing row's already-stored value before re-validating, silently preserving a stale average
  whenever `kwh`/`dias_facturados` changed without repeating `consumo_promedio_diario` too --
  reproduced case: `kwh` 100 -> 295, `dias_facturados` 31, omitted `consumo_promedio_diario`
  froze at 3.226 instead of recomputing to 9.516). This is the opposite of `Lote.nombre`/
  `cantidad_registros` (`application/import_lotes.py`'s own FIX 1), which genuinely *must*
  preserve `existing`'s stored value on omission: those are opaque fields with no derived inputs
  of their own to recompute from, so their only fallback on omission is a domain default
  (`None`/`0`) that would silently wipe real stored data if applied on update. `kwh`/
  `dias_facturados` are always present here, so there is no equivalent "missing input" case to
  guard against.
- `None` (the payload explicitly sent `null`): **store `null`**, skip computation entirely -- a
  `NULL` average is allowed by the schema (`consumos.consumo_promedio_diario` has no `NOT NULL`),
  and an explicit `null` is treated as "the source explicitly has no opinion", the same way it
  overwrites `Lote.nombre` to `null` on update (FIX 1) rather than being silently coerced to some
  computed value.
- a concrete value: **validate it like `kwh`** (NaN/Infinity guards, `numeric(12,3)` bounds,
  never negative) and store it as given -- the source's own average is trusted verbatim once it
  passes those guards, not cross-checked against the derived `kwh / dias_facturados` value.

`numeric(12,3)` rounding can make a genuinely nonzero average indistinguishable from a true zero:
`kwh=0.001`, `dias_facturados=365` computes to `0.0000027...`, which rounds to `0.000` -- the same
value a real zero-consumption record would store. Relevant for anomaly detection downstream,
where zero consumption is itself a signal; not addressed here.

RD-016 ("el consumo debe ser mayor o igual a cero") and the period's `fecha_fin >= fecha_inicio`
check are already enforced as CHECK constraints in `docker/postgres/init/01_schema.sql`
(`ck_consumos_kwh_no_negativo`, `ck_consumos_periodo_valido`). They are enforced here too,
deliberately -- same reasoning `lectura.py`'s module docstring documents for RD-013/RD-014: relying
on the DB's CHECK to reject a bad record would surface as an opaque `IntegrityError` for the whole
transaction instead of a per-record rejection reason `ImportConsumos` can report individually.

RD-017 ("el período no puede superponerse con otro") is **not** enforced here, nor anywhere in
this slice: it would require checking every *other* consumo of the same suministro for a
partially-overlapping period, a cross-row invariant this entity (which only ever sees one record
at a time) cannot express, and the database has no `EXCLUDE` constraint for it either (documented
gap, `docker/postgres/init/01_schema.sql`'s comment on `uq_consumos_suministro_periodo`, and
`docs/03-architecture/DATABASE_DESIGN.md` §6.4). Only the *exact-duplicate* case is prevented, by
that partial unique index -- see `infrastructure/consumo_repository.py`'s `save()`.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from uuid import UUID, uuid4

from energia.contexts.consumos.domain.sentinels import UNSET, UnsetType

# Schema-derived precision limit (docker/postgres/init/01_schema.sql, `consumos.kwh`/
# `consumo_promedio_diario numeric(12,3)`): 12 total digits, 3 of them after the decimal point, so
# at most 9 digits before it. Kept in sync with the DB's column type so an over-precise or
# too-large value is caught here instead of surfacing as an opaque "numeric field overflow"
# database error. Mirrors `lectura.py`'s identical constants exactly (same column type).
_NUMERIC_MAX_DIGITS = 12
_NUMERIC_DECIMAL_PLACES = 3
_NUMERIC_MAX_INTEGER_DIGITS = _NUMERIC_MAX_DIGITS - _NUMERIC_DECIMAL_PLACES
_NUMERIC_MAX_VALUE = Decimal(10) ** _NUMERIC_MAX_INTEGER_DIGITS
_QUANTUM = Decimal(1).scaleb(-_NUMERIC_DECIMAL_PLACES)  # Decimal("0.001")

# `consumos.dias_facturados` is a plain Postgres `integer`, not a `bigint` -- 2_147_483_647 is the
# largest value that column can hold. Same gap `Lectura.dias_facturados` has (`domain/lectura.py`):
# bounded here so a big-int JSON literal is caught before it ever reaches the database.
_DIAS_FACTURADOS_MAX = 2_147_483_647


class ConsumoValidationError(ValueError):
    """Raised by `Consumo.create()` when one or more invariants are violated.

    `errors` holds every individual violation message (not just the first), so `ImportConsumos`
    can report a granular rejection reason for each malformed record without aborting the rest of
    the batch.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True, slots=True)
class Consumo:
    """Domain entity: Consumo (DOMAIN_MODEL.md §7.6). Aggregate Root of the Consumos bounded
    context (`contexts/README.md`, §4.3 "Gestión de Consumos"), alongside `Lectura`/`Lote`.

    Field names mirror the `consumos` table exactly (docker/postgres/init/01_schema.sql) rather
    than the domain doc's camelCase, per this project's naming convention (see
    `contexts/README.md`).

    Identity is the composite natural key `(suministro_id, fecha_inicio, fecha_fin)` (one consumo
    per suministro per billing period, `uq_consumos_suministro_periodo`) -- not `suministro_id`
    alone, since a single suministro accumulates many consumos over time, one per period.
    """

    id: UUID
    suministro_id: UUID
    lote_id: UUID
    lectura_id: UUID | None
    fecha_inicio: date
    fecha_fin: date
    dias_facturados: int
    kwh: Decimal
    consumo_promedio_diario: Decimal | None

    @staticmethod
    def create(
        *,
        suministro_id: UUID | None,
        lote_id: UUID | None,
        fecha_inicio: date | str | None,
        fecha_fin: date | str | None,
        dias_facturados: int | str | None,
        kwh: Decimal | int | float | str | None,
        lectura_id: UUID | None = None,
        consumo_promedio_diario: Decimal | int | float | str | None | UnsetType = UNSET,
        id: UUID | None = None,
    ) -> "Consumo":
        """Validate and build a `Consumo`; raises `ConsumoValidationError` on any violation.

        `id` is optional: a fresh one is generated for a brand-new consumo. `lectura_id` is
        already a fully resolved UUID (or `None`, "no lectura for this period") by the time this
        is called -- see this module's docstring. `consumo_promedio_diario` defaults to `UNSET`
        (compute the derived average) -- see this module's docstring for the full three-state
        rationale.
        """
        errors: list[str] = []

        if suministro_id is None:
            # RD-004: "un suministro posee múltiples consumos".
            errors.append("suministro_id es obligatorio")

        if lote_id is None:
            # RD-012/RD-019: "todo consumo pertenece a un lote".
            errors.append("lote_id es obligatorio")

        fecha_inicio_valida = _parse_fecha(fecha_inicio, "fecha_inicio", errors)
        fecha_fin_valida = _parse_fecha(fecha_fin, "fecha_fin", errors)
        dias_facturados_validos = _parse_dias_facturados(dias_facturados, errors)
        kwh_valido = _parse_numeric_12_3(kwh, "kwh", errors)

        if (
            fecha_inicio_valida is not None
            and fecha_fin_valida is not None
            and fecha_fin_valida < fecha_inicio_valida
        ):
            # ck_consumos_periodo_valido: integridad temporal del período.
            errors.append(
                f"fecha_fin no puede ser anterior a fecha_inicio "
                f"({fecha_fin_valida} < {fecha_inicio_valida})"
            )

        consumo_promedio_diario_valido = _parse_promedio_diario(
            consumo_promedio_diario, kwh_valido, dias_facturados_validos, errors
        )

        if errors:
            raise ConsumoValidationError(errors)

        # Narrows every parsed value from its `| None` parameter type to the dataclass field's
        # non-optional type, for mypy. Safe: each would already have appended to `errors` above if
        # `None`/invalid, and `create()` already raised and returned above in that case.
        assert suministro_id is not None
        assert lote_id is not None
        assert fecha_inicio_valida is not None
        assert fecha_fin_valida is not None
        assert dias_facturados_validos is not None
        assert kwh_valido is not None

        return Consumo(
            id=id or uuid4(),
            suministro_id=suministro_id,
            lote_id=lote_id,
            lectura_id=lectura_id,
            fecha_inicio=fecha_inicio_valida,
            fecha_fin=fecha_fin_valida,
            dias_facturados=dias_facturados_validos,
            kwh=kwh_valido,
            consumo_promedio_diario=consumo_promedio_diario_valido,
        )


def _parse_fecha(value: date | str | None, field_name: str, errors: list[str]) -> date | None:
    if value is None:
        errors.append(f"{field_name} es obligatoria")
        return None
    if isinstance(value, datetime):
        # `datetime` is a subclass of `date` -- checked explicitly and first, or the
        # `isinstance(value, date)` branch below would silently accept it. Mirrors
        # `Lectura.create()`'s identical `fecha_lectura` guard.
        errors.append(f"{field_name} inválida: {value!r} (se esperaba date, no datetime)")
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        errors.append(f"{field_name} inválida: {value!r} (formato esperado: YYYY-MM-DD)")
        return None


def _parse_numeric_12_3(
    value: Decimal | int | float | str | None, field_name: str, errors: list[str]
) -> Decimal | None:
    """Parse and validate one `kwh`/`consumo_promedio_diario` value against `numeric(12,3)` plus
    the "never negative" invariant (RD-016 for `kwh`; applied identically to
    `consumo_promedio_diario` when a concrete value is given -- see this module's docstring).

    Mirrors `Lectura.create()`'s `_parse_medidor` exactly, including the `Decimal(str(value))`
    rationale: converting a `float` straight to `Decimal` carries its exact (long) binary
    representation, which would falsely trip the 3-decimal-place check for an ordinary value like
    `150.5`. Going through `str(value)` first uses the same shortest round-tripping representation
    a JSON payload's `float` already gives.
    """
    if value is None:
        errors.append(f"{field_name} es obligatorio")
        return None

    if isinstance(value, Decimal):
        decimal_value = value
    else:
        try:
            decimal_value = Decimal(str(value))
        except InvalidOperation:
            errors.append(f"{field_name} inválido: {value!r} (debe ser numérico)")
            return None

    if decimal_value.is_nan():
        # Must be checked before any ordering comparison: `Decimal('nan') < 0` raises
        # `decimal.InvalidOperation` (NaN has no ordering) instead of returning `False`. Left
        # unguarded, one NaN-bearing record would crash the whole batch with an HTTP 500.
        errors.append(f"{field_name} inválido: no puede ser NaN")
        return None

    if decimal_value.is_infinite():
        errors.append(f"{field_name} inválido: no puede ser infinito")
        return None

    if decimal_value < 0:
        # RD-016 ("el consumo debe ser mayor o igual a cero"), applied identically to
        # `consumo_promedio_diario` -- see this module's docstring.
        errors.append(f"{field_name} no puede ser negativo: {decimal_value}")
        return None

    # `.normalize()` drops insignificant trailing zeros (e.g. Decimal("100.4560") -> 100.456)
    # before checking the decimal scale, so a value that merely *looks* 4-decimal in its
    # textual/JSON form is not falsely rejected.
    normalized = decimal_value.normalize()
    exponent = normalized.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -_NUMERIC_DECIMAL_PLACES:
        errors.append(
            f"{field_name} no puede tener más de {_NUMERIC_DECIMAL_PLACES} decimales: "
            f"{decimal_value}"
        )
        return None

    if decimal_value >= _NUMERIC_MAX_VALUE:
        errors.append(
            f"{field_name} no puede superar {_NUMERIC_MAX_INTEGER_DIGITS} dígitos enteros "
            f"(numeric({_NUMERIC_MAX_DIGITS},{_NUMERIC_DECIMAL_PLACES})): {decimal_value}"
        )
        return None

    return decimal_value


def _parse_promedio_diario(
    value: Decimal | int | float | str | None | UnsetType,
    kwh_valido: Decimal | None,
    dias_facturados_validos: int | None,
    errors: list[str],
) -> Decimal | None:
    """Resolve `consumo_promedio_diario`'s three states -- see this module's docstring for the
    full rationale of each branch."""
    if isinstance(value, UnsetType):
        if kwh_valido is None or dias_facturados_validos is None:
            # `kwh`/`dias_facturados` already failed their own validation above -- `errors` is
            # non-empty and `create()` will raise before this return value is ever used, so any
            # placeholder is safe here.
            return None
        # DOMAIN_MODEL.md §7.6 `calcularPromedioDiario()`: the derived daily average.
        return (kwh_valido / Decimal(dias_facturados_validos)).quantize(
            _QUANTUM, rounding=ROUND_HALF_UP
        )

    if value is None:
        # Explicit null: store null, do not compute -- see this module's docstring.
        return None

    return _parse_numeric_12_3(value, "consumo_promedio_diario", errors)


def _parse_dias_facturados(value: int | str | None, errors: list[str]) -> int | None:
    """Mirrors `Lectura.create()`'s `_parse_dias_facturados` exactly, applied to the analogous
    "días facturados deben ser mayores que cero" invariant for a Consumo's own period."""
    if value is None:
        errors.append("dias_facturados es obligatorio")
        return None

    dias: int
    if isinstance(value, bool):
        # bool is a subclass of int in Python -- True/False must not silently pass as 1/0.
        errors.append(f"dias_facturados inválido: {value!r} (debe ser un entero)")
        return None
    if isinstance(value, int):
        dias = value
    elif isinstance(value, str):
        try:
            dias = int(value.strip())
        except ValueError:
            errors.append(f"dias_facturados inválido: {value!r} (debe ser un entero)")
            return None
    else:
        errors.append(f"dias_facturados inválido: {value!r} (debe ser un entero)")
        return None

    if dias <= 0:
        errors.append(f"dias_facturados debe ser mayor que cero: {dias}")
        return None

    if dias > _DIAS_FACTURADOS_MAX:
        errors.append(f"dias_facturados no puede superar {_DIAS_FACTURADOS_MAX}: {dias}")
        return None

    return dias
