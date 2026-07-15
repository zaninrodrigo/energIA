"""Unit tests for Etapa 3 (US-008, features) + Etapa 4 (US-009, indicadores estadísticos)
pure domain functions -- AI_ENGINE_SPEC.md §6/§7. No database: every history row is a plain
`FeatureConsumoRow` built by hand, and every expected value is hand-computed in this file's own
docstrings/comments (not re-derived from the implementation under test).

Conventions mirrored from `test_duplicidades.py`/`test_checks.py`: pure functions over
already-fetched rows, no I/O.
"""

import math
from datetime import date
from decimal import Decimal
from uuid import uuid4

from energia.contexts.motor.domain.features import (
    COHORTE_FALLBACK_MINIMA,
    COHORTE_MINIMA,
    UMBRAL_PERCENTILE_EXTREMO,
    UMBRAL_ZSCORE_EXTREMO,
    FeatureVector,
    construir_feature_vector,
    enriquecer_con_indicadores_cohorte,
    resumir_features,
)
from energia.contexts.motor.domain.ports import FeatureConsumoRow


def _fila(
    *, suministro_id, lote_id, fecha_inicio: date, kwh: float, dias_facturados: int = 30
) -> FeatureConsumoRow:
    return FeatureConsumoRow(
        consumo_id=uuid4(),
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_inicio,
        dias_facturados=dias_facturados,
        kwh=Decimal(str(kwh)),
    )


def _mes(year: int, month: int) -> date:
    return date(year, month, 1)


# ---------------------------------------------------------------------------------------------
# Fixture A: 14 monthly periods, Jan-2023..Feb-2024, kwh strictly linear 100..230 step 10.
# Hand-computed expectations documented inline per assertion.
# ---------------------------------------------------------------------------------------------


def _serie_lineal(
    suministro_id, lote_id_por_mes: dict[tuple[int, int], object]
) -> list[FeatureConsumoRow]:
    """14 rows, one per calendar month Jan-2023..Feb-2024, kwh = 100, 110, ..., 230 (step 10).
    `lote_id_por_mes` maps `(year, month) -> lote_id` for the periods whose lote_id matters to
    the test (the "current" period); every other period gets a throwaway lote_id (its own past
    is cross-lote history, exactly like `duplicidades`' fetch scope)."""
    filas = []
    year, month = 2023, 1
    kwh = 100.0
    for _ in range(14):
        lote_id = lote_id_por_mes.get((year, month), uuid4())
        filas.append(
            _fila(
                suministro_id=suministro_id,
                lote_id=lote_id,
                fecha_inicio=_mes(year, month),
                kwh=kwh,
                dias_facturados=28,
            )
        )
        kwh += 10.0
        month += 1
        if month == 13:
            month = 1
            year += 1
    return filas


def test_full_feature_vector_against_hand_computed_values() -> None:
    """F1-F12, F14, F16, F17, is_cold_start, zscore_self, has_conflicted_periods against values
    hand-computed for the 14-point linear series ending 2024-02 (index 13, kwh=230):

    Trailing-12 window (indices 2..13, kwh 120..230 step 10, n=12):
      avg=175, max=230, min=120, sample stdev=sqrt(1300)=36.05551275463989
    F5 pct_change_prev_period = (230-220)/220 = 0.045454545454545456
    F6 pct_change_yoy: same month (Feb) 12 months back = index1 (Feb-2023, kwh=110):
      (230-110)/110 = 1.0909090909090908
    F7 moving_avg_6m (indices 8..13, kwh 180..230): avg = 205.0
    F8 moving_avg_12m == F1's avg = 175.0 (same window, see module docstring's honesty note)
    F9 deviation_from_baseline = (230-175)/36.05551275463989 = 1.5257072987160172
    F10 zero_consumption_streak = 0 (no zero periods)
    F11 trend_slope over the SAME trailing-12 window, x=0..11 (period index), y=120..230:
      perfectly linear with step 10 -> slope == 10.0 exactly
    F12 seasonality_index: only one prior Feb (index1, kwh=110) -> 230/110 = 2.090909090909091
    zscore_self over FULL 14-point history (mean=165, sample stdev=sqrt(1750)=41.83300132670378):
      (230-165)/41.83300132670378 = 1.5537249106279687
    """
    suministro_id = uuid4()
    lote_id = uuid4()
    filas = _serie_lineal(suministro_id, {(2024, 2): lote_id})
    categoria_id = uuid4()
    fecha_alta = date(2020, 1, 1)

    vector = construir_feature_vector(
        suministro_id=suministro_id,
        lote_id=lote_id,
        historial_suministro=filas,
        categoria_tarifaria_id=categoria_id,
        localidad="Formosa",
        fecha_alta=fecha_alta,
        prior_anomaly_count=2,
        tiene_periodos_conflictivos=False,
    )

    assert vector is not None
    f = vector.features

    assert f["avg_consumption"] == 175.0
    assert f["max_consumption"] == 230.0
    assert f["min_consumption"] == 120.0
    assert f["stddev_consumption"] == math.sqrt(1300)
    assert f["pct_change_prev_period"] == (230.0 - 220.0) / 220.0
    assert f["pct_change_yoy"] == (230.0 - 110.0) / 110.0
    assert f["moving_avg_6m"] == 205.0
    assert f["moving_avg_12m"] == 175.0
    assert f["deviation_from_baseline"] == (230.0 - 175.0) / math.sqrt(1300)
    assert f["zero_consumption_streak"] == 0
    assert f["trend_slope"] == 10.0
    assert f["seasonality_index"] == 230.0 / 110.0
    assert f["supply_age_days"] == (date(2024, 2, 1) - fecha_alta).days
    assert f["prior_anomaly_count"] == 2
    assert f["billed_days"] == 28
    assert f["categoria_tarifaria"] == str(categoria_id)
    assert f["has_conflicted_periods"] is False
    assert f["is_cold_start"] is False
    assert f["zscore_self"] == (230.0 - 165.0) / math.sqrt(1750)

    assert vector.is_cold_start is False
    assert vector.has_conflicted_periods is False
    assert vector.kwh_actual == 230.0
    assert vector.categoria_tarifaria_id == categoria_id
    assert vector.localidad == "Formosa"


def test_no_row_for_this_lote_returns_none() -> None:
    """A suministro with history, but none of it belonging to `lote_id`, cannot be scored for
    that lote -- this is the v1 policy for a suministro whose ONLY period(s) in this lote were
    all excluded as conflicted upstream (AI_ENGINE_SPEC.md §6 note): the caller never includes
    such a row in `historial_suministro` for this lote's `lote_id`, so this function correctly
    has nothing to anchor "current" to."""
    suministro_id = uuid4()
    filas = _serie_lineal(suministro_id, {})  # no row tagged with our `lote_id` below

    vector = construir_feature_vector(
        suministro_id=suministro_id,
        lote_id=uuid4(),
        historial_suministro=filas,
        categoria_tarifaria_id=uuid4(),
        localidad=None,
        fecha_alta=date(2020, 1, 1),
        prior_anomaly_count=0,
        tiene_periodos_conflictivos=False,
    )

    assert vector is None


def test_future_periods_are_ignored_for_window_math() -> None:
    """A row with `fecha_inicio` AFTER the current period (e.g. a later lote's data already
    imported) must never leak into "as of this period" window math -- windows only look
    backward from the current period, never forward."""
    suministro_id = uuid4()
    lote_id = uuid4()
    filas = [
        _fila(suministro_id=suministro_id, lote_id=lote_id, fecha_inicio=_mes(2024, 1), kwh=100.0),
        _fila(
            suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 2), kwh=99999.0
        ),
    ]

    vector = construir_feature_vector(
        suministro_id=suministro_id,
        lote_id=lote_id,
        historial_suministro=filas,
        categoria_tarifaria_id=uuid4(),
        localidad=None,
        fecha_alta=date(2020, 1, 1),
        prior_anomaly_count=0,
        tiene_periodos_conflictivos=False,
    )

    assert vector is not None
    assert vector.features["avg_consumption"] == 100.0
    assert vector.features["max_consumption"] == 100.0


def test_conflicted_period_exclusion_is_a_prior_filtering_concern() -> None:
    """`construir_feature_vector` has no notion of "conflicted" itself -- excluding a
    conflicted period (DEC-005, AI_ENGINE_SPEC.md §6 note) is done by the CALLER
    (`ProcesarLote._generar_features`) before ever building `historial_suministro`. This test
    demonstrates the contrast directly: the SAME current period, with and without an extra
    (would-be-conflicted) row present in the history passed in, produces different window
    stats -- proving the function faithfully reflects whatever history it is given, which is
    exactly what makes upstream filtering effective."""
    suministro_id = uuid4()
    lote_id = uuid4()
    conflictiva = _fila(
        suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2023, 12), kwh=1000.0
    )
    actual = _fila(
        suministro_id=suministro_id, lote_id=lote_id, fecha_inicio=_mes(2024, 1), kwh=100.0
    )

    con_conflicto = construir_feature_vector(
        suministro_id=suministro_id,
        lote_id=lote_id,
        historial_suministro=[conflictiva, actual],
        categoria_tarifaria_id=uuid4(),
        localidad=None,
        fecha_alta=date(2020, 1, 1),
        prior_anomaly_count=0,
        tiene_periodos_conflictivos=True,
    )
    sin_conflicto = construir_feature_vector(
        suministro_id=suministro_id,
        lote_id=lote_id,
        historial_suministro=[actual],  # caller already excluded the conflicted row
        categoria_tarifaria_id=uuid4(),
        localidad=None,
        fecha_alta=date(2020, 1, 1),
        prior_anomaly_count=0,
        tiene_periodos_conflictivos=True,
    )

    assert con_conflicto is not None and sin_conflicto is not None
    assert con_conflicto.features["avg_consumption"] == (1000.0 + 100.0) / 2
    assert con_conflicto.features["max_consumption"] == 1000.0
    assert sin_conflicto.features["avg_consumption"] == 100.0
    assert sin_conflicto.features["max_consumption"] == 100.0
    # `has_conflicted_periods` is a passthrough flag the caller supplies, independent of whether
    # the conflicted row is actually present in `historial_suministro` this time.
    assert con_conflicto.features["has_conflicted_periods"] is True
    assert sin_conflicto.features["has_conflicted_periods"] is True


# ---------------------------------------------------------------------------------------------
# Cold-start (DEC-006/DEC-007, AI_ENGINE_SPEC.md §6.2): history < 3 periods.
# ---------------------------------------------------------------------------------------------


def test_cold_start_suministro_nulls_where_mandated() -> None:
    """A suministro with only 2 periods (< MINIMO_PERIODOS_DESVIO=3, DEC-007): `is_cold_start`
    true, F4/F9/zscore_self null (need >=3), F11 null (needs >=4), F6/F12 fall back to their
    documented cold-start defaults (null / 1.0 neutral) since there is no 12-months-back or
    prior-year same-month data at all. F1/F2/F3/F7/F8 still compute over "lo disponible" (2
    periods): avg=(100+150)/2=125, max=150, min=100. F5 IS computable (only needs 1 prior
    period): (150-100)/100 = 0.5."""
    suministro_id = uuid4()
    lote_id = uuid4()
    filas = [
        _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 1), kwh=100.0),
        _fila(suministro_id=suministro_id, lote_id=lote_id, fecha_inicio=_mes(2024, 2), kwh=150.0),
    ]

    vector = construir_feature_vector(
        suministro_id=suministro_id,
        lote_id=lote_id,
        historial_suministro=filas,
        categoria_tarifaria_id=uuid4(),
        localidad=None,
        fecha_alta=date(2023, 1, 1),
        prior_anomaly_count=0,
        tiene_periodos_conflictivos=False,
    )

    assert vector is not None
    f = vector.features
    assert vector.is_cold_start is True
    assert f["is_cold_start"] is True
    assert f["avg_consumption"] == 125.0
    assert f["max_consumption"] == 150.0
    assert f["min_consumption"] == 100.0
    assert f["stddev_consumption"] is None
    assert f["pct_change_prev_period"] == 0.5
    assert f["pct_change_yoy"] is None
    assert f["deviation_from_baseline"] is None
    assert f["trend_slope"] is None
    assert f["seasonality_index"] == 1.0  # no prior year, same month -> neutral
    assert f["zscore_self"] is None


def test_no_previous_period_at_all_nulls_pct_change_prev_period() -> None:
    """F5: a suministro's FIRST ever period has no `t-1` to compare against -- null, not 0
    (§6.2: null NEVER 0 for insufficient history)."""
    suministro_id = uuid4()
    lote_id = uuid4()
    filas = [
        _fila(suministro_id=suministro_id, lote_id=lote_id, fecha_inicio=_mes(2024, 1), kwh=100.0)
    ]

    vector = construir_feature_vector(
        suministro_id=suministro_id,
        lote_id=lote_id,
        historial_suministro=filas,
        categoria_tarifaria_id=uuid4(),
        localidad=None,
        fecha_alta=date(2023, 1, 1),
        prior_anomaly_count=0,
        tiene_periodos_conflictivos=False,
    )

    assert vector is not None
    assert vector.features["pct_change_prev_period"] is None
    assert vector.is_cold_start is True  # 1 period < 3


def test_zero_previous_period_kwh_nulls_pct_change_prev_period_division_guard() -> None:
    """F5's denominator guard: `kwh_{t-1} == 0` makes the percent change mathematically
    undefined (division by zero) -- treated as null, not an exception or an infinite value."""
    suministro_id = uuid4()
    lote_id = uuid4()
    filas = [
        _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 1), kwh=0.0),
        _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 2), kwh=0.0),
        _fila(suministro_id=suministro_id, lote_id=lote_id, fecha_inicio=_mes(2024, 3), kwh=50.0),
    ]

    vector = construir_feature_vector(
        suministro_id=suministro_id,
        lote_id=lote_id,
        historial_suministro=filas,
        categoria_tarifaria_id=uuid4(),
        localidad=None,
        fecha_alta=date(2023, 1, 1),
        prior_anomaly_count=0,
        tiene_periodos_conflictivos=False,
    )

    assert vector is not None
    assert vector.features["pct_change_prev_period"] is None


def test_zero_consumption_streak_counts_backward_from_current() -> None:
    """F10: 4 consecutive zero-kwh periods immediately before (and including) the current one,
    then the streak resets to 0 as soon as a non-zero period is hit walking backward."""
    suministro_id = uuid4()
    lote_id = uuid4()
    filas = [
        _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 1), kwh=100.0),
        _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 2), kwh=0.0),
        _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 3), kwh=0.0),
        _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 4), kwh=0.0),
        _fila(suministro_id=suministro_id, lote_id=lote_id, fecha_inicio=_mes(2024, 5), kwh=0.0),
    ]

    vector = construir_feature_vector(
        suministro_id=suministro_id,
        lote_id=lote_id,
        historial_suministro=filas,
        categoria_tarifaria_id=uuid4(),
        localidad=None,
        fecha_alta=date(2023, 1, 1),
        prior_anomaly_count=0,
        tiene_periodos_conflictivos=False,
    )

    assert vector is not None
    assert vector.features["zero_consumption_streak"] == 4


def test_non_zero_current_period_has_zero_streak() -> None:
    suministro_id = uuid4()
    lote_id = uuid4()
    filas = [
        _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 1), kwh=0.0),
        _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 2), kwh=0.0),
        _fila(suministro_id=suministro_id, lote_id=lote_id, fecha_inicio=_mes(2024, 3), kwh=50.0),
    ]

    vector = construir_feature_vector(
        suministro_id=suministro_id,
        lote_id=lote_id,
        historial_suministro=filas,
        categoria_tarifaria_id=uuid4(),
        localidad=None,
        fecha_alta=date(2023, 1, 1),
        prior_anomaly_count=0,
        tiene_periodos_conflictivos=False,
    )

    assert vector is not None
    assert vector.features["zero_consumption_streak"] == 0


def test_zero_streak_bridges_conflicted_nonzero_period_using_unfiltered_history() -> None:
    """FIX 1 (reviewer finding, CRITICAL): F10 must count over the UNFILTERED history, not the
    conflicted-excluded one every OTHER window feature uses -- a conflicted period that had REAL,
    NONZERO consumption (e.g. a double-billed month) still evidences the supply was active, and
    must break a zero-streak even though it never appears in `historial_suministro` (the caller
    already stripped conflicted rows out of that one, DEC-005). Jan=0, Feb=500 (conflicted --
    present in `historial_suministro_completo` but absent from `historial_suministro`), Mar=0,
    Apr=0 (current): walking backward from Apr, Feb's 500 breaks the streak at Mar -> streak == 2
    (Mar, Apr), NOT 3 -- the bug this fix corrects, which bridges Jan+Mar+Apr as if Feb never
    happened, reaching R1's Alta-severity trigger `>= 3` (AI_ENGINE_SPEC.md §8) on a false
    signal."""
    suministro_id = uuid4()
    lote_id = uuid4()
    enero = _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 1), kwh=0.0)
    febrero_conflictiva = _fila(
        suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 2), kwh=500.0
    )
    marzo = _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 3), kwh=0.0)
    abril_actual = _fila(
        suministro_id=suministro_id, lote_id=lote_id, fecha_inicio=_mes(2024, 4), kwh=0.0
    )
    historial_filtrado = [enero, marzo, abril_actual]  # caller already excluded febrero_conflictiva
    historial_completo = [enero, febrero_conflictiva, marzo, abril_actual]

    vector = construir_feature_vector(
        suministro_id=suministro_id,
        lote_id=lote_id,
        historial_suministro=historial_filtrado,
        historial_suministro_completo=historial_completo,
        categoria_tarifaria_id=uuid4(),
        localidad=None,
        fecha_alta=date(2023, 1, 1),
        prior_anomaly_count=0,
        tiene_periodos_conflictivos=True,
    )

    assert vector is not None
    assert vector.features["zero_consumption_streak"] == 2
    # Every OTHER feature keeps reading the FILTERED history, unaffected by this fix.
    assert vector.features["avg_consumption"] == 0.0


def test_zero_streak_continues_through_a_conflicted_period_that_is_itself_zero() -> None:
    """The counterpart case (FIX 1): a conflicted period whose OWN kwh is 0 evidences nothing --
    no real consumption to break the streak -- so it continues counting through it exactly like
    any other zero period would. The fix is about not LOSING a nonzero conflicted period's
    signal, not about treating every conflicted period as automatically streak-breaking."""
    suministro_id = uuid4()
    lote_id = uuid4()
    enero = _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 1), kwh=0.0)
    febrero_conflictiva_cero = _fila(
        suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 2), kwh=0.0
    )
    marzo = _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 3), kwh=0.0)
    abril_actual = _fila(
        suministro_id=suministro_id, lote_id=lote_id, fecha_inicio=_mes(2024, 4), kwh=0.0
    )
    historial_filtrado = [enero, marzo, abril_actual]
    historial_completo = [enero, febrero_conflictiva_cero, marzo, abril_actual]

    vector = construir_feature_vector(
        suministro_id=suministro_id,
        lote_id=lote_id,
        historial_suministro=historial_filtrado,
        historial_suministro_completo=historial_completo,
        categoria_tarifaria_id=uuid4(),
        localidad=None,
        fecha_alta=date(2023, 1, 1),
        prior_anomaly_count=0,
        tiene_periodos_conflictivos=True,
    )

    assert vector is not None
    assert vector.features["zero_consumption_streak"] == 4


def test_zero_streak_defaults_to_filtered_history_when_completo_omitted() -> None:
    """Backward-compatibility guard: every existing caller/test that predates FIX 1 does not pass
    `historial_suministro_completo` at all -- F10 must fall back to `historial_suministro` itself
    (i.e. behave exactly as before this fix), not raise or silently score against an empty
    history."""
    suministro_id = uuid4()
    lote_id = uuid4()
    filas = [
        _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 1), kwh=0.0),
        _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 2), kwh=0.0),
        _fila(suministro_id=suministro_id, lote_id=lote_id, fecha_inicio=_mes(2024, 3), kwh=0.0),
    ]

    vector = construir_feature_vector(
        suministro_id=suministro_id,
        lote_id=lote_id,
        historial_suministro=filas,
        categoria_tarifaria_id=uuid4(),
        localidad=None,
        fecha_alta=date(2023, 1, 1),
        prior_anomaly_count=0,
        tiene_periodos_conflictivos=False,
    )

    assert vector is not None
    assert vector.features["zero_consumption_streak"] == 3


def test_seasonality_index_neutral_when_no_prior_year_same_month() -> None:
    """F12: no period at all shares the current period's calendar month in an earlier year ->
    neutral 1.0 (DEC-007 cold-start default), even with plenty of history otherwise."""
    suministro_id = uuid4()
    lote_id = uuid4()
    filas = [
        _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 1), kwh=100.0),
        _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 2), kwh=110.0),
        _fila(suministro_id=suministro_id, lote_id=lote_id, fecha_inicio=_mes(2024, 3), kwh=120.0),
    ]

    vector = construir_feature_vector(
        suministro_id=suministro_id,
        lote_id=lote_id,
        historial_suministro=filas,
        categoria_tarifaria_id=uuid4(),
        localidad=None,
        fecha_alta=date(2023, 1, 1),
        prior_anomaly_count=0,
        tiene_periodos_conflictivos=False,
    )

    assert vector is not None
    assert vector.features["seasonality_index"] == 1.0


def test_seasonality_index_null_when_prior_year_mean_is_zero() -> None:
    """FIX 2 (reviewer finding, WARNING): a prior-year same-month MEAN of exactly `0` is a REAL
    division-by-zero -- there WAS prior-year data, it just billed nothing that month -- distinct
    from "no prior-year data at all" (the neutral `1.0` case above). Reporting `1.0` here would
    mask a `0 -> 5000` jump as "perfectly seasonal"; the guard must null it out instead, matching
    every other div-zero guard in this module (F5, F9, F13)."""
    suministro_id = uuid4()
    lote_id = uuid4()
    filas = [
        _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2023, 6), kwh=0.0),
        _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 5), kwh=100.0),
        _fila(suministro_id=suministro_id, lote_id=lote_id, fecha_inicio=_mes(2024, 6), kwh=5000.0),
    ]

    vector = construir_feature_vector(
        suministro_id=suministro_id,
        lote_id=lote_id,
        historial_suministro=filas,
        categoria_tarifaria_id=uuid4(),
        localidad=None,
        fecha_alta=date(2023, 1, 1),
        prior_anomaly_count=0,
        tiene_periodos_conflictivos=False,
    )

    assert vector is not None
    assert vector.features["seasonality_index"] is None


def test_seasonality_index_normal_when_prior_year_mean_is_nonzero() -> None:
    """FIX 2's normal path, unaffected: a nonzero prior-year same-month mean still divides
    normally -- (kwh_t / media_mes), same formula as before this fix."""
    suministro_id = uuid4()
    lote_id = uuid4()
    filas = [
        _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2023, 6), kwh=50.0),
        _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 5), kwh=100.0),
        _fila(suministro_id=suministro_id, lote_id=lote_id, fecha_inicio=_mes(2024, 6), kwh=150.0),
    ]

    vector = construir_feature_vector(
        suministro_id=suministro_id,
        lote_id=lote_id,
        historial_suministro=filas,
        categoria_tarifaria_id=uuid4(),
        localidad=None,
        fecha_alta=date(2023, 1, 1),
        prior_anomaly_count=0,
        tiene_periodos_conflictivos=False,
    )

    assert vector is not None
    assert vector.features["seasonality_index"] == 150.0 / 50.0


def test_deviation_from_baseline_null_when_stddev_zero() -> None:
    """F9's guard: a perfectly flat 12-month window has `stddev_consumption == 0` -- the
    deviation ratio is mathematically undefined (0/0 in practice, since a flat window's current
    value always equals its own mean), so it is null rather than raising or reporting a false
    zero."""
    suministro_id = uuid4()
    lote_id = uuid4()
    filas = [
        _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, m), kwh=100.0)
        for m in range(1, 4)
    ]
    filas[-1] = _fila(
        suministro_id=suministro_id, lote_id=lote_id, fecha_inicio=_mes(2024, 3), kwh=100.0
    )

    vector = construir_feature_vector(
        suministro_id=suministro_id,
        lote_id=lote_id,
        historial_suministro=filas,
        categoria_tarifaria_id=uuid4(),
        localidad=None,
        fecha_alta=date(2023, 1, 1),
        prior_anomaly_count=0,
        tiene_periodos_conflictivos=False,
    )

    assert vector is not None
    assert vector.features["stddev_consumption"] == 0.0
    assert vector.features["deviation_from_baseline"] is None


def test_trend_slope_null_below_minimum_periods() -> None:
    """F11: exactly 3 periods (< MINIMO_PERIODOS_TENDENCIA=4) -> null, even though F1-F4 already
    have enough history (>=3) to compute (DEC-007: different minimums per feature)."""
    suministro_id = uuid4()
    lote_id = uuid4()
    filas = [
        _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 1), kwh=100.0),
        _fila(suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=_mes(2024, 2), kwh=110.0),
        _fila(suministro_id=suministro_id, lote_id=lote_id, fecha_inicio=_mes(2024, 3), kwh=120.0),
    ]

    vector = construir_feature_vector(
        suministro_id=suministro_id,
        lote_id=lote_id,
        historial_suministro=filas,
        categoria_tarifaria_id=uuid4(),
        localidad=None,
        fecha_alta=date(2023, 1, 1),
        prior_anomaly_count=0,
        tiene_periodos_conflictivos=False,
    )

    assert vector is not None
    assert vector.features["trend_slope"] is None


def test_trend_slope_uses_calendar_month_offsets_not_list_index() -> None:
    """FIX 4 (reviewer finding, WARNING): F11 must regress `kwh` against calendar MONTHS elapsed
    since the window's FIRST period, not the periods' plain list position -- a period arriving
    many calendar months after its predecessor (a gap in the readings, e.g. unread/unbilled for a
    while) must weigh proportionally in the regression, not be treated as if it were merely "the
    next" contiguous month.

    Fixture: kwh = [100, 100, 100, 400], the first 3 periods 1 calendar month apart (Jan/Feb/Mar
    2024), the LAST period 10 calendar months after the 3rd (Mar 2024 -> Jan 2025) -- a calendar
    GAP the old list-index x-axis (0, 1, 2, 3) is blind to.

    Old (list-index) slope, hand-computed for contrast: x=[0,1,2,3], mean_x=1.5, mean_y=175 ->
    slope = 450/5 = 90.0 exactly.
    New (calendar month offsets) x=[0,1,2,12] (months elapsed since 2024-01-01), mean_x=3.75:
      numerador = sum((x-3.75)*(y-175)) = 281.25 + 206.25 + 131.25 + 1856.25 = 2475.0
      denominador = sum((x-3.75)**2) = 14.0625 + 7.5625 + 3.0625 + 68.0625 = 92.75
      slope = 2475.0 / 92.75 ≈ 26.68 -- materially lower than the old 90.0, reflecting the real
      10-month gap instead of masking it as a normal contiguous 4th point.
    """
    suministro_id = uuid4()
    lote_id = uuid4()
    filas = [
        _fila(
            suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=date(2024, 1, 1), kwh=100.0
        ),
        _fila(
            suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=date(2024, 2, 1), kwh=100.0
        ),
        _fila(
            suministro_id=suministro_id, lote_id=uuid4(), fecha_inicio=date(2024, 3, 1), kwh=100.0
        ),
        _fila(
            suministro_id=suministro_id, lote_id=lote_id, fecha_inicio=date(2025, 1, 1), kwh=400.0
        ),
    ]

    vector = construir_feature_vector(
        suministro_id=suministro_id,
        lote_id=lote_id,
        historial_suministro=filas,
        categoria_tarifaria_id=uuid4(),
        localidad=None,
        fecha_alta=date(2020, 1, 1),
        prior_anomaly_count=0,
        tiene_periodos_conflictivos=False,
    )

    assert vector is not None
    slope_esperado = 2475.0 / 92.75
    assert vector.features["trend_slope"] == slope_esperado
    assert slope_esperado < 90.0  # strictly lower than the old list-index slope (90.0)


def test_supply_age_days_null_when_fecha_alta_is_after_fecha_inicio() -> None:
    """FIX 5(a) (reviewer finding, small guard): `fecha_alta` AFTER the period being scored is a
    data inconsistency -- a supply cannot be billed before it was registered -- `null`, never a
    negative day count."""
    suministro_id = uuid4()
    lote_id = uuid4()
    filas = [
        _fila(suministro_id=suministro_id, lote_id=lote_id, fecha_inicio=_mes(2024, 1), kwh=100.0)
    ]

    vector = construir_feature_vector(
        suministro_id=suministro_id,
        lote_id=lote_id,
        historial_suministro=filas,
        categoria_tarifaria_id=uuid4(),
        localidad=None,
        fecha_alta=date(2024, 6, 1),  # AFTER the period's fecha_inicio (2024-01-01)
        prior_anomaly_count=0,
        tiene_periodos_conflictivos=False,
    )

    assert vector is not None
    assert vector.features["supply_age_days"] is None


# ---------------------------------------------------------------------------------------------
# Etapa 4b -- cohort indicators (percentile_peer, iqr_outlier_flag) + F13 peer_ratio (DEC-008).
# ---------------------------------------------------------------------------------------------


def _vector_con_kwh(*, categoria_id, localidad, kwh: float) -> FeatureVector:
    """A minimal `FeatureVector` for cohort-enrichment tests -- `features` only needs the keys
    `enriquecer_con_indicadores_cohorte` actually writes to, but every real key is present so a
    test asserting the WHOLE dict (none here do) would still see a realistic shape."""
    return FeatureVector(
        suministro_id=uuid4(),
        lote_id=uuid4(),
        features={"peer_ratio": None, "percentile_peer": None, "iqr_outlier_flag": None},
        is_cold_start=False,
        has_conflicted_periods=False,
        categoria_tarifaria_id=categoria_id,
        localidad=localidad,
        kwh_actual=kwh,
    )


def test_cohort_at_or_above_minimum_uses_categoria_x_localidad_directly() -> None:
    """12 suministros, same categoria x localidad (>= COHORTE_MINIMA=10) -> no fallback needed.
    Sorted kwh: 100,105,110,115,120,125,130,135,140,145,150,1000 (n=12).
    Q1 index = 0.25*11=2.75 -> between idx2(110) and idx3(115): 110 + 0.75*5 = 113.75
    Q3 index = 0.75*11=8.25 -> between idx8(140) and idx9(145): 140 + 0.25*5 = 141.25
    IQR = 27.5 -> lower=113.75-41.25=72.5, upper=141.25+41.25=182.5
    -> 1000 is an outlier (> 182.5); 100 is not (>= 72.5).
    percentile_peer(1000) = 12/12 = 1.0; percentile_peer(100) = 1/12.
    """
    assert COHORTE_MINIMA == 10  # this test's cohort size is deliberately >= the constant
    categoria_id = uuid4()
    kwh_values = [100, 105, 110, 115, 120, 125, 130, 135, 140, 145, 150, 1000]
    vectores = [
        _vector_con_kwh(categoria_id=categoria_id, localidad="Formosa", kwh=v) for v in kwh_values
    ]

    enriquecidos = enriquecer_con_indicadores_cohorte(vectores)

    por_kwh = {v.kwh_actual: v for v in enriquecidos}
    assert por_kwh[1000.0].features["iqr_outlier_flag"] is True
    assert por_kwh[1000.0].features["percentile_peer"] == 1.0
    assert por_kwh[100.0].features["iqr_outlier_flag"] is False
    assert por_kwh[100.0].features["percentile_peer"] == 1 / 12


def test_small_cohort_falls_back_to_categoria_alone() -> None:
    """5 suministros, same categoria, DIFFERENT localidades (so each categoria x localidad cell
    has < COHORTE_MINIMA=10 members) -> falls back to the categoria-alone pool, which has all 5
    (>= COHORTE_FALLBACK_MINIMA=3). Sorted kwh: 10,20,30,40,50 (n=5).
    Q1 index=0.25*4=1.0 -> exactly idx1=20; Q3 index=0.75*4=3.0 -> exactly idx3=40.
    IQR=20 -> lower=20-30=-10, upper=40+30=70 -> none are outliers.
    percentile_peer(30)=3/5=0.6. peer_ratio(30)=30/median(30)=1.0; peer_ratio(50)=50/30.
    """
    assert COHORTE_FALLBACK_MINIMA == 3
    categoria_id = uuid4()
    localidades = ["Formosa", "Clorinda", "Pirané", "El Colorado", "Formosa"]
    kwh_values = [10, 20, 30, 40, 50]
    vectores = [
        _vector_con_kwh(categoria_id=categoria_id, localidad=loc, kwh=v)
        for loc, v in zip(localidades, kwh_values, strict=True)
    ]

    enriquecidos = enriquecer_con_indicadores_cohorte(vectores)

    por_kwh = {v.kwh_actual: v for v in enriquecidos}
    assert por_kwh[30.0].features["percentile_peer"] == 0.6
    assert por_kwh[30.0].features["iqr_outlier_flag"] is False
    assert por_kwh[30.0].features["peer_ratio"] == 1.0
    assert por_kwh[50.0].features["peer_ratio"] == 50 / 30


def test_cohort_still_too_small_after_fallback_nulls_indicators_but_not_peer_ratio() -> None:
    """Only 2 suministros share a categoria (< COHORTE_FALLBACK_MINIMA=3, even after falling
    back from an even smaller categoria x localidad cell) -> percentile_peer/iqr_outlier_flag
    are null (DEC-008: "si todavía < 3, indicadores null"), but F13 peer_ratio is NOT part of
    that null policy (module docstring): it still resolves against the categoria-alone pool of
    2, since a suministro always has at least itself in that pool. Median of [10, 20] = 15
    (linear interpolation) -> peer_ratio(20) = 20/15."""
    categoria_id = uuid4()
    vectores = [
        _vector_con_kwh(categoria_id=categoria_id, localidad="Formosa", kwh=10),
        _vector_con_kwh(categoria_id=categoria_id, localidad="Clorinda", kwh=20),
    ]

    enriquecidos = enriquecer_con_indicadores_cohorte(vectores)

    por_kwh = {v.kwh_actual: v for v in enriquecidos}
    assert por_kwh[20.0].features["percentile_peer"] is None
    assert por_kwh[20.0].features["iqr_outlier_flag"] is None
    assert por_kwh[20.0].features["peer_ratio"] == 20 / 15


def test_peer_ratio_null_when_cohort_median_is_zero() -> None:
    """F13's own division guard: a cohort whose median resolves to 0 kwh makes the ratio
    undefined -- null, not a ZeroDivisionError or an infinite value."""
    categoria_id = uuid4()
    vectores = [
        _vector_con_kwh(categoria_id=categoria_id, localidad="Formosa", kwh=0),
        _vector_con_kwh(categoria_id=categoria_id, localidad="Formosa", kwh=0),
        _vector_con_kwh(categoria_id=categoria_id, localidad="Formosa", kwh=0),
    ]

    enriquecidos = enriquecer_con_indicadores_cohorte(vectores)

    assert all(v.features["peer_ratio"] is None for v in enriquecidos)


def test_iqr_outlier_flag_uses_leave_one_out_bounds_not_masked_by_self() -> None:
    """FIX 3 (reviewer finding, WARNING): a 3-member fallback cohort `[10, 20, 10000]` -- the
    reviewer's exact scenario. Under the OLD, inclusive bounds, `10000` drags its OWN Q3 (and
    therefore the upper fence) up with it and is never flagged, while `percentile_peer` for that
    SAME vector correctly reports `1.0` -- an internal contradiction. Leave-one-out bounds (Q1/Q3
    computed from the cohort MINUS the evaluated suministro) resolve it:
      - kwh=10000: LOO cohort = [10, 20] -> Q1=12.5, Q3=17.5, IQR=5, upper=17.5+7.5=25 ->
        10000 > 25 -> outlier True (was False under the old inclusive bounds).
      - kwh=10 / kwh=20: LOO cohort is `[20, 10000]` / `[10, 10000]` respectively -- still
        comfortably inside their own (10000-inflated) bounds -> False, unaffected.
    `percentile_peer` stays inclusive and unaffected either way (1.0 / (1/3) / (2/3))."""
    categoria_id = uuid4()
    vectores = [
        _vector_con_kwh(categoria_id=categoria_id, localidad="Formosa", kwh=10),
        _vector_con_kwh(categoria_id=categoria_id, localidad="Clorinda", kwh=20),
        _vector_con_kwh(categoria_id=categoria_id, localidad="Pirané", kwh=10000),
    ]

    enriquecidos = enriquecer_con_indicadores_cohorte(vectores)

    por_kwh = {v.kwh_actual: v for v in enriquecidos}
    assert por_kwh[10000.0].features["iqr_outlier_flag"] is True
    assert por_kwh[10000.0].features["percentile_peer"] == 1.0
    assert por_kwh[10.0].features["iqr_outlier_flag"] is False
    assert por_kwh[20.0].features["iqr_outlier_flag"] is False


def test_iqr_outlier_flag_none_when_loo_cohort_below_minimum() -> None:
    """Boundary case (FIX 3): a cohort of only 2 members total is already below
    `COHORTE_FALLBACK_MINIMA=3` -- `percentile_peer`/`iqr_outlier_flag` both stay `null` via the
    PRE-EXISTING small-cohort gate (DEC-008), unaffected by the new leave-one-out bounds: removing
    the evaluated suministro from a 2-member cohort would leave only 1 peer, nowhere near enough
    to define a meaningful IQR spread either way -- this test pins down that the fix does not
    accidentally loosen that floor."""
    categoria_id = uuid4()
    vectores = [
        _vector_con_kwh(categoria_id=categoria_id, localidad="Formosa", kwh=10),
        _vector_con_kwh(categoria_id=categoria_id, localidad="Clorinda", kwh=20),
    ]

    enriquecidos = enriquecer_con_indicadores_cohorte(vectores)

    por_kwh = {v.kwh_actual: v for v in enriquecidos}
    assert por_kwh[10.0].features["iqr_outlier_flag"] is None
    assert por_kwh[20.0].features["iqr_outlier_flag"] is None


def test_iqr_outlier_flag_large_cohort_behavior_unchanged() -> None:
    """FIX 3 must not change the flag for the existing large-cohort case
    (`test_cohort_at_or_above_minimum_uses_categoria_x_localidad_directly`): with n=12, one
    excluded self barely moves Q1/Q3 -- 1000 stays an outlier, 100 stays not, exactly as before
    the leave-one-out change (hand-verified: LOO Q1/Q3 for either member shift by only ~2-5 kWh,
    nowhere near enough to flip either flag)."""
    categoria_id = uuid4()
    kwh_values = [100, 105, 110, 115, 120, 125, 130, 135, 140, 145, 150, 1000]
    vectores = [
        _vector_con_kwh(categoria_id=categoria_id, localidad="Formosa", kwh=v) for v in kwh_values
    ]

    enriquecidos = enriquecer_con_indicadores_cohorte(vectores)

    por_kwh = {v.kwh_actual: v for v in enriquecidos}
    assert por_kwh[1000.0].features["iqr_outlier_flag"] is True
    assert por_kwh[100.0].features["iqr_outlier_flag"] is False


# ---------------------------------------------------------------------------------------------
# resumir_features (informe response summary, mission directive #7).
# ---------------------------------------------------------------------------------------------


def test_resumir_features_counts_match_hand_picked_vectors() -> None:
    assert UMBRAL_ZSCORE_EXTREMO == 3
    assert UMBRAL_PERCENTILE_EXTREMO == 0.99

    def _vector(*, cold_start, conflictivo, zscore, iqr_flag, percentile) -> FeatureVector:
        return FeatureVector(
            suministro_id=uuid4(),
            lote_id=uuid4(),
            features={
                "zscore_self": zscore,
                "iqr_outlier_flag": iqr_flag,
                "percentile_peer": percentile,
            },
            is_cold_start=cold_start,
            has_conflicted_periods=conflictivo,
            categoria_tarifaria_id=uuid4(),
            localidad=None,
            kwh_actual=0.0,
        )

    vectores = [
        _vector(cold_start=True, conflictivo=False, zscore=None, iqr_flag=False, percentile=None),
        _vector(cold_start=False, conflictivo=True, zscore=3.5, iqr_flag=True, percentile=0.99),
        _vector(cold_start=False, conflictivo=True, zscore=-4.0, iqr_flag=False, percentile=0.5),
        _vector(cold_start=False, conflictivo=False, zscore=1.0, iqr_flag=False, percentile=0.2),
    ]

    resumen = resumir_features(vectores)

    assert resumen.suministros_con_vector == 4
    assert resumen.cold_starts == 1
    assert resumen.con_periodos_conflictivos == 2
    assert resumen.zscore_extremos == 2  # 3.5 and -4.0, |z| >= 3
    assert resumen.iqr_outliers == 1
    assert resumen.percentile_extremos == 1  # only the exact 0.99
