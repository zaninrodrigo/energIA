"""Etapa 3 — Generación de features (US-008, AI_ENGINE_SPEC.md §6) + Etapa 4 — Indicadores
estadísticos (US-009, §7).

`construir_feature_vector` is pure: it takes one suministro's already-fetched, already-filtered
kwh history (`FeatureConsumoRow`, `domain/ports.py`) plus its metadata, and returns a
`FeatureVector` with the 17 features of §6.1 (`FeatureVector.features`, keyed by their EXACT
English identifiers per the spec table) alongside `is_cold_start` and `zscore_self` (the one
Etapa 4 indicator that only needs the suministro's OWN history, not a cross-suministro cohort).
`enriquecer_con_indicadores_cohorte` is the second pass: it fills in `peer_ratio` (F13) and the
two remaining Etapa 4 indicators (`percentile_peer`, `iqr_outlier_flag`), which need EVERY
suministro's current-period kwh in the SAME lote at once (DEC-008 cohort), not just one
suministro's own history -- this is why they cannot be computed inside the first pass.

**Conflicted-period exclusion (DEC-005, AI_ENGINE_SPEC.md §6 note) is NOT this module's job.**
`ProcesarLote._generar_features` (application layer) drops every `FeatureConsumoRow` whose
`consumo_id` appears in Etapa 2's `periodos_conflictivos` (both sides of each pair -- no
canonicity rule exists to prefer one side over the other, so both are excluded) before ever
calling `construir_feature_vector`. This keeps the domain function's contract simple: it always
faithfully reflects whatever history it is handed, with no special-casing for "conflicted" rows
baked in here -- see `tests/unit/contexts/motor/domain/test_features.py`'s
`test_conflicted_period_exclusion_is_a_prior_filtering_concern` for the direct demonstration.
`tiene_periodos_conflictivos` is still a parameter here: a plain passthrough flag (`
has_conflicted_periods` in the output jsonb) the caller already knows from Etapa 2's report, not
something this module derives on its own.

**FIX 1 (reviewer finding, CRITICAL) -- F10 `zero_consumption_streak` is the ONE exception to
"always reflects whatever history it is handed" above.** Excluding a conflicted-but-NONZERO
period (e.g. a double-billed month, kwh > 0) from the filtered `historial_suministro` silently
BRIDGES the gap it leaves behind: two zero periods that are only adjacent because the real,
nonzero period between them got stripped out now look like an uninterrupted streak, inflating
`zero_consumption_streak` past what the supply's raw, physically observed readings actually show
(persisted `streak == 3` reaching R1's Alta-severity trigger `>= 3`, AI_ENGINE_SPEC.md §8, when
the true streak is `2`). A zero-streak is a statement about RAW OBSERVED READINGS -- a conflicted
period, even one excluded from every OTHER window, still evidences the supply was billed some
nonzero consumption that period, so it must still break the streak; a conflicted period whose OWN
kwh is `0` evidences nothing and the streak correctly continues through it.
`construir_feature_vector` therefore takes a SECOND, UNFILTERED history
(`historial_suministro_completo`) used ONLY for F10 -- every other feature (F1-F9, F11-F17) keeps
using the filtered `historial_suministro`, unaffected.
See AI_ENGINE_SPEC.md §6.3's note and
`test_zero_streak_bridges_conflicted_nonzero_period_using_unfiltered_history`/
`test_zero_streak_continues_through_a_conflicted_period_that_is_itself_zero`
(`tests/unit/contexts/motor/domain/test_features.py`) for the direct demonstration.

**Decimal in, float out (precision choice).** Every `FeatureConsumoRow.kwh` is a `Decimal`
(mirrors `consumos.kwh numeric(12,3)`), but every persisted feature value is a plain `float`:
jsonb has no fixed-point decimal type, and Python's `Decimal` is not JSON-serializable without a
custom encoder. `kwh` is converted to `float` once, as early as possible (`_kwh_float`), and
every computation from that point on is ordinary `float` arithmetic (via the `statistics`-module
equivalents hand-rolled below for exact control over ties/interpolation, see `_percentile`).
Precision loss is negligible at these magnitudes: `numeric(12,3)` caps kwh at 9 integer digits +
3 decimals, comfortably inside `float64`'s ~15-17 significant digits.

**F1 (`avg_consumption`) and F8 (`moving_avg_12m`) are mathematically identical under this
window definition** -- both are the mean of the trailing 12-period window ending at (and
including) the current period. This is an honest observation, not a bug: AI_ENGINE_SPEC.md §6.1
names them for different downstream roles (F1 as a general exposure signal feeding the IRE's
"consumo promedio" factor, §10.1; F8 specifically as F9's own baseline term), even though v1's
window definition happens to make their VALUES equal. Both keys are still written to the jsonb
separately, exactly as the spec's identifier table requires -- neither is silently collapsed
into the other.

**F15 (`prior_anomaly_count`) is 0 for every suministro today, correct by construction**: it is
a straight passthrough of whatever `ProcesarLote._generar_features` fetched from `anomalias`/
`resultados_ia` (`domain/ports.py`'s `PriorAnomalyCountRow`) — those tables stay empty until
Etapas 6-7 (Isolation Forest, IRE composition) exist and start writing to them, so every count is
`0` until then, not a placeholder this module invents.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from statistics import stdev
from uuid import UUID

from energia.contexts.motor.domain.ports import FeatureConsumoRow

VERSION_FEATURES = "v1"

# DEC-007 (AI_ENGINE_SPEC.md §6.2/§15): window sizes for the "moving average" features.
VENTANA_CORTA_MESES = 6
VENTANA_LARGA_MESES = 12

# DEC-007: minimum periods of history before a desvío-based feature stops being null. Shared by
# F4 (stddev_consumption), F9 (deviation_from_baseline, via F4), zscore_self (Etapa 4a), AND
# `is_cold_start` itself -- "aligned with DEC-007 minimums" (mission directive #5): a suministro
# with fewer periods than this is cold-start by definition, the same floor every desvío feature
# already needs to produce a non-null value.
MINIMO_PERIODOS_DESVIO = 3

# F11 (trend_slope): a 2-point line has a slope trivially equal to the delta between the two
# points -- not a meaningful REGRESSION until there is at least one more point to fit against.
MINIMO_PERIODOS_TENDENCIA = 4

# DEC-008 (AI_ENGINE_SPEC.md §7/§15): cohort = categoria x localidad; below this size, fall back
# to categoria alone. Provisional constant, calibration-pending against real cohort sizes
# (PROJECT_MASTER_SPEC.md #8) -- same caveat as `domain/checks.py`'s/`domain/duplicidades.py`'s
# own placeholder thresholds.
COHORTE_MINIMA = 10
# If the categoria-alone fallback pool is STILL smaller than this, the Etapa 4 cohort indicators
# (percentile_peer, iqr_outlier_flag) go null -- DEC-008: "si todavía < 3, indicadores null".
# F13 (peer_ratio) does NOT follow this null policy -- see `enriquecer_con_indicadores_cohorte`'s
# docstring for why it always resolves to some ratio instead.
COHORTE_FALLBACK_MINIMA = 3

# Thresholds for the informe's features summary (mission directive #7) -- NOT DEC-0xx decisions,
# implementation-level reporting cutoffs mirroring R4/R5's percentile thresholds (§8) and a
# conventional |z| >= 3 "extreme" cutoff.
UMBRAL_ZSCORE_EXTREMO = 3
UMBRAL_PERCENTILE_EXTREMO = 0.99


@dataclass(frozen=True, slots=True)
class FeatureVector:
    """Etapa 3+4's per-suministro output. `features` is the exact jsonb payload
    `feature_vectors.features` persists (`domain/ports.py`'s `FeatureVectorParaGuardar` is the
    persistence-only projection of this, dropping the transient fields below).

    `categoria_tarifaria_id`/`localidad`/`kwh_actual` are TRANSIENT: needed by
    `enriquecer_con_indicadores_cohorte`'s cross-suministro cohort grouping (DEC-008), but never
    written to the jsonb directly (categoria IS persisted, as the string F17 `categoria_tarifaria`
    inside `features` -- these fields just avoid re-parsing that string back into a UUID for
    grouping purposes; localidad/kwh_actual are not spec features at all, purely grouping keys).
    """

    suministro_id: UUID
    lote_id: UUID
    features: dict[str, object]
    is_cold_start: bool
    has_conflicted_periods: bool
    categoria_tarifaria_id: UUID
    localidad: str | None
    kwh_actual: float


def _kwh_float(value: Decimal) -> float:
    """The one place `Decimal` -> `float` conversion happens for a kwh value -- see this
    module's docstring, "Decimal in, float out"."""
    return float(value)


def _promedio(valores: list[float]) -> float:
    return sum(valores) / len(valores)


def _desvio_estandar(valores: list[float]) -> float | None:
    """Sample standard deviation (n-1 denominator), matching PostgreSQL's `stddev()` (an alias
    for `stddev_samp`) -- so a psql spot-check against `feature_vectors` needs no correction
    factor. `None` (not raised) when fewer than 2 values are given, even though callers already
    guard this with `MINIMO_PERIODOS_DESVIO` (3) before invoking window-stat helpers -- defensive
    only, never expected to trigger in practice."""
    if len(valores) < 2:
        return None
    return stdev(valores)


def _percentile(ordenados: list[float], p: float) -> float:
    """Linear-interpolation percentile (the same method PostgreSQL's `percentile_cont` and
    numpy's default `interpolation="linear"` use) over an ALREADY-SORTED list -- deliberately not
    `statistics.quantiles` (whose `n`-groups API doesn't map directly onto an arbitrary `p` and
    has surprising edge behavior for very small `n`). Used for Q1/Q3 (`p=0.25`/`p=0.75`, IQR) and
    the cohort median (`p=0.5`, F13's denominator) alike, so every cohort statistic in this module
    shares one exact, hand-verifiable formula."""
    n = len(ordenados)
    if n == 1:
        return ordenados[0]
    indice = p * (n - 1)
    piso = math.floor(indice)
    techo = math.ceil(indice)
    if piso == techo:
        return ordenados[int(indice)]
    peso = indice - piso
    return ordenados[piso] + (ordenados[techo] - ordenados[piso]) * peso


def _offsets_meses(fechas: list[date]) -> list[float]:
    """FIX 4 (reviewer finding, WARNING): calendar MONTHS elapsed since `fechas[0]` -- `(year -
    year0) * 12 + (month - month0)`, ignoring day-of-month (periods are ~monthly, `fecha_inicio`
    is each period's start). A period arriving many calendar months after its predecessor (a gap
    in the readings -- e.g. a supply left unread/unbilled for a while) must weigh proportionally
    more in `_pendiente_regresion`'s regression than a normal 1-month step; a plain `0, 1, 2, ...,
    n-1` list index (the old x-axis) cannot tell a 10-month gap apart from a contiguous month, so
    it under/over-states the slope across the jump -- see `_pendiente_regresion`'s docstring and
    `test_trend_slope_uses_calendar_month_offsets_not_list_index`
    (`tests/unit/contexts/motor/domain/test_features.py`) for the worked-out contrast."""
    primero = fechas[0]
    return [
        float((fecha.year - primero.year) * 12 + (fecha.month - primero.month)) for fecha in fechas
    ]


def _pendiente_regresion(valores: list[float], *, offsets_meses: list[float]) -> float:
    """F11 `trend_slope`: OLS slope of `valores` against `offsets_meses` (FIX 4, reviewer
    finding) -- calendar MONTHS elapsed since the window's FIRST period (`_offsets_meses`), NOT
    the periods' plain list position. For a perfectly contiguous monthly window this is
    numerically IDENTICAL to the old `0, 1, 2, ..., n-1` index (both count "1 unit" per month
    apart) -- the fix only changes the slope when the window has an actual calendar gap between
    two periods, which the old index-based x-axis was blind to. The slope is still interpreted as
    kwh/month, not kwh/day -- documented here as the precision/units choice (mission directive
    #3): every consumer of this feature (R-rules, IRE factors) only cares about the SIGN and
    relative magnitude of the trend, not an absolute kwh/day unit."""
    xs = offsets_meses
    media_x = _promedio(xs)
    media_y = _promedio(valores)
    numerador = sum((x - media_x) * (y - media_y) for x, y in zip(xs, valores, strict=True))
    denominador = sum((x - media_x) ** 2 for x in xs)
    if denominador == 0:
        return 0.0  # pragma: no cover — unreachable given the n>=2 guard callers already apply
    return numerador / denominador


def construir_feature_vector(
    *,
    suministro_id: UUID,
    lote_id: UUID,
    historial_suministro: list[FeatureConsumoRow],
    categoria_tarifaria_id: UUID,
    localidad: str | None,
    fecha_alta: date,
    prior_anomaly_count: int,
    tiene_periodos_conflictivos: bool,
    historial_suministro_completo: list[FeatureConsumoRow] | None = None,
) -> "FeatureVector | None":
    """Build one suministro's Etapa 3+4a `FeatureVector` for `lote_id`.

    `historial_suministro` is this ONE suministro's full active consumo history -- any lote,
    already excluding conflicted rows (see module docstring) -- in any order (sorted internally).
    Returns `None` when none of `historial_suministro`'s rows belong to `lote_id` (nothing to
    anchor "the current period" to -- e.g. every period this suministro had in `lote_id` was
    conflicted and got filtered out upstream, or the caller made a bookkeeping mistake): a v1
    policy, not silently scoring against the wrong period.

    `historial_suministro_completo` is FIX 1's addition (reviewer finding, CRITICAL, see module
    docstring): the SAME suministro's history WITHOUT the conflicted-period exclusion, used ONLY
    to compute F10 `zero_consumption_streak` -- every other feature keeps reading `historial_
    suministro` exactly as before. Defaults to `historial_suministro` itself (i.e. "nothing was
    excluded") when omitted, so a caller with no conflicted periods to worry about -- or every
    existing test that predates this fix -- gets byte-identical F10 behavior either way.
    """
    ordenado = sorted(historial_suministro, key=lambda fila: fila.fecha_inicio)
    candidatos_actuales = [fila for fila in ordenado if fila.lote_id == lote_id]
    if not candidatos_actuales:
        return None
    actual = max(candidatos_actuales, key=lambda fila: fila.fecha_inicio)

    # "As of this period" -- window math never looks at periods AFTER `actual` (e.g. a later
    # lote's data already imported), only backward.
    hasta_actual = [fila for fila in ordenado if fila.fecha_inicio <= actual.fecha_inicio]
    kwh_actual = _kwh_float(actual.kwh)
    n_total = len(hasta_actual)
    is_cold_start = n_total < MINIMO_PERIODOS_DESVIO

    ventana_12 = hasta_actual[-VENTANA_LARGA_MESES:]
    kwh_12 = [_kwh_float(fila.kwh) for fila in ventana_12]
    avg_consumption = _promedio(kwh_12)
    max_consumption = max(kwh_12)
    min_consumption = min(kwh_12)
    stddev_consumption = _desvio_estandar(kwh_12) if len(kwh_12) >= MINIMO_PERIODOS_DESVIO else None

    if n_total >= 2:
        kwh_previo = _kwh_float(hasta_actual[-2].kwh)
        pct_change_prev_period = (kwh_actual - kwh_previo) / kwh_previo if kwh_previo != 0 else None
    else:
        pct_change_prev_period = None

    anio_anterior = actual.fecha_inicio.year - 1
    fila_yoy = next(
        (
            fila
            for fila in hasta_actual
            if fila.fecha_inicio.year == anio_anterior
            and fila.fecha_inicio.month == actual.fecha_inicio.month
        ),
        None,
    )
    if fila_yoy is not None and (kwh_yoy := _kwh_float(fila_yoy.kwh)) != 0:
        pct_change_yoy = (kwh_actual - kwh_yoy) / kwh_yoy
    else:
        pct_change_yoy = None

    ventana_6 = hasta_actual[-VENTANA_CORTA_MESES:]
    moving_avg_6m = _promedio([_kwh_float(fila.kwh) for fila in ventana_6])
    moving_avg_12m = avg_consumption  # SAME window as F1 -- see module docstring's honesty note.

    if stddev_consumption is not None and stddev_consumption != 0:
        deviation_from_baseline = (kwh_actual - moving_avg_12m) / stddev_consumption
    else:
        deviation_from_baseline = None

    # FIX 1 (reviewer finding, CRITICAL): F10 walks the UNFILTERED history (`historial_suministro_
    # completo`, module docstring), NOT `hasta_actual` above -- a conflicted-but-nonzero period
    # excluded from `hasta_actual` must still break the streak; a conflicted period that is
    # itself zero still evidences nothing and the streak continues through it either way.
    historial_completo = historial_suministro_completo
    if historial_completo is None:
        historial_completo = historial_suministro
    ordenado_completo = sorted(historial_completo, key=lambda fila: fila.fecha_inicio)
    hasta_actual_completo = [
        fila for fila in ordenado_completo if fila.fecha_inicio <= actual.fecha_inicio
    ]
    zero_consumption_streak = 0
    for fila in reversed(hasta_actual_completo):
        if _kwh_float(fila.kwh) == 0:
            zero_consumption_streak += 1
        else:
            break

    if len(ventana_12) >= MINIMO_PERIODOS_TENDENCIA:
        offsets_ventana_12 = _offsets_meses([fila.fecha_inicio for fila in ventana_12])
        trend_slope = _pendiente_regresion(kwh_12, offsets_meses=offsets_ventana_12)
    else:
        trend_slope = None

    mismo_mes_anios_previos = [
        _kwh_float(fila.kwh)
        for fila in hasta_actual[:-1]
        if fila.fecha_inicio.month == actual.fecha_inicio.month
        and fila.fecha_inicio.year < actual.fecha_inicio.year
    ]
    seasonality_index: float | None
    if not mismo_mes_anios_previos:
        seasonality_index = 1.0  # DEC-007 cold-start default: neutral, no prior year to compare.
    else:
        # FIX 2 (reviewer finding, WARNING): a prior-year same-month MEAN of exactly `0` is a
        # real division-by-zero (there WAS prior-year data, it just billed nothing) -- distinct
        # from "no prior-year data at all" above, and must not be silently reported as the SAME
        # neutral `1.0`, which would mask a 0 -> nonzero jump as "perfectly seasonal".
        media_mes = _promedio(mismo_mes_anios_previos)
        seasonality_index = kwh_actual / media_mes if media_mes != 0 else None

    kwh_historia_completa = [_kwh_float(fila.kwh) for fila in hasta_actual]
    if n_total >= MINIMO_PERIODOS_DESVIO:
        media_total = _promedio(kwh_historia_completa)
        desvio_total = _desvio_estandar(kwh_historia_completa)
        if desvio_total is not None and desvio_total != 0:
            zscore_self = (kwh_actual - media_total) / desvio_total
        else:
            zscore_self = None
    else:
        zscore_self = None

    # FIX 5(a) (reviewer finding, small guard): `fecha_alta` AFTER the period being scored is a
    # data inconsistency (a supply cannot be billed before it was registered) -- `None`, never a
    # negative day count the IRE/downstream consumers would have to special-case.
    supply_age_days = (
        None if fecha_alta > actual.fecha_inicio else (actual.fecha_inicio - fecha_alta).days
    )

    features: dict[str, object] = {
        "avg_consumption": avg_consumption,
        "max_consumption": max_consumption,
        "min_consumption": min_consumption,
        "stddev_consumption": stddev_consumption,
        "pct_change_prev_period": pct_change_prev_period,
        "pct_change_yoy": pct_change_yoy,
        "moving_avg_6m": moving_avg_6m,
        "moving_avg_12m": moving_avg_12m,
        "deviation_from_baseline": deviation_from_baseline,
        "zero_consumption_streak": zero_consumption_streak,
        "trend_slope": trend_slope,
        "seasonality_index": seasonality_index,
        "peer_ratio": None,  # filled by enriquecer_con_indicadores_cohorte (F13, DEC-008)
        "supply_age_days": supply_age_days,
        "prior_anomaly_count": prior_anomaly_count,
        "billed_days": actual.dias_facturados,
        "categoria_tarifaria": str(categoria_tarifaria_id),
        "is_cold_start": is_cold_start,
        "has_conflicted_periods": tiene_periodos_conflictivos,
        "zscore_self": zscore_self,
        "percentile_peer": None,  # filled by enriquecer_con_indicadores_cohorte (DEC-008)
        "iqr_outlier_flag": None,  # filled by enriquecer_con_indicadores_cohorte (DEC-008)
    }

    return FeatureVector(
        suministro_id=suministro_id,
        lote_id=lote_id,
        features=features,
        is_cold_start=is_cold_start,
        has_conflicted_periods=tiene_periodos_conflictivos,
        categoria_tarifaria_id=categoria_tarifaria_id,
        localidad=localidad,
        kwh_actual=kwh_actual,
    )


def enriquecer_con_indicadores_cohorte(
    vectors: list[FeatureVector],
    *,
    cohorte_minima: int = COHORTE_MINIMA,
    cohorte_fallback_minima: int = COHORTE_FALLBACK_MINIMA,
) -> list[FeatureVector]:
    """Second pass (DEC-008): fills F13 `peer_ratio` + Etapa 4b's `percentile_peer`/
    `iqr_outlier_flag`, all three needing the SAME cross-suministro cohort (categoria x
    localidad among `vectors` -- i.e. every ANALYZED suministro of the CURRENT lote, mission
    directive #4 -- with fallback to categoria alone below `cohorte_minima`).

    **F13 `peer_ratio` never goes null purely for a small cohort, unlike the two indicators.**
    A suministro is always a member of its OWN categoria-alone pool (at least itself), so that
    pool is used as `peer_ratio`'s last-resort denominator even when it is smaller than
    `cohorte_fallback_minima` -- only an actual median of `0` (division by zero) nulls it out.
    `percentile_peer`/`iqr_outlier_flag`, by contrast, DO go null when even the categoria-alone
    fallback is smaller than `cohorte_fallback_minima` (DEC-008: "si todavía < 3, indicadores
    null") -- these two feed anomaly signals directly (§8 R4/R5, §7), so a statistically
    meaningless comparison against 1-2 peers is withheld rather than reported as if reliable.

    **FIX 3 (reviewer finding, WARNING) -- `iqr_outlier_flag`'s bounds are LEAVE-ONE-OUT, unlike
    `percentile_peer`.** Computing Q1/Q3 INCLUDING the evaluated suministro's own kwh lets a
    genuine spike inflate its OWN upper bound, masking exactly the outlier it should flag -- a
    3-member fallback cohort `[10, 20, 10000]` never flags `10000` under inclusive bounds (its own
    value drags Q3 -- and therefore the upper fence -- up with it), while `percentile_peer` in the
    SAME vector correctly reports `1.0` for it: an internal contradiction between the two
    indicators that this fix resolves. `percentile_peer` stays inclusive on purpose -- it is the
    standard rank-based statistic ("what fraction of the cohort, INCLUDING me, is at or below my
    own value" is well-defined and correct as-is), only the IQR bounds need the evaluated
    suministro excluded from their own computation. See
    `test_iqr_outlier_flag_uses_leave_one_out_bounds_not_masked_by_self`/
    `test_iqr_outlier_flag_none_when_loo_cohort_below_minimum`
    (`tests/unit/contexts/motor/domain/test_features.py`) and AI_ENGINE_SPEC.md §7's note.
    """
    por_categoria_localidad: dict[tuple[UUID, str | None], list[float]] = defaultdict(list)
    por_categoria: dict[UUID, list[float]] = defaultdict(list)
    for vector in vectors:
        clave = (vector.categoria_tarifaria_id, vector.localidad)
        por_categoria_localidad[clave].append(vector.kwh_actual)
        por_categoria[vector.categoria_tarifaria_id].append(vector.kwh_actual)

    enriquecidos = []
    for vector in vectors:
        clave = (vector.categoria_tarifaria_id, vector.localidad)
        grupo_cat_loc = por_categoria_localidad[clave]
        grupo_cat = por_categoria[vector.categoria_tarifaria_id]

        cohorte_indicadores: list[float] | None
        if len(grupo_cat_loc) >= cohorte_minima:
            cohorte_indicadores = grupo_cat_loc
        elif len(grupo_cat) >= cohorte_fallback_minima:
            cohorte_indicadores = grupo_cat
        else:
            cohorte_indicadores = None

        cohorte_peer_ratio = grupo_cat_loc if len(grupo_cat_loc) >= cohorte_minima else grupo_cat
        mediana = _percentile(sorted(cohorte_peer_ratio), 0.5)
        peer_ratio = vector.kwh_actual / mediana if mediana != 0 else None

        if cohorte_indicadores is None:
            percentile_peer = None
            iqr_outlier_flag = None
        else:
            ordenado = sorted(cohorte_indicadores)
            percentile_peer = sum(1 for x in ordenado if x <= vector.kwh_actual) / len(ordenado)

            # FIX 3 (reviewer finding, WARNING): leave-one-out -- the evaluated suministro's OWN
            # kwh must not inflate its own IQR bounds (docstring above). `.remove` drops exactly
            # ONE occurrence (this suministro's own contribution to `cohorte_indicadores`), not
            # every peer that happens to share the identical reading.
            cohorte_loo = list(cohorte_indicadores)
            cohorte_loo.remove(vector.kwh_actual)
            if len(cohorte_loo) < cohorte_fallback_minima - 1:
                # Unreachable given the `cohorte_indicadores is None` gate above: that gate
                # already required `cohorte_indicadores` to have >= `cohorte_fallback_minima` (or
                # >= `cohorte_minima`) members BEFORE removing self, so `cohorte_loo` can never
                # drop below `cohorte_fallback_minima - 1` -- defensive only, mirrors
                # `_desvio_estandar`'s own never-triggered `len < 2` guard.
                iqr_outlier_flag = None  # pragma: no cover — unreachable, see comment above
            else:
                ordenado_loo = sorted(cohorte_loo)
                q1 = _percentile(ordenado_loo, 0.25)
                q3 = _percentile(ordenado_loo, 0.75)
                iqr = q3 - q1
                limite_inferior = q1 - 1.5 * iqr
                limite_superior = q3 + 1.5 * iqr
                iqr_outlier_flag = (
                    vector.kwh_actual < limite_inferior or vector.kwh_actual > limite_superior
                )

        nuevas_features = dict(vector.features)
        nuevas_features["peer_ratio"] = peer_ratio
        nuevas_features["percentile_peer"] = percentile_peer
        nuevas_features["iqr_outlier_flag"] = iqr_outlier_flag
        enriquecidos.append(replace(vector, features=nuevas_features))

    return enriquecidos


@dataclass(frozen=True, slots=True)
class ResumenFeatures:
    """The informe's features summary (mission directive #7) -- counts only, never the full
    vectors (response-size discipline; vectors live in `feature_vectors`, see `docs/03-
    architecture/API_SPEC.md`)."""

    suministros_con_vector: int
    cold_starts: int
    con_periodos_conflictivos: int
    zscore_extremos: int
    iqr_outliers: int
    percentile_extremos: int


def resumir_features(vectors: list[FeatureVector]) -> ResumenFeatures:
    """Aggregate `vectors` (already cohort-enriched) into the informe's features summary. Plain
    loop with `isinstance` narrowing (not a `sum(1 for ... if ...)` one-liner): `features` is
    typed `dict[str, object]` (jsonb has no fixed schema mypy can see through), so extracting a
    numeric value for the `>=`/`abs()` comparisons needs an explicit narrowing step either way.
    """
    cold_starts = 0
    con_periodos_conflictivos = 0
    zscore_extremos = 0
    iqr_outliers = 0
    percentile_extremos = 0
    for vector in vectors:
        if vector.is_cold_start:
            cold_starts += 1
        if vector.has_conflicted_periods:
            con_periodos_conflictivos += 1

        zscore = vector.features["zscore_self"]
        if isinstance(zscore, int | float) and abs(zscore) >= UMBRAL_ZSCORE_EXTREMO:
            zscore_extremos += 1

        if vector.features["iqr_outlier_flag"] is True:
            iqr_outliers += 1

        percentile = vector.features["percentile_peer"]
        if isinstance(percentile, int | float) and percentile >= UMBRAL_PERCENTILE_EXTREMO:
            percentile_extremos += 1

    return ResumenFeatures(
        suministros_con_vector=len(vectors),
        cold_starts=cold_starts,
        con_periodos_conflictivos=con_periodos_conflictivos,
        zscore_extremos=zscore_extremos,
        iqr_outliers=iqr_outliers,
        percentile_extremos=percentile_extremos,
    )
