"""Orchestrates the whole synthetic dataset: assigns a categoria/localidad/anomaly plan to each
suministro, builds its monthly kwh series (`profiles.py` + `anomalies.py`), and derives the
JSON-able payload lists for the 5 import endpoints (clientes, suministros, lotes, lecturas,
consumos) plus the ground-truth manifest.

Determinism: a single `random.Random(seed)` instance is created once, in `generate_dataset()`,
and threaded through every draw below, in a fixed order -- never a bare `random.random()` call,
never `numpy`'s global RNG state. That is what makes `generate_dataset(seed, scale)` (and
therefore `manifest.py`'s ground truth, which is derived from the same plan) a pure function of
its inputs: same seed and scale always produce byte-identical output.

`_DATASET_START` is a fixed calendar reference (2022-01), not `datetime.now()` -- there is no
wall-clock dependency anywhere in this module. Every monthly period uses the REAL calendar month
length (`_period_bounds()`, via `calendar.monthrange`), not a hardcoded 30 days, so `consumos.
dias_facturados` always matches the real number of days in that period, exactly like production
billing data would.

## Seed-encoded natural keys

Every natural key this module produces (`numero_suministro`, `numero_cliente`, `codigo_lote`)
embeds the seed that produced it (`SYN-S{seed}-SUM-00001`, `SYN-S{seed}-CLI-00001`,
`LOTE-SYN-S{seed}-2022-01`). Without this, two different seeds would produce the SAME identities
(`SYN-SUM-00001` regardless of seed), so re-running with a different seed against the same
EnergIA instance would silently UPSERT over the previous seed's rows through the real import
API's own natural-key matching -- the old dataset's rows would be overwritten in place while its
manifest kept claiming ground truth for data that no longer exists. Seed-encoding the key makes
two different seeds produce entirely disjoint datasets (same-seed re-runs stay idempotent, as
before -- the key does not change across re-runs of the same seed).
"""

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from random import Random

from energia.tools.synthetic.anomalies import (
    ANCHORED_ANOMALY_TYPES,
    AnomalyPlan,
    AnomalyType,
    inject_gradual_decline,
    inject_spike,
    inject_spike_leve,
    inject_sudden_drop,
    inject_sudden_drop_leve,
    inject_zero_consumption_streak,
)
from energia.tools.synthetic.profiles import PROFILES, Categoria, monthly_baseline_kwh

# Fixed calendar reference -- deliberately not `datetime.now()` (see this module's docstring).
_DATASET_START_YEAR = 2022
_DATASET_START_MONTH = 1

# Formosa (Argentina) localities -- give the categoria x localidad cohort (RD-009, DEC-008)
# more than one member per cell, which peer-comparison features (F13, percentile_peer) need.
LOCALIDADES = ("Formosa", "Clorinda", "Pirané", "El Colorado")

# Weighted so a realistic majority of suministros are Residencial, matching real distribution
# feeder patterns (many households, fewer large industrial plants).
CATEGORIA_WEIGHTS: dict[Categoria, float] = {
    Categoria.RESIDENCIAL: 0.70,
    Categoria.COMERCIAL: 0.22,
    Categoria.INDUSTRIAL: 0.08,
}

DEFAULT_ANOMALY_RATE = 0.05

# The 4 rule-strength shapes (each one at, or comfortably beyond, an R1/R2/R3 trigger --
# AI_ENGINE_SPEC.md sec 8 -- except GRADUAL_DECLINE, the one non-rule shape).
RULE_STRENGTH_TYPE_WEIGHTS: dict[AnomalyType, float] = {
    AnomalyType.SUDDEN_DROP: 0.25,
    AnomalyType.ZERO_CONSUMPTION_STREAK: 0.25,
    AnomalyType.GRADUAL_DECLINE: 0.25,
    AnomalyType.SPIKE: 0.25,
}
# The 2 deliberately sub-threshold ("_leve") shapes -- see anomalies.py's docstring, "Sub-
# threshold (`_leve`) siblings", for why they exist (isolating the ML/statistics branches'
# contribution from what the rule engine alone would already catch).
SUB_THRESHOLD_TYPE_WEIGHTS: dict[AnomalyType, float] = {
    AnomalyType.SUDDEN_DROP_LEVE: 0.5,
    AnomalyType.SPIKE_LEVE: 0.5,
}
DEFAULT_TYPE_WEIGHTS: dict[AnomalyType, float] = {
    **RULE_STRENGTH_TYPE_WEIGHTS,
    **SUB_THRESHOLD_TYPE_WEIGHTS,
}

# Of anomalous suministros (beyond the "at least one of each type" guarantee -- see
# `_plan_suministros()`), the fraction assigned a sub-threshold ("_leve") anomaly instead of a
# rule-strength one. Configurable via `plan_suministros()`/`generate_dataset()`'s own
# `sub_threshold_rate` parameter.
DEFAULT_SUB_THRESHOLD_RATE = 0.40

# Cold-start (AI_ENGINE_SPEC.md sec 6.2): the fraction of suministros whose consumption history
# starts partway through the dataset window instead of at month 0.
_COLD_START_RATE = 0.15
_COLD_START_MAX_OFFSET_FRACTION = 3  # late entrants join within the first months//3 months

# Established suministros (not cold-start) predate the dataset by 1-10 years.
_ESTABLISHED_MIN_AGE_DAYS = 365
_ESTABLISHED_MAX_AGE_DAYS = 3650

_INJECTORS = {
    AnomalyType.SUDDEN_DROP: inject_sudden_drop,
    AnomalyType.SUDDEN_DROP_LEVE: inject_sudden_drop_leve,
    AnomalyType.ZERO_CONSUMPTION_STREAK: inject_zero_consumption_streak,
    AnomalyType.GRADUAL_DECLINE: inject_gradual_decline,
    AnomalyType.SPIKE: inject_spike,
    AnomalyType.SPIKE_LEVE: inject_spike_leve,
}

# Margins (months before/after the injection point within the suministro's OWN active series)
# preferred for each anomaly type -- see `_pick_start_index()`. Purely cosmetic/robustness: every
# injector already no-ops safely on its own if a series is too short for its preferred margin
# (see anomalies.py's `_is_plantable()`).
_START_MARGINS: dict[AnomalyType, tuple[int, int]] = {
    AnomalyType.SUDDEN_DROP: (3, 4),
    AnomalyType.SUDDEN_DROP_LEVE: (3, 4),
    AnomalyType.ZERO_CONSUMPTION_STREAK: (3, 6),
    AnomalyType.GRADUAL_DECLINE: (0, 6),
    AnomalyType.SPIKE: (1, 1),
    AnomalyType.SPIKE_LEVE: (1, 1),
}


@dataclass(frozen=True, slots=True)
class ScaleConfig:
    suministros: int
    months: int


SCALES: dict[str, ScaleConfig] = {
    "small": ScaleConfig(suministros=100, months=24),
    "medium": ScaleConfig(suministros=1000, months=36),
    "large": ScaleConfig(suministros=5000, months=36),
}


@dataclass(frozen=True, slots=True)
class SuministroPlan:
    """One suministro's full ground-truth assignment: identity, cohort membership, cold-start
    offset, and (if any) its planted anomaly -- everything `manifest.py` needs to serialize,
    and everything `generate_dataset()` needs to build its payloads."""

    index: int
    numero_suministro: str
    numero_cliente: str
    categoria: Categoria
    localidad: str
    entry_month_offset: int
    fecha_alta: date
    anomaly: AnomalyPlan | None


@dataclass(frozen=True, slots=True)
class SyntheticDataset:
    """Everything `api_loader.py` needs to import, plus the ground-truth manifest
    `manifest.py`/`cli.py` write to disk. Every payload list is already in the exact shape the
    corresponding `POST /api/v1/.../import` endpoint expects (docs/03-architecture/API_SPEC.md).
    """

    seed: int
    scale: str
    months: int
    clientes: list[dict[str, object]]
    suministros: list[dict[str, object]]
    lotes: list[dict[str, object]]
    lecturas: list[dict[str, object]]
    consumos: list[dict[str, object]]
    manifest: dict[str, object] = field(repr=False)


def _period_bounds(year: int, month: int) -> tuple[date, date, int]:
    """`(fecha_inicio, fecha_fin, dias_facturados)` for one calendar month, using the REAL
    number of days in that month (28/29/30/31) -- `calendar.monthrange` already accounts for
    leap years."""
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day), last_day


def _month_index_to_year_month(month_index: int) -> tuple[int, int]:
    """0-based `month_index` (0 = `_DATASET_START_YEAR`-`_DATASET_START_MONTH`) to a
    `(year, month)` pair."""
    absolute_month = (_DATASET_START_MONTH - 1) + month_index
    year = _DATASET_START_YEAR + absolute_month // 12
    month = absolute_month % 12 + 1
    return year, month


def _weighted_choice[T: (Categoria, AnomalyType)](rng: Random, weights: dict[T, float]) -> T:
    keys = list(weights.keys())
    return rng.choices(keys, weights=[weights[key] for key in keys], k=1)[0]


def _pick_start_index(rng: Random, series_length: int, tipo: AnomalyType) -> int:
    """An injection start index within `[0, series_length)`, preferring the margins in
    `_START_MARGINS` but falling back to the widest range that still fits when the series is
    too short for that preference.

    Contract: `series_length` must be `>= 1` -- callers (`_plan_suministros()` below) must skip
    planting an anomaly at all when a suministro's series is shorter than that (an empty, 0-month
    series has no valid index whatsoever). This function does NOT guard `series_length == 0`
    itself: it would return `0`, a value that looks in-range but indexes nothing, which is
    exactly the bug this contract exists to prevent (see anomalies.py's `_is_plantable()`, the
    injectors' own second line of defense against the same case).
    """
    min_before, min_after = _START_MARGINS[tipo]
    lowest, highest = min_before, series_length - min_after
    if highest < lowest:
        lowest, highest = 0, max(series_length - 1, 0)
    return rng.randint(lowest, highest)


def plan_suministros(
    seed: int,
    n_suministros: int,
    months: int,
    *,
    anomaly_rate: float = DEFAULT_ANOMALY_RATE,
    type_weights: dict[AnomalyType, float] | None = None,
    sub_threshold_rate: float = DEFAULT_SUB_THRESHOLD_RATE,
) -> list[SuministroPlan]:
    """Standalone, independently-testable entry point: builds its own seeded
    `random.Random(seed)` and delegates to `_plan_suministros()`.

    `generate_dataset()` does NOT call this function -- it calls `_plan_suministros()` directly
    with the ONE `random.Random(seed)` instance it threads through the entire pipeline (planning
    AND series construction), per this module's single-RNG determinism rule. This function
    exists so planning alone stays unit-testable/reusable without also building every monthly
    kwh series.
    """
    rng = Random(seed)
    return _plan_suministros(
        rng,
        seed,
        n_suministros,
        months,
        anomaly_rate=anomaly_rate,
        type_weights=type_weights,
        sub_threshold_rate=sub_threshold_rate,
    )


def _plan_suministros(
    rng: Random,
    seed: int,
    n_suministros: int,
    months: int,
    *,
    anomaly_rate: float = DEFAULT_ANOMALY_RATE,
    type_weights: dict[AnomalyType, float] | None = None,
    sub_threshold_rate: float = DEFAULT_SUB_THRESHOLD_RATE,
) -> list[SuministroPlan]:
    """Assign every suministro's categoria/localidad/cold-start offset/anomaly, drawing only
    from the given `rng` -- a pure function of `rng`'s current state and the other arguments.
    `seed` is used ONLY to embed in each identity (`numero_suministro`/`numero_cliente` -- see
    this module's docstring, "Seed-encoded natural keys"), never as a source of randomness.

    At least one suministro of each `AnomalyType` is guaranteed (not left to chance): the first
    `len(AnomalyType)` anomaly slots cover each type exactly once, before the remaining slots (if
    `anomaly_rate` implies more) are assigned by weighted random choice -- `sub_threshold_rate`
    of THOSE remaining slots draw from the sub-threshold ("_leve") pool, the rest from the
    rule-strength pool (see `RULE_STRENGTH_TYPE_WEIGHTS`/`SUB_THRESHOLD_TYPE_WEIGHTS`).

    A slot whose suministro's own series is too short to plant anything meaningful in (`months -
    entry_month_offset < 1`, e.g. a cold-start suministro in a 1-month dataset) is skipped
    entirely -- its `anomaly` stays `None` (see anomalies.py's `_is_plantable()`/this module's
    `_pick_start_index()` contract note for why planting there would be meaningless anyway).
    """
    weights = type_weights or DEFAULT_TYPE_WEIGHTS
    types = list(weights.keys())
    rule_pool = {tipo: weights[tipo] for tipo in types if tipo in RULE_STRENGTH_TYPE_WEIGHTS}
    leve_pool = {tipo: weights[tipo] for tipo in types if tipo in SUB_THRESHOLD_TYPE_WEIGHTS}

    plans: list[SuministroPlan] = []
    for index in range(n_suministros):
        categoria = _weighted_choice(rng, CATEGORIA_WEIGHTS)
        localidad = rng.choice(LOCALIDADES)

        is_cold_start = rng.random() < _COLD_START_RATE
        max_offset = max(1, months // _COLD_START_MAX_OFFSET_FRACTION)
        entry_month_offset = rng.randint(1, max_offset) if is_cold_start else 0

        if entry_month_offset == 0:
            age_days = rng.randint(_ESTABLISHED_MIN_AGE_DAYS, _ESTABLISHED_MAX_AGE_DAYS)
            fecha_alta = date(_DATASET_START_YEAR, _DATASET_START_MONTH, 1) - timedelta(
                days=age_days
            )
        else:
            entry_year, entry_month = _month_index_to_year_month(entry_month_offset)
            fecha_alta = date(entry_year, entry_month, 1)

        plans.append(
            SuministroPlan(
                index=index,
                numero_suministro=f"SYN-S{seed}-SUM-{index:05d}",
                numero_cliente=f"SYN-S{seed}-CLI-{index:05d}",
                categoria=categoria,
                localidad=localidad,
                entry_month_offset=entry_month_offset,
                fecha_alta=fecha_alta,
                anomaly=None,
            )
        )

    affected_count = max(len(types), round(anomaly_rate * n_suministros))
    affected_count = min(affected_count, n_suministros)
    affected_indices = rng.sample(range(n_suministros), affected_count)

    for slot, suministro_index in enumerate(affected_indices):
        plan = plans[suministro_index]
        series_length = months - plan.entry_month_offset
        if series_length < 1:
            continue  # too short to plant anything meaningful -- anomaly stays None (FIX 3)

        if slot < len(types):
            tipo = types[slot]
        else:
            draw_sub_threshold = bool(leve_pool) and (
                not rule_pool or rng.random() < sub_threshold_rate
            )
            tipo = _weighted_choice(rng, leve_pool if draw_sub_threshold else rule_pool or weights)

        start_index = _pick_start_index(rng, series_length, tipo)

        # Only the start index is decided here; the injector itself draws its own randomized
        # magnitude (drop fraction, streak length, spike multiplier) when the series is built,
        # in `_build_kwh_series()` below, from this SAME threaded `rng` -- so the whole plan +
        # build sequence stays one single deterministic draw order for a given seed.
        placeholder_anomaly = AnomalyPlan(
            tipo=tipo,
            start_index=start_index,
            length=0,
            fraud_shaped=False,
            parametros={},
        )
        plans[suministro_index] = SuministroPlan(
            index=plan.index,
            numero_suministro=plan.numero_suministro,
            numero_cliente=plan.numero_cliente,
            categoria=plan.categoria,
            localidad=plan.localidad,
            entry_month_offset=plan.entry_month_offset,
            fecha_alta=plan.fecha_alta,
            anomaly=placeholder_anomaly,
        )

    return plans


def _build_kwh_series(
    rng: Random, plan: SuministroPlan, months: int
) -> tuple[list[float], AnomalyPlan | None]:
    """The suministro's full monthly kwh series (length `months - entry_month_offset`), with its
    planted anomaly (if any) actually applied -- returns the FINAL `AnomalyPlan` (the injector's
    own randomized parameters filled in), not the placeholder `plan_suministros()` produced.
    `None` when there is no planted anomaly, OR when the injector itself no-opped because the
    series was too short (see anomalies.py's `_is_plantable()`)."""
    profile = PROFILES[plan.categoria]
    series_length = months - plan.entry_month_offset

    series = [
        monthly_baseline_kwh(
            rng,
            profile,
            calendar_month=_month_index_to_year_month(plan.entry_month_offset + local_index)[1],
            elapsed_months=plan.entry_month_offset + local_index,
        )
        for local_index in range(series_length)
    ]

    if plan.anomaly is None:
        return series, None

    injector = _INJECTORS[plan.anomaly.tipo]
    new_series, final_plan = injector(series, plan.anomaly.start_index, rng)
    return new_series, final_plan


def generate_dataset(
    seed: int,
    scale: str,
    *,
    months: int | None = None,
    anomaly_rate: float = DEFAULT_ANOMALY_RATE,
    sub_threshold_rate: float = DEFAULT_SUB_THRESHOLD_RATE,
) -> SyntheticDataset:
    """Build the full synthetic dataset (5 import payload lists + ground-truth manifest) for one
    `(seed, scale)` combination -- a pure function: no I/O, no wall clock, no dict-ordering
    nondeterminism, no `numpy` global RNG. Calling this twice with the same arguments produces
    identical output (see `tests/unit/tools/synthetic/test_generator.py`,
    `test_generate_dataset_is_deterministic_given_the_same_seed_and_scale`). Two DIFFERENT seeds
    produce entirely disjoint natural keys (see this module's docstring, "Seed-encoded natural
    keys").
    """
    scale_config = SCALES[scale]
    resolved_months = months or scale_config.months

    # ONE seeded RNG, threaded through both planning and series construction below -- this
    # module's single-RNG determinism rule (see module docstring).
    rng = Random(seed)
    plans = _plan_suministros(
        rng,
        seed,
        scale_config.suministros,
        resolved_months,
        anomaly_rate=anomaly_rate,
        sub_threshold_rate=sub_threshold_rate,
    )

    clientes: list[dict[str, object]] = []
    suministros: list[dict[str, object]] = []
    lecturas: list[dict[str, object]] = []
    consumos: list[dict[str, object]] = []
    consumo_count_by_lote: dict[str, int] = {}
    anomalias_manifest: list[dict[str, object]] = []

    for plan in plans:
        clientes.append(
            {
                "numero_cliente": plan.numero_cliente,
                "nombre": f"Cliente Sintetico {plan.index:05d}",
            }
        )
        suministros.append(
            {
                "numero_suministro": plan.numero_suministro,
                "numero_cliente": plan.numero_cliente,
                "categoria_tarifaria": plan.categoria.value,
                "localidad": plan.localidad,
                "fecha_alta": plan.fecha_alta.isoformat(),
            }
        )

        series, final_anomaly = _build_kwh_series(rng, plan, resolved_months)

        lectura_anterior = round(rng.uniform(0.0, 10_000.0), 3)
        kwh_rounded_series: list[float] = []
        for local_index, kwh in enumerate(series):
            month_index = plan.entry_month_offset + local_index
            year, month = _month_index_to_year_month(month_index)
            fecha_inicio, fecha_fin, dias_facturados = _period_bounds(year, month)
            codigo_lote = f"LOTE-SYN-S{seed}-{year:04d}-{month:02d}"

            kwh_rounded = round(kwh, 3)
            kwh_rounded_series.append(kwh_rounded)
            lectura_actual = round(lectura_anterior + kwh_rounded, 3)

            lecturas.append(
                {
                    "numero_suministro": plan.numero_suministro,
                    "fecha_lectura": fecha_fin.isoformat(),
                    "lectura_anterior": lectura_anterior,
                    "lectura_actual": lectura_actual,
                    "dias_facturados": dias_facturados,
                }
            )
            consumos.append(
                {
                    "numero_suministro": plan.numero_suministro,
                    "codigo_lote": codigo_lote,
                    "fecha_inicio": fecha_inicio.isoformat(),
                    "fecha_fin": fecha_fin.isoformat(),
                    "dias_facturados": dias_facturados,
                    "kwh": kwh_rounded,
                    "fecha_lectura": fecha_fin.isoformat(),
                }
            )
            consumo_count_by_lote[codigo_lote] = consumo_count_by_lote.get(codigo_lote, 0) + 1

            lectura_anterior = lectura_actual

        if final_anomaly is not None:
            start_year, start_month = _month_index_to_year_month(
                plan.entry_month_offset + final_anomaly.start_index
            )
            parametros = dict(final_anomaly.parametros)
            if final_anomaly.tipo in ANCHORED_ANOMALY_TYPES:
                # REALIZED pct_change, computed from the ACTUAL persisted (rounded) kwh values
                # -- never re-derived by calibration consumers from the drawn parameter alone
                # (see anomalies.py's docstring, "Anchoring `sudden_drop`/`spike`...").
                anchor_kwh = kwh_rounded_series[final_anomaly.start_index - 1]
                first_month_kwh = kwh_rounded_series[final_anomaly.start_index]
                # No extra rounding here: this must be BIT-IDENTICAL to what any calibration
                # consumer would compute from the SAME persisted `consumos.kwh` values, so a
                # comparison against `manifest["anomalias"][i]["parametros"]
                # ["pct_change_first_month"]` never needs an epsilon tolerance.
                parametros["pct_change_first_month"] = first_month_kwh / anchor_kwh - 1
            anomalias_manifest.append(
                {
                    "numero_suministro": plan.numero_suministro,
                    "tipo": final_anomaly.tipo.value,
                    "fraud_shaped": final_anomaly.fraud_shaped,
                    "periodo_inicio": date(start_year, start_month, 1).isoformat(),
                    "periodos_afectados": final_anomaly.length,
                    "parametros": parametros,
                }
            )

    lotes: list[dict[str, object]] = [
        {
            "codigo_lote": codigo_lote,
            "nombre": f"Sintetico {codigo_lote.removeprefix('LOTE-SYN-')}",
            "cantidad_registros": cantidad,
        }
        for codigo_lote, cantidad in sorted(consumo_count_by_lote.items())
    ]

    manifest = {
        "seed": seed,
        "scale": scale,
        "months": resolved_months,
        "suministros": [
            {
                "numero_suministro": plan.numero_suministro,
                "categoria": plan.categoria.value,
                "localidad": plan.localidad,
                "entry_month_offset": plan.entry_month_offset,
                "fecha_alta": plan.fecha_alta.isoformat(),
            }
            for plan in sorted(plans, key=lambda item: item.index)
        ],
        "anomalias": sorted(anomalias_manifest, key=lambda item: str(item["numero_suministro"])),
    }

    return SyntheticDataset(
        seed=seed,
        scale=scale,
        months=resolved_months,
        clientes=clientes,
        suministros=suministros,
        lotes=lotes,
        lecturas=lecturas,
        consumos=consumos,
        manifest=manifest,
    )
