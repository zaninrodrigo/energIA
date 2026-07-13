"""Lectura entity and invariants (DOMAIN_MODEL.md §7.5, docker/postgres/init/01_schema.sql).

Persistence-ignorant on purpose (ADR-001), mirroring `contexts/suministros/domain/suministro.py`'s
pattern exactly: no SQLAlchemy or any other infrastructure import belongs in this module.
`Lectura.create()` is the only way to build a `Lectura` from untrusted input -- it enforces
every invariant and raises `LecturaValidationError` (with every violation found, not just the
first) instead of silently accepting bad data. Reconstructing a `Lectura` from a trusted source
(a database row already known to satisfy these invariants) can call the dataclass constructor
directly, e.g. from `infrastructure/lectura_repository.py`.

`suministro_id` is a plain, already-resolved UUID here -- resolving the natural key a source
record actually carries (`numero_suministro`) is `ImportLecturas`' job (application layer), via
the `SuministroDirectory` port (domain/ports.py). This entity only enforces that *some*
suministro was resolved (RD-015) -- not that the referenced row exists, which the application
layer already guarantees by construction before calling `create()`.

RD-013 ("la lectura actual debe ser mayor o igual que la anterior") and RD-014 ("los días
facturados deben ser mayores que cero") are already enforced as CHECK constraints in
`docker/postgres/init/01_schema.sql` (`ck_lecturas_actual_mayor_igual_anterior`,
`ck_lecturas_dias_facturados_positivo`). They are enforced here too, deliberately: relying on
the DB's CHECK to reject a bad record would surface as an opaque `IntegrityError` for the whole
transaction instead of a per-record rejection reason `ImportLecturas` can report individually
(the same reasoning `contexts/README.md` documents for schema-derived length limits).
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

# Schema-derived precision limit (docker/postgres/init/01_schema.sql, `lecturas.lectura_anterior`/
# `lectura_actual numeric(12,3)`): 12 total digits, 3 of them after the decimal point, so at most
# 9 digits before it. Kept in sync with the DB's column type so an over-precise or too-large
# value is caught here instead of surfacing as an opaque "numeric field overflow" database error.
_NUMERIC_MAX_DIGITS = 12
_NUMERIC_DECIMAL_PLACES = 3
_NUMERIC_MAX_INTEGER_DIGITS = _NUMERIC_MAX_DIGITS - _NUMERIC_DECIMAL_PLACES
_NUMERIC_MAX_VALUE = Decimal(10) ** _NUMERIC_MAX_INTEGER_DIGITS


class LecturaValidationError(ValueError):
    """Raised by `Lectura.create()` when one or more invariants are violated.

    `errors` holds every individual violation message (not just the first), so
    `ImportLecturas` can report a granular rejection reason for each malformed record without
    aborting the rest of the batch.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True, slots=True)
class Lectura:
    """Domain entity: Lectura (DOMAIN_MODEL.md §7.5). Aggregate Root of the Consumos bounded
    context (`contexts/README.md`, §4.3 "Gestión de Consumos").

    Field names mirror the `lecturas` table exactly (docker/postgres/init/01_schema.sql) rather
    than the domain doc's camelCase, per this project's naming convention (see
    `contexts/README.md`): domain nouns and DB columns are the same Spanish identifiers.

    Identity is the composite natural key `(suministro_id, fecha_lectura)` (one lectura per
    suministro per date, `uq_lecturas_suministro_fecha`) -- not `numero_suministro` alone, since a
    single suministro accumulates many lecturas over time.
    """

    id: UUID
    suministro_id: UUID
    fecha_lectura: date
    lectura_anterior: Decimal
    lectura_actual: Decimal
    dias_facturados: int

    @staticmethod
    def create(
        *,
        suministro_id: UUID | None,
        fecha_lectura: date | str | None,
        lectura_anterior: Decimal | int | float | str | None,
        lectura_actual: Decimal | int | float | str | None,
        dias_facturados: int | str | None,
        id: UUID | None = None,
    ) -> "Lectura":
        """Validate and build a `Lectura`; raises `LecturaValidationError` on any violation.

        `id` is optional: a fresh one is generated for a brand-new lectura. Passing an existing
        id (as `ImportLecturas` does when updating a row found by `(suministro_id,
        fecha_lectura)`) keeps the row's identity stable across re-imports.
        """
        errors: list[str] = []

        if suministro_id is None:
            # RD-015: "Una lectura pertenece a un único suministro."
            errors.append("suministro_id es obligatorio")

        fecha_lectura_valida = _parse_fecha_lectura(fecha_lectura, errors)
        lectura_anterior_valida = _parse_medidor(lectura_anterior, "lectura_anterior", errors)
        lectura_actual_valida = _parse_medidor(lectura_actual, "lectura_actual", errors)
        dias_facturados_validos = _parse_dias_facturados(dias_facturados, errors)

        if (
            lectura_anterior_valida is not None
            and lectura_actual_valida is not None
            and lectura_actual_valida < lectura_anterior_valida
        ):
            # RD-013: "La lectura actual debe ser mayor o igual que la anterior."
            errors.append(
                "lectura_actual no puede ser menor que lectura_anterior "
                f"({lectura_actual_valida} < {lectura_anterior_valida})"
            )

        if errors:
            raise LecturaValidationError(errors)

        # Narrows every parsed value from its `| None` parameter type to the dataclass field's
        # non-optional type, for mypy. Safe: each would already have appended to `errors` above
        # if `None`/invalid, and `create()` already raised and returned above in that case.
        assert suministro_id is not None
        assert fecha_lectura_valida is not None
        assert lectura_anterior_valida is not None
        assert lectura_actual_valida is not None
        assert dias_facturados_validos is not None

        return Lectura(
            id=id or uuid4(),
            suministro_id=suministro_id,
            fecha_lectura=fecha_lectura_valida,
            lectura_anterior=lectura_anterior_valida,
            lectura_actual=lectura_actual_valida,
            dias_facturados=dias_facturados_validos,
        )


def _parse_fecha_lectura(value: date | str | None, errors: list[str]) -> date | None:
    if value is None:
        errors.append("fecha_lectura es obligatoria")
        return None
    if isinstance(value, datetime):
        # `datetime` is a subclass of `date` -- checked explicitly and first, or the
        # `isinstance(value, date)` branch below would silently accept it. Not reachable via the
        # JSON boundary today, but guards a future file/Oracle adapter (ADR-004) passing one.
        errors.append(f"fecha_lectura inválida: {value!r} (se esperaba date, no datetime)")
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        errors.append(f"fecha_lectura inválida: {value!r} (formato esperado: YYYY-MM-DD)")
        return None


def _parse_medidor(
    value: Decimal | int | float | str | None, field_name: str, errors: list[str]
) -> Decimal | None:
    """Parse and validate one `lectura_anterior`/`lectura_actual` value against `numeric(12,3)`
    plus the domain-only "a meter never goes negative" invariant.

    `Decimal(str(value))`, not `Decimal(value)` directly: a `float` converted straight to
    `Decimal` carries its exact (long) binary representation, which would falsely trip the
    3-decimal-place check for an ordinary value like `150.5`. Going through `str(value)` first
    uses the same shortest round-tripping representation `repr()`/`str()` already give a float
    parsed from a JSON payload, which is what the caller (a JSON body) actually sent.
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
        # Must be checked before any ordering comparison: `Decimal('nan') < 0` does not return
        # False like a regular numeric comparison would -- it raises `decimal.InvalidOperation`
        # (NaN has no ordering), which nothing downstream catches. Left unguarded, one NaN-bearing
        # record would crash the whole batch with an HTTP 500 instead of being rejected on its own.
        errors.append(f"{field_name} inválido: no puede ser NaN")
        return None

    if decimal_value.is_infinite():
        # Infinity does not raise on comparison the way NaN does (it has a well-defined
        # ordering), so it would otherwise slip through here and only get rejected later, as an
        # incidental side effect of the integer-digit-limit check below. Rejected explicitly
        # instead, with a reason that actually names the problem.
        errors.append(f"{field_name} inválido: no puede ser infinito")
        return None

    if decimal_value < 0:
        # RD-050: "Una lectura de medidor nunca puede ser negativa." Domain-only invariant (not a
        # DB CHECK): a physical meter reading never goes negative.
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


def _parse_dias_facturados(value: int | str | None, errors: list[str]) -> int | None:
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
        # RD-014: "Los días facturados deben ser mayores que cero."
        errors.append(f"dias_facturados debe ser mayor que cero: {dias}")
        return None

    return dias
