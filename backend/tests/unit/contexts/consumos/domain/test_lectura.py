"""Unit tests for the Lectura entity and its invariants (DOMAIN_MODEL.md §7.5).

Mirrors `tests/unit/contexts/suministros/domain/test_suministro.py`'s structure, plus the
numeric invariants specific to Lectura (RD-013, RD-014) that Suministro does not have.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from energia.contexts.consumos.domain.lectura import Lectura, LecturaValidationError

_SUMINISTRO_ID = uuid4()


def _create(**overrides: object) -> Lectura:
    kwargs: dict[str, object] = {
        "suministro_id": _SUMINISTRO_ID,
        "fecha_lectura": "2024-01-15",
        "lectura_anterior": "100.000",
        "lectura_actual": "150.500",
        "dias_facturados": 30,
    }
    kwargs.update(overrides)
    return Lectura.create(**kwargs)  # type: ignore[arg-type]


def test_create_builds_a_lectura_with_the_given_values() -> None:
    lectura = _create()

    assert isinstance(lectura.id, UUID)
    assert lectura.suministro_id == _SUMINISTRO_ID
    assert lectura.fecha_lectura == date(2024, 1, 15)
    assert lectura.lectura_anterior == Decimal("100.000")
    assert lectura.lectura_actual == Decimal("150.500")
    assert lectura.dias_facturados == 30


def test_create_accepts_a_date_object_for_fecha_lectura() -> None:
    lectura = _create(fecha_lectura=date(2023, 5, 1))

    assert lectura.fecha_lectura == date(2023, 5, 1)


def test_create_rejects_a_datetime_instance_for_fecha_lectura() -> None:
    """`datetime` is a subclass of `date` -- an `isinstance(value, date)` check alone would let
    a `datetime` instance silently pass through as if it were a plain date. Not reachable via the
    JSON boundary today (a JSON payload only ever carries strings), but guards a future adapter
    (a file import, the Oracle ETL per ADR-004) that might pass a `datetime` object directly."""
    with pytest.raises(LecturaValidationError) as exc_info:
        _create(fecha_lectura=datetime(2024, 1, 15, 10, 30))

    assert any("fecha_lectura" in reason for reason in exc_info.value.errors)


def test_create_accepts_numeric_types_for_lectura_values() -> None:
    lectura = _create(lectura_anterior=100, lectura_actual=150.5, dias_facturados="30")

    assert lectura.lectura_anterior == Decimal("100")
    assert lectura.lectura_actual == Decimal("150.5")
    assert lectura.dias_facturados == 30


def test_create_honors_an_explicit_id_for_reconstruction() -> None:
    explicit_id = UUID("11111111-1111-1111-1111-111111111111")

    lectura = _create(id=explicit_id)

    assert lectura.id == explicit_id


def test_create_rejects_a_missing_suministro_id() -> None:
    """RD-015: "Una lectura pertenece a un único suministro."."""
    with pytest.raises(LecturaValidationError) as exc_info:
        _create(suministro_id=None)

    assert any("suministro_id" in reason for reason in exc_info.value.errors)


def test_create_rejects_a_missing_fecha_lectura() -> None:
    with pytest.raises(LecturaValidationError) as exc_info:
        _create(fecha_lectura=None)

    assert any("fecha_lectura" in reason for reason in exc_info.value.errors)


def test_create_rejects_an_invalid_fecha_lectura_format() -> None:
    with pytest.raises(LecturaValidationError) as exc_info:
        _create(fecha_lectura="15/01/2024")

    assert any("fecha_lectura" in reason for reason in exc_info.value.errors)


@pytest.mark.parametrize("field", ["lectura_anterior", "lectura_actual"])
def test_create_rejects_a_missing_lectura_value(field: str) -> None:
    with pytest.raises(LecturaValidationError) as exc_info:
        _create(**{field: None})

    assert any(field in reason for reason in exc_info.value.errors)


@pytest.mark.parametrize("field", ["lectura_anterior", "lectura_actual"])
def test_create_rejects_a_non_numeric_lectura_value(field: str) -> None:
    with pytest.raises(LecturaValidationError) as exc_info:
        _create(**{field: "not-a-number"})

    assert any(field in reason for reason in exc_info.value.errors)


@pytest.mark.parametrize("field", ["lectura_anterior", "lectura_actual"])
def test_create_rejects_a_negative_lectura_value(field: str) -> None:
    """Meters don't go negative -- a stricter domain invariant than the DB CHECKs, which only
    compare lectura_actual against lectura_anterior (RD-013), not against zero."""
    with pytest.raises(LecturaValidationError) as exc_info:
        _create(**{field: "-1"})

    assert any(field in reason and "negativ" in reason for reason in exc_info.value.errors)


@pytest.mark.parametrize("field", ["lectura_anterior", "lectura_actual"])
def test_create_rejects_a_nan_lectura_value(field: str) -> None:
    """`Decimal('nan') < 0` raises `decimal.InvalidOperation` (NaN has no ordering) instead of
    returning a bool -- checking `.is_nan()` before any comparison turns a would-be-uncaught
    exception (and an HTTP 500 for the whole batch) into a per-record rejection reason."""
    with pytest.raises(LecturaValidationError) as exc_info:
        _create(**{field: float("nan")})

    assert any(field in reason for reason in exc_info.value.errors)


@pytest.mark.parametrize("field", ["lectura_anterior", "lectura_actual"])
def test_create_rejects_an_infinite_lectura_value(field: str) -> None:
    """Infinity does not raise on comparison like NaN does, but it is still not a valid meter
    reading -- rejected explicitly (with a clear reason) instead of only incidentally via the
    schema integer-digit-limit check."""
    with pytest.raises(LecturaValidationError) as exc_info:
        _create(**{field: float("inf")})

    assert any(field in reason for reason in exc_info.value.errors)


@pytest.mark.parametrize("field", ["lectura_anterior", "lectura_actual"])
def test_create_rejects_a_lectura_value_with_more_than_three_decimals(field: str) -> None:
    """`lecturas.lectura_anterior`/`lectura_actual` are numeric(12,3) -- more than 3 decimal
    places does not fit the column's scale."""
    with pytest.raises(LecturaValidationError) as exc_info:
        _create(**{field: "100.1234"})

    assert any(field in reason for reason in exc_info.value.errors)


def test_create_accepts_a_lectura_value_whose_trailing_zeros_normalize_within_three_decimals() -> (
    None
):
    """ "100.4560" has 4 digits after the decimal point textually, but the trailing zero is
    insignificant -- it normalizes to 3 decimal places and must not be rejected."""
    lectura = _create(lectura_anterior="100.4560", lectura_actual="100.4560")

    assert lectura.lectura_anterior == Decimal("100.456")


@pytest.mark.parametrize("field", ["lectura_anterior", "lectura_actual"])
def test_create_rejects_a_lectura_value_exceeding_the_schema_integer_digit_limit(
    field: str,
) -> None:
    """numeric(12,3) allows at most 9 digits before the decimal point (12 total - 3 scale)."""
    with pytest.raises(LecturaValidationError) as exc_info:
        _create(**{field: "1000000000"})  # 10 digits

    assert any(field in reason for reason in exc_info.value.errors)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("999999999.999", Decimal("999999999.999")),  # 9 integer digits + 3 decimals: exact fit
        ("0.001", Decimal("0.001")),  # smallest representable scale for numeric(12,3)
        ("1e3", Decimal("1000")),  # scientific notation is a valid Decimal literal too
    ],
)
def test_create_accepts_lectura_values_at_the_schema_precision_boundary(
    value: str, expected: Decimal
) -> None:
    """numeric(12,3) boundary values that must be accepted, not just values comfortably inside
    the range."""
    lectura = _create(lectura_anterior=value, lectura_actual=value)

    assert lectura.lectura_anterior == expected
    assert lectura.lectura_actual == expected


@pytest.mark.parametrize(
    "value",
    [
        "1000000000",  # one digit past the 9-integer-digit limit
        "0.0005",  # one decimal place past the 3-decimal-place scale
    ],
)
def test_create_rejects_lectura_values_just_past_the_schema_precision_boundary(
    value: str,
) -> None:
    """The same boundary from the rejecting side: one unit past what numeric(12,3) allows,
    for both the integer-digit limit and the decimal-scale limit."""
    with pytest.raises(LecturaValidationError) as exc_info:
        _create(lectura_anterior=value, lectura_actual=value)

    assert any(
        "lectura_anterior" in reason or "lectura_actual" in reason
        for reason in exc_info.value.errors
    )


def test_create_rejects_lectura_actual_lower_than_lectura_anterior() -> None:
    """RD-013: "La lectura actual debe ser mayor o igual que la anterior."."""
    with pytest.raises(LecturaValidationError) as exc_info:
        _create(lectura_anterior="200.000", lectura_actual="100.000")

    assert any("lectura_actual" in reason for reason in exc_info.value.errors)


def test_create_accepts_lectura_actual_equal_to_lectura_anterior() -> None:
    lectura = _create(lectura_anterior="100.000", lectura_actual="100.000")

    assert lectura.lectura_actual == lectura.lectura_anterior


def test_create_rejects_a_missing_dias_facturados() -> None:
    with pytest.raises(LecturaValidationError) as exc_info:
        _create(dias_facturados=None)

    assert any("dias_facturados" in reason for reason in exc_info.value.errors)


@pytest.mark.parametrize("dias_facturados", [0, -5])
def test_create_rejects_a_non_positive_dias_facturados(dias_facturados: int) -> None:
    """RD-014: "Los días facturados deben ser mayores que cero."."""
    with pytest.raises(LecturaValidationError) as exc_info:
        _create(dias_facturados=dias_facturados)

    assert any("dias_facturados" in reason for reason in exc_info.value.errors)


def test_create_rejects_a_non_integer_dias_facturados() -> None:
    with pytest.raises(LecturaValidationError) as exc_info:
        _create(dias_facturados="not-a-number")

    assert any("dias_facturados" in reason for reason in exc_info.value.errors)


def test_create_rejects_a_boolean_dias_facturados() -> None:
    """`bool` is a subclass of `int` in Python -- `True`/`False` must not silently pass as 1/0."""
    with pytest.raises(LecturaValidationError) as exc_info:
        _create(dias_facturados=True)

    assert any("dias_facturados" in reason for reason in exc_info.value.errors)


def test_create_rejects_a_dias_facturados_of_an_unsupported_type() -> None:
    """Neither `int`, `str` nor `bool` -- e.g. a `float` sneaking through a loosely-typed
    caller (the JSON boundary types `LecturaSourceRecord.dias_facturados` as `int | str | None`,
    but nothing prevents a caller from passing something else at runtime)."""
    with pytest.raises(LecturaValidationError) as exc_info:
        _create(dias_facturados=30.5)

    assert any("dias_facturados" in reason for reason in exc_info.value.errors)


def test_create_accepts_a_dias_facturados_at_the_postgres_integer_max() -> None:
    """`lecturas.dias_facturados` is a plain Postgres `integer` (docker/postgres/init/
    01_schema.sql) -- 2_147_483_647 is the largest value that column can hold."""
    lectura = _create(dias_facturados=2_147_483_647)

    assert lectura.dias_facturados == 2_147_483_647


def test_create_rejects_a_dias_facturados_one_past_the_postgres_integer_max() -> None:
    with pytest.raises(LecturaValidationError) as exc_info:
        _create(dias_facturados=2_147_483_648)

    assert any("dias_facturados" in reason for reason in exc_info.value.errors)


def test_create_rejects_the_reproduced_out_of_range_dias_facturados() -> None:
    """Same gap `Lote.cantidad_registros` had (the reviewer flagged both as the identical
    pattern): an unbounded Python int passes every prior check and would otherwise die at the
    database as an opaque `DBAPIError` instead of a per-record rejection."""
    with pytest.raises(LecturaValidationError) as exc_info:
        _create(dias_facturados=99999999999)

    assert any("dias_facturados" in reason for reason in exc_info.value.errors)


def test_create_accumulates_every_violated_invariant_in_one_error() -> None:
    with pytest.raises(LecturaValidationError) as exc_info:
        _create(
            suministro_id=None,
            fecha_lectura=None,
            lectura_anterior=None,
            lectura_actual=None,
            dias_facturados=None,
        )

    reasons = exc_info.value.errors
    assert any("suministro_id" in reason for reason in reasons)
    assert any("fecha_lectura" in reason for reason in reasons)
    assert any("lectura_anterior" in reason for reason in reasons)
    assert any("lectura_actual" in reason for reason in reasons)
    assert any("dias_facturados" in reason for reason in reasons)
