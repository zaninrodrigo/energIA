"""Unit tests for energia.tools.synthetic.anomalies.

Each injector is a pure function over a suministro's monthly kwh series: it must never touch a
bare `random.random()` (only the seeded `random.Random` it is given), never produce a negative
kwh, and return an `AnomalyPlan` describing exactly what it did, for the ground-truth manifest.
"""

from random import Random

import pytest

from energia.tools.synthetic.anomalies import (
    AnomalyType,
    inject_gradual_decline,
    inject_spike,
    inject_spike_leve,
    inject_sudden_drop,
    inject_sudden_drop_leve,
    inject_zero_consumption_streak,
)

_BASELINE_SERIES = [300.0] * 24

# Anchored injectors (sudden_drop/spike and their `_leve` siblings) all take the same
# `(series, start_index, rng)` signature and share the same no-op contract on too-short series --
# parametrized together below instead of duplicating the same test 4 times.
_ANCHORED_INJECTORS = (
    inject_sudden_drop,
    inject_sudden_drop_leve,
    inject_spike,
    inject_spike_leve,
)


def test_anomaly_type_merges_zero_streak_and_meter_stall_into_one_member() -> None:
    """See this module's docstring: with kwh always constructed as the lectura delta
    (generator.py), a zero-kwh month and a frozen meter reading are the SAME data pattern in
    this schema -- there is no separate `meter_stall` member pretending otherwise."""
    names = {member.value for member in AnomalyType}

    assert "zero_consumption_streak" in names
    assert "meter_stall" not in names
    assert "zero_streak" not in names
    assert "sudden_drop_leve" in names
    assert "spike_leve" in names
    assert len(AnomalyType) == 6


def test_sudden_drop_falls_60_to_80_percent_and_stays_down_permanently() -> None:
    rng = Random(1)

    new_series, plan = inject_sudden_drop(_BASELINE_SERIES, start_index=10, rng=rng)

    assert plan.tipo is AnomalyType.SUDDEN_DROP
    assert plan.fraud_shaped is True
    assert new_series[:10] == _BASELINE_SERIES[:10]  # untouched before the injection point
    for month_value in new_series[10:]:  # permanent: every month after, including the last
        ratio = month_value / _BASELINE_SERIES[0]
        assert 0.20 <= ratio <= 0.40  # a 60-80% drop leaves 20-40% of the baseline


def test_sudden_drop_is_deterministic_given_a_seeded_rng() -> None:
    first, _ = inject_sudden_drop(_BASELINE_SERIES, start_index=5, rng=Random(99))
    second, _ = inject_sudden_drop(_BASELINE_SERIES, start_index=5, rng=Random(99))

    assert first == second


@pytest.mark.parametrize("seed", range(30))
def test_sudden_drop_realized_pct_change_is_anchored_to_the_prior_realized_month(
    seed: int,
) -> None:
    """FIX 1(a): a naive `series[start_index] * (1 - drop_fraction)` (scaling the anomalous
    month's OWN noisy baseline) lets a baseline jump AT the injection point push the REALIZED
    month-over-month pct_change outside the drawn `drop_fraction` -- this crafted series (the
    baseline itself triples right at the injection point) reproduces exactly that divergence;
    the anchored fix must keep the realized ratio inside [-0.80, -0.60] regardless."""
    series = [300.0] * 6 + [900.0] + [300.0] * 17  # baseline itself jumps at index 6
    rng = Random(seed)

    new_series, plan = inject_sudden_drop(series, start_index=6, rng=rng)

    assert plan is not None
    realized_pct_change = new_series[6] / series[5] - 1
    assert -0.80 - 1e-9 <= realized_pct_change <= -0.60 + 1e-9


@pytest.mark.parametrize("seed", range(30))
def test_spike_realized_pct_change_is_anchored_to_the_prior_realized_month(seed: int) -> None:
    """FIX 1(a): same divergence as sudden_drop's, mirrored for spike -- a baseline DROP right at
    the injection point must not push the realized ratio below R3's +200% (2.0) floor."""
    series = [300.0] * 8 + [90.0] + [300.0] * 15  # baseline itself drops at index 8
    rng = Random(seed)

    new_series, plan = inject_spike(series, start_index=8, rng=rng)

    assert plan is not None
    realized_pct_change = new_series[8] / series[7] - 1
    assert 2.0 - 1e-9 <= realized_pct_change <= 4.0 + 1e-9


@pytest.mark.parametrize("seed", range(30))
def test_sudden_drop_leve_stays_below_r2s_60_percent_threshold(seed: int) -> None:
    """FIX 1(b): `sudden_drop_leve` is deliberately sub-threshold -- only the ML/statistics
    branches could plausibly catch it, never R2 (which needs <= -60%)."""
    rng = Random(seed)

    new_series, plan = inject_sudden_drop_leve(_BASELINE_SERIES, start_index=10, rng=rng)

    assert plan is not None
    assert plan.tipo is AnomalyType.SUDDEN_DROP_LEVE
    realized_pct_change = new_series[10] / _BASELINE_SERIES[9] - 1
    assert -0.50 - 1e-9 <= realized_pct_change <= -0.30 + 1e-9
    assert realized_pct_change > -0.60  # below R2's own trigger, on purpose


@pytest.mark.parametrize("seed", range(30))
def test_spike_leve_stays_below_r3s_200_percent_threshold(seed: int) -> None:
    """FIX 1(b): `spike_leve` is deliberately sub-threshold -- only the ML/statistics branches
    could plausibly catch it, never R3 (which needs >= +200%)."""
    rng = Random(seed)

    new_series, plan = inject_spike_leve(_BASELINE_SERIES, start_index=8, rng=rng)

    assert plan is not None
    assert plan.tipo is AnomalyType.SPIKE_LEVE
    realized_pct_change = new_series[8] / _BASELINE_SERIES[7] - 1
    assert 0.8 - 1e-9 <= realized_pct_change <= 1.6 + 1e-9
    assert realized_pct_change < 2.0  # below R3's own trigger, on purpose


@pytest.mark.parametrize("injector", _ANCHORED_INJECTORS)
def test_anchored_injectors_no_op_on_an_empty_series(injector: object) -> None:
    """FIX 3: reproduces the original IndexError (`generate_dataset(3, "small", months=1)`
    could hand an injector a 0-length series) -- must no-op instead of crashing."""
    new_series, plan = injector([], start_index=0, rng=Random(1))  # type: ignore[operator]

    assert new_series == []
    assert plan is None


@pytest.mark.parametrize("injector", _ANCHORED_INJECTORS)
def test_anchored_injectors_no_op_when_there_is_no_prior_realized_month(injector: object) -> None:
    """FIX 3: a 1-month series has no REALIZED previous month to anchor to -- `start_index=0` is
    the only possible index, and anchored injectors need `start_index >= 1`."""
    new_series, plan = injector([300.0], start_index=0, rng=Random(1))  # type: ignore[operator]

    assert new_series == [300.0]
    assert plan is None


def test_zero_consumption_streak_no_ops_on_an_empty_series() -> None:
    """FIX 3: unlike the anchored injectors, this one has no "previous month" requirement, but
    must still no-op instead of indexing an empty series."""
    new_series, plan = inject_zero_consumption_streak([], start_index=0, rng=Random(1))

    assert new_series == []
    assert plan is None


def test_gradual_decline_no_ops_on_an_empty_series() -> None:
    """FIX 3: same as zero_consumption_streak's -- no "previous month" requirement, but still
    must no-op instead of indexing an empty series."""
    new_series, plan = inject_gradual_decline([], start_index=0, rng=Random(1))

    assert new_series == []
    assert plan is None


def test_zero_consumption_streak_produces_at_least_3_consecutive_zero_months() -> None:
    rng = Random(2)

    new_series, plan = inject_zero_consumption_streak(_BASELINE_SERIES, start_index=4, rng=rng)

    assert plan.tipo is AnomalyType.ZERO_CONSUMPTION_STREAK
    assert plan.fraud_shaped is True
    assert plan.length >= 3
    affected = new_series[4 : 4 + plan.length]
    assert affected == [0.0] * plan.length
    assert new_series[: 4 - 1 + 1] == _BASELINE_SERIES[:4]  # untouched before the streak


def test_zero_consumption_streak_recovers_to_normal_afterward() -> None:
    """A temporary streak (vacant property, seasonal shutdown), distinct from
    `sudden_drop`/`gradual_decline` which are permanent -- see the injector's own docstring."""
    rng = Random(3)

    new_series, plan = inject_zero_consumption_streak(_BASELINE_SERIES, start_index=2, rng=rng)

    tail_start = 2 + plan.length
    assert new_series[tail_start:] == _BASELINE_SERIES[tail_start:]


def test_zero_consumption_streak_clamps_to_the_series_length() -> None:
    """A streak requested near the end of the series must not index past it."""
    rng = Random(4)

    new_series, plan = inject_zero_consumption_streak(
        _BASELINE_SERIES, start_index=len(_BASELINE_SERIES) - 2, rng=rng
    )

    assert len(new_series) == len(_BASELINE_SERIES)
    assert plan.length <= 2


def test_gradual_decline_is_a_monotonic_5_percent_per_month_decline() -> None:
    rng = Random(5)

    new_series, plan = inject_gradual_decline(_BASELINE_SERIES, start_index=0, rng=rng)

    assert plan.tipo is AnomalyType.GRADUAL_DECLINE
    assert plan.fraud_shaped is True
    for earlier, later in zip(new_series[0:11], new_series[1:12], strict=True):
        assert later < earlier  # strictly decreasing month over month, for the decline window
    # after 12 months of -5%/month, ~0.95**12 =~ 54% of baseline remains
    assert new_series[11] / _BASELINE_SERIES[11] == pytest.approx(0.95**12, rel=1e-6)


def test_gradual_decline_holds_at_the_final_level_after_the_decline_window() -> None:
    rng = Random(6)

    new_series, plan = inject_gradual_decline(_BASELINE_SERIES, start_index=0, rng=rng)

    ratio_at_end_of_window = new_series[11] / _BASELINE_SERIES[11]
    ratio_after_window = new_series[15] / _BASELINE_SERIES[15]
    assert ratio_at_end_of_window == pytest.approx(ratio_after_window, rel=1e-9)


def test_spike_is_a_single_month_3x_to_5x_that_reverts_immediately() -> None:
    rng = Random(7)

    new_series, plan = inject_spike(_BASELINE_SERIES, start_index=8, rng=rng)

    assert plan.tipo is AnomalyType.SPIKE
    assert plan.fraud_shaped is False  # non-fraud: a one-off appliance/meter misreading
    assert plan.length == 1
    ratio = new_series[8] / _BASELINE_SERIES[8]
    assert 3.0 <= ratio < 5.0
    assert new_series[:8] == _BASELINE_SERIES[:8]
    assert new_series[9:] == _BASELINE_SERIES[9:]
