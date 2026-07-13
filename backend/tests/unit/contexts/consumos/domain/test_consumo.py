"""Unit tests for `Consumo.create()` (DOMAIN_MODEL.md §7.6, US-004).

Mirrors `test_lectura.py`'s style: every invariant, every parsing edge case, isolated from any
database. `consumo_promedio_diario`'s three-state (`UNSET`/`None`/value) behavior is the one
genuinely new shape here -- see `domain/consumo.py`'s module docstring for the full rationale.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from energia.contexts.consumos.domain.consumo import Consumo, ConsumoValidationError
from energia.contexts.consumos.domain.sentinels import UNSET

_SUMINISTRO_ID = uuid4()
_LOTE_ID = uuid4()
_LECTURA_ID = uuid4()


def _valid_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "suministro_id": _SUMINISTRO_ID,
        "lote_id": _LOTE_ID,
        "fecha_inicio": "2024-01-01",
        "fecha_fin": "2024-01-31",
        "dias_facturados": 31,
        "kwh": "310.000",
    }
    kwargs.update(overrides)
    return kwargs


def test_create_builds_a_valid_consumo_with_a_fresh_id() -> None:
    consumo = Consumo.create(**_valid_kwargs())

    assert isinstance(consumo.id, UUID)
    assert consumo.suministro_id == _SUMINISTRO_ID
    assert consumo.lote_id == _LOTE_ID
    assert consumo.lectura_id is None
    assert consumo.fecha_inicio == date(2024, 1, 1)
    assert consumo.fecha_fin == date(2024, 1, 31)
    assert consumo.dias_facturados == 31
    assert consumo.kwh == Decimal("310.000")


def test_create_accepts_a_given_id_and_lectura_id() -> None:
    fixed_id = uuid4()
    consumo = Consumo.create(**_valid_kwargs(id=fixed_id, lectura_id=_LECTURA_ID))

    assert consumo.id == fixed_id
    assert consumo.lectura_id == _LECTURA_ID


def test_create_accepts_date_instances_directly() -> None:
    consumo = Consumo.create(
        **_valid_kwargs(fecha_inicio=date(2024, 1, 1), fecha_fin=date(2024, 1, 31))
    )

    assert consumo.fecha_inicio == date(2024, 1, 1)
    assert consumo.fecha_fin == date(2024, 1, 31)


# -- consumo_promedio_diario: derivation (DOMAIN_MODEL.md §7.6 calcularPromedioDiario()) --------


def test_omitted_consumo_promedio_diario_is_computed_from_kwh_and_dias_facturados() -> None:
    consumo = Consumo.create(**_valid_kwargs(kwh="100", dias_facturados=3))

    # 100 / 3 = 33.333... -> quantized to numeric(12,3).
    assert consumo.consumo_promedio_diario == Decimal("33.333")


def test_omitted_consumo_promedio_diario_default_is_unset_not_a_required_kwarg() -> None:
    kwargs = _valid_kwargs()
    assert "consumo_promedio_diario" not in kwargs
    Consumo.create(**kwargs)  # must not raise


def test_explicit_null_consumo_promedio_diario_stores_null_without_computing() -> None:
    consumo = Consumo.create(
        **_valid_kwargs(kwh="100", dias_facturados=3, consumo_promedio_diario=None)
    )

    assert consumo.consumo_promedio_diario is None


def test_explicit_consumo_promedio_diario_value_is_stored_as_given() -> None:
    consumo = Consumo.create(**_valid_kwargs(consumo_promedio_diario="12.5"))

    assert consumo.consumo_promedio_diario == Decimal("12.5")


def test_explicit_unset_sentinel_computes_the_same_as_a_truly_omitted_argument() -> None:
    consumo = Consumo.create(
        **_valid_kwargs(kwh="100", dias_facturados=3, consumo_promedio_diario=UNSET)
    )

    assert consumo.consumo_promedio_diario == Decimal("33.333")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), -1, -0.01])
def test_explicit_consumo_promedio_diario_rejects_the_same_invalid_shapes_as_kwh(
    value: float,
) -> None:
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(consumo_promedio_diario=value))

    assert any("consumo_promedio_diario" in reason for reason in exc_info.value.errors)


# -- suministro_id / lote_id: RD-004, RD-012/RD-019 ---------------------------------------------


def test_missing_suministro_id_is_rejected() -> None:
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(suministro_id=None))

    assert any("suministro_id" in reason for reason in exc_info.value.errors)


def test_missing_lote_id_is_rejected() -> None:
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(lote_id=None))

    assert any("lote_id" in reason for reason in exc_info.value.errors)


# -- fecha_inicio / fecha_fin --------------------------------------------------------------------


def test_missing_fecha_inicio_is_rejected() -> None:
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(fecha_inicio=None))

    assert any("fecha_inicio" in reason for reason in exc_info.value.errors)


def test_missing_fecha_fin_is_rejected() -> None:
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(fecha_fin=None))

    assert any("fecha_fin" in reason for reason in exc_info.value.errors)


def test_malformed_fecha_inicio_is_rejected() -> None:
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(fecha_inicio="not-a-date"))

    assert any("fecha_inicio" in reason for reason in exc_info.value.errors)


def test_datetime_instance_for_fecha_inicio_is_rejected() -> None:
    """`datetime` is a subclass of `date` -- must be checked explicitly and first (mirrors
    `Lectura.create()`'s `fecha_lectura` guard), or it would silently be accepted."""
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(fecha_inicio=datetime(2024, 1, 1, 12, 0)))

    assert any("fecha_inicio" in reason for reason in exc_info.value.errors)


def test_datetime_instance_for_fecha_fin_is_rejected() -> None:
    """Same guard as `fecha_inicio`, for `fecha_fin` -- both go through the shared
    `_parse_fecha` helper, but each call site must independently exercise the guard."""
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(fecha_fin=datetime(2024, 1, 31, 23, 59)))

    assert any("fecha_fin" in reason for reason in exc_info.value.errors)


def test_fecha_fin_before_fecha_inicio_is_rejected() -> None:
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(fecha_inicio="2024-02-01", fecha_fin="2024-01-01"))

    assert any("fecha_fin" in reason for reason in exc_info.value.errors)


def test_fecha_fin_equal_to_fecha_inicio_is_accepted() -> None:
    consumo = Consumo.create(**_valid_kwargs(fecha_inicio="2024-01-01", fecha_fin="2024-01-01"))

    assert consumo.fecha_inicio == consumo.fecha_fin


# -- dias_facturados: RD analogous to RD-014, plus the int4 bound ------------------------------


def test_missing_dias_facturados_is_rejected() -> None:
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(dias_facturados=None))

    assert any("dias_facturados" in reason for reason in exc_info.value.errors)


def test_zero_dias_facturados_is_rejected() -> None:
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(dias_facturados=0))

    assert any("dias_facturados" in reason for reason in exc_info.value.errors)


def test_negative_dias_facturados_is_rejected() -> None:
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(dias_facturados=-5))

    assert any("dias_facturados" in reason for reason in exc_info.value.errors)


def test_boolean_dias_facturados_is_rejected() -> None:
    """`bool` is a subclass of `int` in Python -- must not silently pass as 1/0."""
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(dias_facturados=True))

    assert any("dias_facturados" in reason for reason in exc_info.value.errors)


def test_dias_facturados_over_the_int4_bound_is_rejected() -> None:
    """`consumos.dias_facturados` is a plain Postgres `integer`, not a `bigint` -- bounded here so
    an over-large value is rejected before it ever reaches the database as an opaque error."""
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(dias_facturados=99999999999))

    assert any("dias_facturados" in reason for reason in exc_info.value.errors)


def test_dias_facturados_as_a_numeric_string_is_accepted() -> None:
    consumo = Consumo.create(**_valid_kwargs(dias_facturados="30"))

    assert consumo.dias_facturados == 30


# -- kwh: RD-016 ----------------------------------------------------------------------------------


def test_missing_kwh_is_rejected() -> None:
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(kwh=None))

    assert any("kwh" in reason for reason in exc_info.value.errors)


def test_negative_kwh_is_rejected() -> None:
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(kwh=-10))

    assert any("kwh" in reason for reason in exc_info.value.errors)


def test_zero_kwh_is_accepted() -> None:
    consumo = Consumo.create(**_valid_kwargs(kwh=0))

    assert consumo.kwh == Decimal("0")


def test_nan_kwh_is_rejected() -> None:
    """`Decimal('nan') < 0` does not return `False` -- it raises `InvalidOperation` -- so NaN must
    be guarded before any ordering comparison, or one bad record would 500 the whole batch."""
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(kwh=float("nan")))

    assert any("NaN" in reason for reason in exc_info.value.errors)


def test_infinite_kwh_is_rejected() -> None:
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(kwh=float("inf")))

    assert any("infinito" in reason for reason in exc_info.value.errors)


def test_kwh_with_more_than_three_decimals_is_rejected() -> None:
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(kwh="10.1234"))

    assert any("decimales" in reason for reason in exc_info.value.errors)


def test_kwh_over_the_numeric_12_3_bound_is_rejected() -> None:
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(kwh="1" + "0" * 9))

    assert any("kwh" in reason for reason in exc_info.value.errors)


def test_non_numeric_kwh_is_rejected() -> None:
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(kwh="not-a-number"))

    assert any("kwh" in reason for reason in exc_info.value.errors)


def test_kwh_given_as_a_decimal_instance_directly_is_accepted() -> None:
    consumo = Consumo.create(**_valid_kwargs(kwh=Decimal("42.500")))

    assert consumo.kwh == Decimal("42.500")


def test_dias_facturados_given_as_a_non_numeric_string_is_rejected() -> None:
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(dias_facturados="treinta"))

    assert any("dias_facturados" in reason for reason in exc_info.value.errors)


def test_dias_facturados_given_as_an_unsupported_type_is_rejected() -> None:
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(dias_facturados=30.5))  # type: ignore[arg-type]

    assert any("dias_facturados" in reason for reason in exc_info.value.errors)


# -- multiple violations are all reported, not just the first ------------------------------------


def test_multiple_violations_are_all_reported_not_just_the_first() -> None:
    with pytest.raises(ConsumoValidationError) as exc_info:
        Consumo.create(**_valid_kwargs(suministro_id=None, lote_id=None, kwh=-1, dias_facturados=0))

    joined = "; ".join(exc_info.value.errors)
    assert "suministro_id" in joined
    assert "lote_id" in joined
    assert "kwh" in joined
    assert "dias_facturados" in joined
