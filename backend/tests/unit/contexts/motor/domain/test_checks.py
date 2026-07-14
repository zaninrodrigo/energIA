"""Unit tests for `motor.domain.checks` -- Etapa 1's V1-V7 integrity checks (US-006,
AI_ENGINE_SPEC.md §4.1). Each check is exercised in isolation against hand-built
`ConsumoValidacionRow`s, no database involved (pure domain logic)."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from energia.contexts.motor.domain.checks import (
    TOLERANCIA_KWH_LECTURA,
    check_v1_consumo_sin_lectura,
    check_v2_kwh_vs_delta_lectura,
    check_v3_dias_facturados_mismatch,
    check_v4_continuidad_periodos,
    check_v5_solapamiento_parcial,
    check_v6_categoria_invalida,
    check_v7_valores_negativos,
    ejecutar_checks,
)
from energia.contexts.motor.domain.ports import ConsumoValidacionRow

_SUMINISTRO = uuid4()


def _fila(**overrides: object) -> ConsumoValidacionRow:
    defaults: dict[str, object] = {
        "consumo_id": uuid4(),
        "suministro_id": _SUMINISTRO,
        "fecha_inicio": date(2024, 1, 1),
        "fecha_fin": date(2024, 1, 31),
        "dias_facturados": 31,
        "kwh": Decimal("100.000"),
        "lectura_id": uuid4(),
        "lectura_anterior": Decimal("100.000"),
        "lectura_actual": Decimal("200.000"),
        "lectura_dias_facturados": 31,
        "prev_fecha_inicio": None,
        "prev_fecha_fin": None,
        "categoria_deleted_at": None,
    }
    defaults.update(overrides)
    return ConsumoValidacionRow(**defaults)  # type: ignore[arg-type]


def test_v1_flags_consumo_without_lectura() -> None:
    fila = _fila(lectura_id=None, lectura_anterior=None, lectura_actual=None)

    hallazgos = check_v1_consumo_sin_lectura([fila])

    assert len(hallazgos) == 1
    assert hallazgos[0].check == "V1"
    assert hallazgos[0].suministro_id == _SUMINISTRO
    assert hallazgos[0].consumo_id == fila.consumo_id


def test_v1_passes_when_lectura_present() -> None:
    fila = _fila()
    assert check_v1_consumo_sin_lectura([fila]) == []


def test_v2_passes_within_tolerance() -> None:
    # delta = 200.000 - 100.000 = 100.000; kwh matches exactly.
    fila = _fila(kwh=Decimal("100.000"))
    assert check_v2_kwh_vs_delta_lectura([fila]) == []


def test_v2_passes_at_exact_tolerance_boundary() -> None:
    fila = _fila(kwh=Decimal("100.000") + TOLERANCIA_KWH_LECTURA)
    assert check_v2_kwh_vs_delta_lectura([fila]) == []


def test_v2_flags_kwh_beyond_tolerance() -> None:
    fila = _fila(kwh=Decimal("150.000"))  # delta is 100.000, way off

    hallazgos = check_v2_kwh_vs_delta_lectura([fila])

    assert len(hallazgos) == 1
    assert hallazgos[0].check == "V2"


def test_v2_skips_rows_without_lectura_data() -> None:
    fila = _fila(lectura_id=None, lectura_anterior=None, lectura_actual=None, kwh=Decimal("999"))
    assert check_v2_kwh_vs_delta_lectura([fila]) == []


def test_v3_flags_dias_facturados_mismatch() -> None:
    fila = _fila(dias_facturados=31, lectura_dias_facturados=30)

    hallazgos = check_v3_dias_facturados_mismatch([fila])

    assert len(hallazgos) == 1
    assert hallazgos[0].check == "V3"


def test_v3_passes_when_dias_facturados_match() -> None:
    fila = _fila(dias_facturados=31, lectura_dias_facturados=31)
    assert check_v3_dias_facturados_mismatch([fila]) == []


def test_v3_skips_rows_without_lectura() -> None:
    fila = _fila(lectura_dias_facturados=None)
    assert check_v3_dias_facturados_mismatch([fila]) == []


def test_v4_flags_gap_between_consecutive_periods() -> None:
    fila = _fila(
        fecha_inicio=date(2024, 3, 1),
        fecha_fin=date(2024, 3, 31),
        prev_fecha_inicio=date(2024, 1, 1),
        prev_fecha_fin=date(2024, 1, 31),
    )

    hallazgos = check_v4_continuidad_periodos([fila])

    assert len(hallazgos) == 1
    assert hallazgos[0].check == "V4"


def test_v4_passes_when_periods_are_contiguous() -> None:
    fila = _fila(
        fecha_inicio=date(2024, 2, 1),
        fecha_fin=date(2024, 2, 29),
        prev_fecha_inicio=date(2024, 1, 1),
        prev_fecha_fin=date(2024, 1, 31),
    )
    assert check_v4_continuidad_periodos([fila]) == []


def test_v4_passes_when_no_previous_period_exists() -> None:
    fila = _fila(prev_fecha_inicio=None, prev_fecha_fin=None)
    assert check_v4_continuidad_periodos([fila]) == []


def test_v4_flags_smallest_possible_gap_of_one_day() -> None:
    """Boundary: a single missing day (2024-02-01) between the previous period's end
    (2024-01-31) and this one's start (2024-02-02) is already enough for V4 to fire -- the
    smallest gap the check detects, not just an obviously-large one (RD-017)."""
    fila = _fila(
        fecha_inicio=date(2024, 2, 2),
        fecha_fin=date(2024, 2, 29),
        prev_fecha_inicio=date(2024, 1, 1),
        prev_fecha_fin=date(2024, 1, 31),
    )

    hallazgos = check_v4_continuidad_periodos([fila])

    assert len(hallazgos) == 1
    assert hallazgos[0].check == "V4"


def test_v5_fires_not_v4_when_fecha_inicio_equals_prev_fecha_fin() -> None:
    """Boundary: `fecha_inicio == prev_fecha_fin` is a same-day overlap (V5), never a gap (V4) --
    the two checks are mutually exclusive at this exact boundary value."""
    fila = _fila(
        fecha_inicio=date(2024, 1, 31),
        fecha_fin=date(2024, 2, 28),
        prev_fecha_inicio=date(2024, 1, 1),
        prev_fecha_fin=date(2024, 1, 31),
    )

    assert check_v4_continuidad_periodos([fila]) == []

    hallazgos_v5 = check_v5_solapamiento_parcial([fila])
    assert len(hallazgos_v5) == 1
    assert hallazgos_v5[0].check == "V5"


def test_v5_flags_partial_overlap_with_previous_period() -> None:
    fila = _fila(
        fecha_inicio=date(2024, 1, 15),
        fecha_fin=date(2024, 2, 15),
        prev_fecha_inicio=date(2024, 1, 1),
        prev_fecha_fin=date(2024, 1, 31),
    )

    hallazgos = check_v5_solapamiento_parcial([fila])

    assert len(hallazgos) == 1
    assert hallazgos[0].check == "V5"


def test_v5_passes_when_periods_do_not_overlap() -> None:
    fila = _fila(
        fecha_inicio=date(2024, 2, 1),
        fecha_fin=date(2024, 2, 29),
        prev_fecha_inicio=date(2024, 1, 1),
        prev_fecha_fin=date(2024, 1, 31),
    )
    assert check_v5_solapamiento_parcial([fila]) == []


def test_v6_flags_soft_deleted_categoria() -> None:
    from datetime import UTC, datetime

    fila = _fila(categoria_deleted_at=datetime(2026, 1, 1, tzinfo=UTC))

    hallazgos = check_v6_categoria_invalida([fila])

    assert len(hallazgos) == 1
    assert hallazgos[0].check == "V6"


def test_v6_passes_when_categoria_is_active() -> None:
    fila = _fila(categoria_deleted_at=None)
    assert check_v6_categoria_invalida([fila]) == []


def test_v7_flags_negative_kwh() -> None:
    fila = _fila(kwh=Decimal("-1.000"))

    hallazgos = check_v7_valores_negativos([fila])

    assert any(h.check == "V7" for h in hallazgos)


def test_v7_flags_non_positive_dias_facturados() -> None:
    fila = _fila(dias_facturados=0)

    hallazgos = check_v7_valores_negativos([fila])

    assert any(h.check == "V7" for h in hallazgos)


def test_v7_passes_for_valid_row() -> None:
    fila = _fila()
    assert check_v7_valores_negativos([fila]) == []


def test_ejecutar_checks_runs_all_seven_and_aggregates_findings() -> None:
    fila_limpia = _fila()
    fila_sin_lectura = _fila(lectura_id=None, lectura_anterior=None, lectura_actual=None)

    hallazgos = ejecutar_checks([fila_limpia, fila_sin_lectura])

    checks_disparados = {h.check for h in hallazgos}
    assert checks_disparados == {"V1"}
    assert all(h.consumo_id == fila_sin_lectura.consumo_id for h in hallazgos)
