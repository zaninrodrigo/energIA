"""Per-categoria consumption profiles: baseline, seasonal curve, growth trend, noise.

EnergIA operates in Argentina (Southern Hemisphere): summer -- the air-conditioning load peak
-- runs December through February, not June-August. `seasonal_factor()` must peak around
January, the opposite of what a Northern Hemisphere assumption would produce.

Every random draw in this module takes an explicit `random.Random` instance (never a bare
`random.random()` call) so a caller threading a single seeded RNG through the whole generator
gets fully reproducible output -- see `generator.py`'s module docstring.
"""

import math
from dataclasses import dataclass
from enum import StrEnum
from random import Random

# Categoria names as seeded by docker/postgres/init/04_seed.sql (DOMAIN_MODEL.md sec 7.3): the
# `suministros.categoria_tarifaria` import payload field resolves by this exact `nombre`. Only
# the three profiled here are used by the generator (Residencial/Comercial/Industrial), out of
# the five seeded categorias -- "Grandes Demandas"/"Alumbrado Publico" are out of scope for v1.


class Categoria(StrEnum):
    RESIDENCIAL = "Residencial"
    COMERCIAL = "Comercial"
    INDUSTRIAL = "Industrial"


@dataclass(frozen=True, slots=True)
class CategoriaProfile:
    """One categoria's monthly consumption model.

    `baseline_kwh`: median monthly consumption before seasonality/trend/noise are applied.
    `seasonal_amplitude`: relative swing of the Dec-Feb/Jun-Aug seasonal curve (0.35 means
    +/-35% around the baseline at the peak/trough).
    `noise_sigma`: standard deviation of the underlying normal in the lognormal noise (see
    `lognormal_noise()`) -- larger for categorias with more month-to-month volatility.
    `annual_growth_rate`: fractional growth applied per elapsed year (a mild multi-year trend,
    e.g. 0.03 = +3%/year), never compounded -- see `growth_factor()`.
    """

    categoria: Categoria
    baseline_kwh: float
    seasonal_amplitude: float
    noise_sigma: float
    annual_growth_rate: float


# Rough real-world magnitude ordering (Residencial << Comercial << Industrial), not calibrated
# against real billing data (none exists yet for this project, see PROJECT_MASTER_SPEC.md #8) --
# these are illustrative synthetic figures, good enough to exercise the AI engine's features.
PROFILES: dict[Categoria, CategoriaProfile] = {
    Categoria.RESIDENCIAL: CategoriaProfile(
        categoria=Categoria.RESIDENCIAL,
        baseline_kwh=280.0,
        seasonal_amplitude=0.35,
        noise_sigma=0.12,
        annual_growth_rate=0.02,
    ),
    Categoria.COMERCIAL: CategoriaProfile(
        categoria=Categoria.COMERCIAL,
        baseline_kwh=1_400.0,
        seasonal_amplitude=0.20,
        noise_sigma=0.10,
        annual_growth_rate=0.03,
    ),
    Categoria.INDUSTRIAL: CategoriaProfile(
        categoria=Categoria.INDUSTRIAL,
        baseline_kwh=9_000.0,
        seasonal_amplitude=0.08,
        noise_sigma=0.06,
        annual_growth_rate=0.04,
    ),
}


def seasonal_factor(month: int, amplitude: float) -> float:
    """Southern Hemisphere seasonal multiplier for a given calendar `month` (1-12).

    A cosine curve centered on January (`month=1` gives the maximum, `cos(0) = 1`): December
    and February land symmetrically close to that peak, July (`month=7`) is the trough
    (`cos(pi) = -1`) -- matching Argentina's actual summer/winter months, the reverse of a
    Northern Hemisphere calendar. Averages to 1.0 over a full 12-month cycle, so it reshapes
    the annual distribution without shifting the yearly total.
    """
    angle = 2 * math.pi * (month - 1) / 12
    return 1.0 + amplitude * math.cos(angle)


def growth_factor(elapsed_months: int, annual_growth_rate: float) -> float:
    """Mild multi-year growth trend: linear in elapsed time (never compounded), `1.0` at
    `elapsed_months=0`."""
    return 1.0 + annual_growth_rate * (elapsed_months / 12)


def lognormal_noise(rng: Random, sigma: float) -> float:
    """Multiplicative noise: mean 1.0, never negative, right-skewed -- consumption noise looks
    like this in practice (a few unusually high months, never a negative one).

    `exp(N(0, sigma) - sigma**2/2)` is the standard mean-1 lognormal construction: the
    `-sigma**2/2` term is is exactly what corrects the mean of `exp(N(0, sigma))` (which is
    `exp(sigma**2/2)`, not 1) back down to 1.0.
    """
    return math.exp(rng.gauss(0.0, sigma) - (sigma**2) / 2)


def monthly_baseline_kwh(
    rng: Random,
    profile: CategoriaProfile,
    *,
    calendar_month: int,
    elapsed_months: int,
) -> float:
    """One suministro-month's undisturbed (non-anomalous) consumption: baseline x seasonality x
    growth trend x noise. Always strictly positive -- every factor is strictly positive."""
    factor = (
        seasonal_factor(calendar_month, profile.seasonal_amplitude)
        * growth_factor(elapsed_months, profile.annual_growth_rate)
        * lognormal_noise(rng, profile.noise_sigma)
    )
    return profile.baseline_kwh * factor
