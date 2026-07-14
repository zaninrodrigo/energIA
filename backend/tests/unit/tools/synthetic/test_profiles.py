"""Unit tests for energia.tools.synthetic.profiles.

Covers the Southern Hemisphere (Argentina) seasonal curve, the multi-year growth trend, the
never-negative right-skewed noise, and the per-categoria baseline ordering.
"""

import math
from random import Random

from energia.tools.synthetic.profiles import (
    PROFILES,
    Categoria,
    growth_factor,
    lognormal_noise,
    monthly_baseline_kwh,
    seasonal_factor,
)


def test_seasonal_factor_peaks_in_january_not_july() -> None:
    """Argentina is in the Southern Hemisphere: summer (AC load peak) is Dec-Feb, so the
    seasonal curve must peak around January, not July (which is deep winter here)."""
    january = seasonal_factor(1, amplitude=0.3)
    july = seasonal_factor(7, amplitude=0.3)

    assert january > july


def test_seasonal_factor_is_roughly_symmetric_around_january() -> None:
    """December and February should be nearly as hot (nearly as high a factor) as January
    itself -- summer spans Dec-Feb, not just one calendar month."""
    december = seasonal_factor(12, amplitude=0.3)
    february = seasonal_factor(2, amplitude=0.3)

    assert math.isclose(december, february, rel_tol=1e-9)


def test_seasonal_factor_averages_to_one_over_a_full_year() -> None:
    """A multiplicative seasonal curve must not change the annual baseline on average -- the
    12 monthly factors average out to 1.0 (a plain cosine over a full period integrates to 0)."""
    factors = [seasonal_factor(month, amplitude=0.4) for month in range(1, 13)]

    assert math.isclose(sum(factors) / 12, 1.0, abs_tol=1e-9)


def test_growth_factor_increases_with_elapsed_months() -> None:
    year_zero = growth_factor(0, annual_growth_rate=0.05)
    year_two = growth_factor(24, annual_growth_rate=0.05)

    assert year_zero == 1.0
    assert year_two > year_zero


def test_lognormal_noise_is_never_negative_across_many_draws() -> None:
    rng = Random(42)

    draws = [lognormal_noise(rng, sigma=0.5) for _ in range(1000)]

    assert all(draw > 0 for draw in draws)


def test_lognormal_noise_is_deterministic_given_a_seeded_rng() -> None:
    """Same seed, same stream position -> identical draws; the whole generator threads a
    single seeded `random.Random`, never bare `random.random()`."""
    first_rng = Random(7)
    second_rng = Random(7)

    first_draws = [lognormal_noise(first_rng, sigma=0.3) for _ in range(5)]
    second_draws = [lognormal_noise(second_rng, sigma=0.3) for _ in range(5)]

    assert first_draws == second_draws


def test_categoria_baselines_are_ordered_residencial_comercial_industrial() -> None:
    """Rough real-world magnitude ordering: a household consumes far less than a shop, which
    consumes far less than an industrial plant."""
    assert (
        PROFILES[Categoria.RESIDENCIAL].baseline_kwh
        < PROFILES[Categoria.COMERCIAL].baseline_kwh
        < PROFILES[Categoria.INDUSTRIAL].baseline_kwh
    )


def test_monthly_baseline_kwh_is_never_negative() -> None:
    rng = Random(123)

    for month in range(1, 13):
        value = monthly_baseline_kwh(
            rng, PROFILES[Categoria.RESIDENCIAL], calendar_month=month, elapsed_months=0
        )
        assert value > 0
