"""Anomaly injectors: each one perturbs a suministro's monthly kwh series and returns an
`AnomalyPlan` describing exactly what it did, so `manifest.py` can record it as ground truth.

Every injector is a pure function over the series it is given, using only the `random.Random`
instance passed in (never a bare `random.random()`), so the whole pipeline stays reproducible
when `generator.py` threads a single seeded RNG through it.

All anomalies here are BEHAVIORAL (consumption-pattern) perturbations, never data-quality
violations: a value is never negative, and `generator.py` always derives a suministro's
`lectura_actual` as a running sum of these very kwh values, so the domain's V1-V7 integrity
checks (docs/03-architecture/DOMAIN_MODEL.md, AI_ENGINE_SPEC.md sec 4.1) stay satisfied by
construction, regardless of which anomaly (if any) a suministro carries.

## Resolving the `zero_streak` vs `meter_stall` overlap

The original design brief asked for two candidate anomaly types: `zero_streak` ("3+ consecutive
months of genuinely zero kwh consumption, meter reading still advances consistent with zero
delta") and `meter_stall` ("lectura_actual == lectura_anterior repeatedly, producing kwh 0").

Both descriptions resolve to the exact same stored data in this schema: `generator.py` always
sets `lectura_actual = lectura_anterior + kwh` for every period of every suministro (that is
what keeps Etapa 1's V2 check, "kwh vs delta de lectura", satisfied for every anomaly type, not
just this one). There is no field anywhere in this data model that records *why* a meter's
delta was zero (no "estimated reading" flag, no separate raw-reading vs. billed-kwh column) --
`lecturas`/`consumos` (docker/postgres/init/01_schema.sql) only ever store the reading pair and
the billed kwh, never a provenance flag for either. A "real" zero-consumption month and a
"frozen/stuck" meter are therefore INDISTINGUISHABLE in the data this generator (or the real
import pipeline) can produce: both are, unavoidably, `kwh = 0` with `lectura_actual ==
lectura_anterior`.

Rather than fabricate a distinction the schema cannot represent, this module merges the two
into a single `AnomalyType.ZERO_CONSUMPTION_STREAK` -- named after AI_ENGINE_SPEC.md sec 6's
feature F10 (`zero_consumption_streak`) and sec 8's rule R1 ("`zero_consumption_streak >= 3` con
suministro activo" -> `Persistencia Anómala`), which is exactly the real-world signal this
anomaly is meant to exercise, whichever of the two physical causes actually produced it.

## Anchoring `sudden_drop`/`spike` to the REALIZED previous month

`sudden_drop` and `spike` exist to exercise R2/R3 (AI_ENGINE_SPEC.md sec 8), both defined in
terms of `pct_change_prev_period` -- the REALIZED month-over-month change the rule engine (or a
calibration script) would actually compute from stored `consumos.kwh`, i.e. `this_month /
previous_month - 1`. Scaling the anomalous month's OWN noisy baseline value (`series[start_index]
* multiplier`) does NOT control that realized ratio: `series[start_index]` and
`series[start_index - 1]` already carry independent seasonal/growth/noise draws, so the ratio
between the SCALED value and the unscaled previous month drifts away from the drawn parameter --
measured empirically, this pushed ~14-15% of planted drops/spikes outside their intended range.

Every anchored injector below instead computes the anomalous month directly from
`series[start_index - 1]` (the REALIZED previous month, still untouched at the point the
injector runs): `value = prev_realized * multiplier` (spike family) or `prev_realized * (1 -
drop_fraction)` (drop family). For `sudden_drop`/`sudden_drop_leve` (multi-month, permanent),
every month after `start_index` is rescaled by the SAME correction factor that anchoring
`start_index` required, so the drop's shape (its relative month-to-month movement, following
the underlying baseline's own seasonality/noise) is preserved exactly as before, just shifted to
the correct level -- see `_apply_anchored_drop_shape()`.

## Sub-threshold ("_leve") siblings

`sudden_drop_leve` and `spike_leve` use the exact same anchoring math as their rule-strength
siblings, only drawing their magnitude from a range deliberately BELOW R2's -60% / R3's +200%
trigger (30-50% drop, 1.8-2.6x multiplier). A dataset with ONLY rule-strength anomalies can't
tell you anything about the ML/statistics branches (Etapas 5-6, Isolation Forest + z-score/IQR
style indicators): every anomaly a rule already catches gives the ML branch no unique credit to
claim. These two sub-threshold types exist purely to isolate that contribution -- see
`generator.py`'s `DEFAULT_SUB_THRESHOLD_RATE`.
"""

from dataclasses import dataclass
from enum import StrEnum
from random import Random

# R2 (AI_ENGINE_SPEC.md sec 8): "pct_change_prev_period <= -60% sostenida >= 2 periodos".
_SUDDEN_DROP_MIN_FRACTION = 0.60
_SUDDEN_DROP_MAX_FRACTION = 0.80

# Sub-threshold sibling of SUDDEN_DROP: 30-50% drop, always below R2's -60% trigger on purpose
# (see this module's docstring, "Sub-threshold (`_leve`) siblings").
_SUDDEN_DROP_LEVE_MIN_FRACTION = 0.30
_SUDDEN_DROP_LEVE_MAX_FRACTION = 0.50

# R1: "zero_consumption_streak >= 3".
_ZERO_STREAK_MIN_LENGTH = 3
_ZERO_STREAK_MAX_LENGTH = 6

_GRADUAL_DECLINE_MONTHLY_RATE = 0.05
_GRADUAL_DECLINE_DURATION_MONTHS = 12

# R3: "pct_change_prev_period >= +200% en un periodo" -- +200% means 3x the previous value; the
# upper end (5x) is comfortably inside R3's territory too, without being an implausible spike.
_SPIKE_MIN_MULTIPLIER = 3.0
_SPIKE_MAX_MULTIPLIER = 5.0

# Sub-threshold sibling of SPIKE: 1.8-2.6x (pct_change +80% to +160%), always below R3's +200%
# trigger on purpose (see this module's docstring, "Sub-threshold (`_leve`) siblings").
_SPIKE_LEVE_MIN_MULTIPLIER = 1.8
_SPIKE_LEVE_MAX_MULTIPLIER = 2.6


class AnomalyType(StrEnum):
    """The 6 injectable anomaly shapes this generator plants -- 4 rule-strength shapes (see this
    module's docstring for why there are 4, not 5: the `zero_streak`/`meter_stall` merge) plus 2
    deliberately sub-threshold ("_leve") siblings of `sudden_drop`/`spike` (see this module's
    docstring, "Sub-threshold (`_leve`) siblings")."""

    SUDDEN_DROP = "sudden_drop"
    SUDDEN_DROP_LEVE = "sudden_drop_leve"
    ZERO_CONSUMPTION_STREAK = "zero_consumption_streak"
    GRADUAL_DECLINE = "gradual_decline"
    SPIKE = "spike"
    SPIKE_LEVE = "spike_leve"


# Anomaly types whose anomalous month is anchored to the REALIZED previous month (see this
# module's docstring, "Anchoring `sudden_drop`/`spike`..."): the only types for which a
# `pct_change_first_month` is meaningful/recorded (`generator.py` computes it from the actual
# persisted kwh values for exactly these types).
ANCHORED_ANOMALY_TYPES: frozenset[AnomalyType] = frozenset(
    {
        AnomalyType.SUDDEN_DROP,
        AnomalyType.SUDDEN_DROP_LEVE,
        AnomalyType.SPIKE,
        AnomalyType.SPIKE_LEVE,
    }
)


@dataclass(frozen=True, slots=True)
class AnomalyPlan:
    """What one injector actually did to one suministro's series -- the ground-truth record
    `manifest.py` serializes. `start_index`/`length` are 0-based offsets into that suministro's
    own monthly series (`generator.py` maps them to calendar periods when building payloads).
    """

    tipo: AnomalyType
    start_index: int
    length: int
    fraud_shaped: bool
    parametros: dict[str, float | int]


def _is_plantable(series_length: int, start_index: int, *, needs_previous_month: bool) -> bool:
    """Whether an injector can do anything at `start_index` in a series of `series_length`
    months without indexing out of bounds.

    `start_index` must land inside a non-empty series; anchored injectors (`sudden_drop`/`spike`
    and their `_leve` siblings, which anchor the anomalous month to the REALIZED previous month
    -- see this module's docstring) additionally require that a previous month actually exists
    (`start_index >= 1`).

    Every injector below checks this FIRST and no-ops (returns the series unchanged, `None`
    plan) when it is `False` -- this is what makes `generator.py`'s `_pick_start_index()` safe
    to call with any `series_length >= 0`, including the 0-length series a cold-start suministro
    gets when its `entry_month_offset` consumes the entire dataset window (e.g. a 1-month
    dataset, see `generator.py`'s own docstring on that function's contract).
    """
    if series_length < 1 or not (0 <= start_index < series_length):
        return False
    return not needs_previous_month or start_index >= 1


def _apply_anchored_drop_shape(
    series: list[float], start_index: int, drop_fraction: float
) -> list[float]:
    """Scale every month from `start_index` to the end of `series` by `(1 - drop_fraction)`,
    anchored so the REALIZED month-over-month change at `start_index` is exactly `-drop_fraction`
    relative to the REALIZED previous month (`series[start_index - 1]`) -- not relative to
    `start_index`'s own undisturbed baseline value, which would let post-noise drift push the
    realized ratio outside the intended range (see this module's docstring). Every subsequent
    month keeps the same shape RELATIVE to `start_index` (their ratio to `series[start_index]`
    is unchanged), just uniformly rescaled by the anchor correction -- assumes `series
    [start_index] > 0` (always true: `profiles.monthly_baseline_kwh()` is strictly positive).
    """
    anchor_correction = series[start_index - 1] / series[start_index]
    new_series = list(series)
    for index in range(start_index, len(series)):
        new_series[index] = series[index] * (1 - drop_fraction) * anchor_correction
    return new_series


def inject_sudden_drop(
    series: list[float], start_index: int, rng: Random
) -> tuple[list[float], AnomalyPlan | None]:
    """R2 ("Caída Brusca"): consumption falls 60-80% at `start_index`, ANCHORED to the REALIZED
    previous month (see this module's docstring), and never recovers -- every month from
    `start_index` to the end of the series stays at the reduced level. No-ops (see
    `_is_plantable()`) when the series is too short or has no previous month to anchor to."""
    if not _is_plantable(len(series), start_index, needs_previous_month=True):
        return list(series), None

    drop_fraction = rng.uniform(_SUDDEN_DROP_MIN_FRACTION, _SUDDEN_DROP_MAX_FRACTION)
    new_series = _apply_anchored_drop_shape(series, start_index, drop_fraction)

    plan = AnomalyPlan(
        tipo=AnomalyType.SUDDEN_DROP,
        start_index=start_index,
        length=len(series) - start_index,
        fraud_shaped=True,
        parametros={"drop_fraction": round(drop_fraction, 4)},
    )
    return new_series, plan


def inject_sudden_drop_leve(
    series: list[float], start_index: int, rng: Random
) -> tuple[list[float], AnomalyPlan | None]:
    """Sub-threshold sibling of `inject_sudden_drop` (see this module's docstring): a 30-50% drop,
    below R2's -60% trigger on purpose, same anchoring math otherwise. No-ops on a too-short
    series exactly like `inject_sudden_drop`."""
    if not _is_plantable(len(series), start_index, needs_previous_month=True):
        return list(series), None

    drop_fraction = rng.uniform(_SUDDEN_DROP_LEVE_MIN_FRACTION, _SUDDEN_DROP_LEVE_MAX_FRACTION)
    new_series = _apply_anchored_drop_shape(series, start_index, drop_fraction)

    plan = AnomalyPlan(
        tipo=AnomalyType.SUDDEN_DROP_LEVE,
        start_index=start_index,
        length=len(series) - start_index,
        fraud_shaped=True,
        parametros={"drop_fraction": round(drop_fraction, 4)},
    )
    return new_series, plan


def inject_zero_consumption_streak(
    series: list[float],
    start_index: int,
    rng: Random,
    *,
    min_length: int = _ZERO_STREAK_MIN_LENGTH,
    max_length: int = _ZERO_STREAK_MAX_LENGTH,
) -> tuple[list[float], AnomalyPlan | None]:
    """R1 ("Persistencia Anómala"): 3-6 consecutive months of exactly zero kwh, then a full
    recovery to the undisturbed baseline -- see this module's docstring for why this single
    type stands in for both a real zero-consumption spell and a frozen meter. Clamped to the
    series length if requested too close to the end; no-ops (see `_is_plantable()`) on an empty
    series.
    """
    if not _is_plantable(len(series), start_index, needs_previous_month=False):
        return list(series), None

    requested_length = rng.randint(min_length, max_length)
    end_index = min(start_index + requested_length, len(series))
    actual_length = end_index - start_index

    new_series = list(series)
    for index in range(start_index, end_index):
        new_series[index] = 0.0

    plan = AnomalyPlan(
        tipo=AnomalyType.ZERO_CONSUMPTION_STREAK,
        start_index=start_index,
        length=actual_length,
        fraud_shaped=True,
        parametros={},
    )
    return new_series, plan


def inject_gradual_decline(
    series: list[float],
    start_index: int,
    rng: Random,
    *,
    monthly_rate: float = _GRADUAL_DECLINE_MONTHLY_RATE,
    duration_months: int = _GRADUAL_DECLINE_DURATION_MONTHS,
) -> tuple[list[float], AnomalyPlan | None]:
    """A linear -5%/month decline sustained for 12 months (clamped if the series is shorter),
    then held permanently at the level reached at the end of that window -- a stable siphoning
    habit, not a temporary dip. `rng` is accepted for signature symmetry with the other
    injectors (this shape has no randomized parameter of its own) but is not drawn from. No-ops
    (see `_is_plantable()`) on an empty series.
    """
    del rng
    if not _is_plantable(len(series), start_index, needs_previous_month=False):
        return list(series), None

    new_series = list(series)
    end_index = min(start_index + duration_months, len(series))

    factor = 1.0
    for index in range(start_index, end_index):
        factor *= 1 - monthly_rate
        new_series[index] = series[index] * factor
    for index in range(end_index, len(series)):
        new_series[index] = series[index] * factor

    plan = AnomalyPlan(
        tipo=AnomalyType.GRADUAL_DECLINE,
        start_index=start_index,
        length=len(series) - start_index,
        fraud_shaped=True,
        parametros={
            "monthly_rate": monthly_rate,
            "duration_months": end_index - start_index,
        },
    )
    return new_series, plan


def inject_spike(
    series: list[float], start_index: int, rng: Random
) -> tuple[list[float], AnomalyPlan | None]:
    """R3 ("Incremento Brusco"): a single month at 3-5x the REALIZED previous month (see this
    module's docstring), reverting immediately afterward -- a non-fraud anomaly (a one-off
    appliance, a meter misreading), unlike every other injector in this module. No-ops (see
    `_is_plantable()`) when the series is too short or has no previous month to anchor to."""
    if not _is_plantable(len(series), start_index, needs_previous_month=True):
        return list(series), None

    multiplier = rng.uniform(_SPIKE_MIN_MULTIPLIER, _SPIKE_MAX_MULTIPLIER)
    new_series = list(series)
    new_series[start_index] = series[start_index - 1] * multiplier

    plan = AnomalyPlan(
        tipo=AnomalyType.SPIKE,
        start_index=start_index,
        length=1,
        fraud_shaped=False,
        parametros={"multiplier": round(multiplier, 4)},
    )
    return new_series, plan


def inject_spike_leve(
    series: list[float], start_index: int, rng: Random
) -> tuple[list[float], AnomalyPlan | None]:
    """Sub-threshold sibling of `inject_spike` (see this module's docstring): a 1.8-2.6x
    multiplier, below R3's +200% trigger on purpose, same anchoring math otherwise. No-ops on a
    too-short series exactly like `inject_spike`."""
    if not _is_plantable(len(series), start_index, needs_previous_month=True):
        return list(series), None

    multiplier = rng.uniform(_SPIKE_LEVE_MIN_MULTIPLIER, _SPIKE_LEVE_MAX_MULTIPLIER)
    new_series = list(series)
    new_series[start_index] = series[start_index - 1] * multiplier

    plan = AnomalyPlan(
        tipo=AnomalyType.SPIKE_LEVE,
        start_index=start_index,
        length=1,
        fraud_shaped=False,
        parametros={"multiplier": round(multiplier, 4)},
    )
    return new_series, plan
