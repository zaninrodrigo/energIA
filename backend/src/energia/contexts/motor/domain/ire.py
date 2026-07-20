"""Etapa 7 -- Composición del IRE (AI_ENGINE_SPEC.md §10) + Etapa 8 -- Impacto Económico Estimado
(§11). The **convergence** of the three branches (reglas, estadística, ML) into a single priority
score per suministro.

**Execution order (implementation note, §3 diagram amendment).** The spec's §3 pipeline diagram
draws stage [7] (IRE) before [8] (IEE) as a reading order, but the IEE is one of the IRE's own 8
factors (weight 0.10, §10.1) -- it must be COMPUTED first. This module reflects that: IEE math
(`calcular_iee_kwh`/`normalizar_iee_lote`) runs, then its normalized output feeds `componer_ire`.
Persistence stays atomic regardless (both land in the SAME transaction, `application/
procesar_lote.py`) -- only the in-memory COMPUTATION order is 8 -> 7, not the pipeline's
stage-numbering/table-mapping (§14), which is unchanged.

**Pure, no I/O.** Every function here takes an already-built `FeatureVector` (Etapa 3-4) plus
already-computed Etapa 6 (ML) outputs -- never recomputes a feature, never queries anything.

**Null feature policy (mission directive #3).** A null input feature makes its OWN factor
contribute exactly `0` (never penalized, per §6.2's cold-start-must-not-be-penalized principle)
-- `componer_ire` computes every factor's value regardless, but `ComposicionIRE.factores` (the
persisted DEC-016 breakdown) only KEEPS factors with a nonzero contribution, matching DEC-016's own
wording ("una lista de estos objetos, uno por factor con contribución no nula"). A null-caused
zero-value factor and a genuinely-computed zero-value factor are therefore indistinguishable in
the persisted breakdown -- both simply do not appear. This is a known, documented v1 simplification
(not a bug): the alternative (persisting a `{"reason": "sin datos suficientes"}` row with `
contribution: 0`) was considered and rejected as contradicting DEC-016's explicit "no nula" wording
verbatim, a decision already validated 2026-07-14.
"""

import bisect
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from energia.contexts.motor.domain.features import FeatureVector
from energia.contexts.motor.domain.isolation_forest import (
    BANDA_ALTO_RIESGO_MAX,
    CLASIFICACION_CRITICO,
    bandear_clasificacion,
)

# ---------------------------------------------------------------------------------------------
# §10.1 -- Fórmula: 8 factors, canonical weights (sum = 1.00).
# ---------------------------------------------------------------------------------------------

CLAVE_SCORE_IA = "score_ia"
CLAVE_HISTORIAL_CONSUMOS = "historial_consumos"
CLAVE_PERSISTENCIA_ANOMALIAS = "persistencia_anomalias"
CLAVE_VARIACION_PORCENTUAL = "variacion_porcentual"
CLAVE_IMPACTO_ECONOMICO = "impacto_economico"
CLAVE_INSPECCIONES_ANTERIORES = "inspecciones_anteriores"
CLAVE_CONSUMO_PROMEDIO = "consumo_promedio"
CLAVE_CATEGORIA_TARIFARIA = "categoria_tarifaria"

PESOS_IRE: dict[str, float] = {
    CLAVE_SCORE_IA: 0.30,
    CLAVE_HISTORIAL_CONSUMOS: 0.15,
    CLAVE_PERSISTENCIA_ANOMALIAS: 0.15,
    CLAVE_VARIACION_PORCENTUAL: 0.15,
    CLAVE_IMPACTO_ECONOMICO: 0.10,
    CLAVE_INSPECCIONES_ANTERIORES: 0.08,
    CLAVE_CONSUMO_PROMEDIO: 0.04,
    CLAVE_CATEGORIA_TARIFARIA: 0.03,
}

# v1 (§10.1's explicit note): "inspecciones anteriores" has no feedback data yet -- always 0 --
# its weight is redistributed PROPORTIONALLY among the other 7, not dropped/ignored.
CLAVES_CERO_V1: frozenset[str] = frozenset({CLAVE_INSPECCIONES_ANTERIORES})


def redistribuir_pesos(pesos: Mapping[str, float], claves_cero: frozenset[str]) -> dict[str, float]:
    """Generic proportional redistribution: every key in `claves_cero` gets effective weight `0`;
    every OTHER key's weight is scaled by `1 / (1 - sum(pesos[k] for k in claves_cero))` so the
    remaining keys' weights still sum to `1.0` overall. Pure arithmetic, no domain knowledge --
    `PESOS_EFECTIVOS_V1` below is this module's own (v1-specific) application of it."""
    masa_cero = sum(pesos[clave] for clave in claves_cero)
    masa_restante = 1.0 - masa_cero
    return {
        clave: (0.0 if clave in claves_cero else peso / masa_restante)
        for clave, peso in pesos.items()
    }


PESOS_EFECTIVOS_V1: dict[str, float] = redistribuir_pesos(PESOS_IRE, CLAVES_CERO_V1)

# ---------------------------------------------------------------------------------------------
# §10.4 -- Normalización por factor (implementación v1): constantes de calibración pendiente
# (PROJECT_MASTER_SPEC.md #8), documentadas explícitamente -- ver AI_ENGINE_SPEC.md §10.4.
# ---------------------------------------------------------------------------------------------

# historial_consumos: |deviation_from_baseline| (F9, z-score) capped at this many std devs, then
# scaled so the cap maps to 100 (`* 20`, since 5 * 20 = 100).
HISTORIAL_Z_CAP = 5.0
HISTORIAL_ESCALA = 100.0 / HISTORIAL_Z_CAP  # 20.0

# persistencia_anomalias: prior_anomaly_count (F15) weighted more heavily than the current
# zero-streak (F10) -- a CONFIRMED history of anomalies is a stronger persistence signal than an
# in-progress streak -- capped at 100.
PERSISTENCIA_PESO_PRIOR_COUNT = 25.0
PERSISTENCIA_PESO_ZERO_STREAK = 10.0
PERSISTENCIA_CAP = 100.0

# variacion_porcentual: max(|F5|, |F6|) capped at 300% (3.0), scaled so the cap maps to 100.
VARIACION_CAP = 3.0
VARIACION_ESCALA = 100.0 / VARIACION_CAP

# categoria_tarifaria: "ajuste por criticidad de categoría" (§10.1) -- no `precio_kwh`/criticidad
# column exists anywhere in the schema (categorias_tarifarias only has nombre/descripcion, same
# gap DEC-017/§16 already documents for the IEE) -- this is a v1 FIXED lookup table, calibration-
# pending (PROJECT_MASTER_SPEC.md #8), keyed on the exact seeded `nombre`s (`docker/postgres/init/
# 04_seed.sql`). Reasoning: economic exposure per meter/inspection roughly tracks industrial >
# grandes demandas > comercial > residencial > alumbrado público (a municipal, non-fraud-typical
# consumer) -- a judgment call, not measured data, exactly like R-rule thresholds (DEC-009).
CRITICIDAD_CATEGORIA: dict[str, float] = {
    "Grandes Demandas": 100.0,
    "Industrial": 90.0,
    "Comercial": 60.0,
    "Residencial": 30.0,
    "Alumbrado Público": 20.0,
}
# Unknown/missing categoria nombre (data drift, e.g. a category renamed after this table was
# written) -- neutral midpoint, never 0 (which would silently zero out a real factor) nor 100
# (which would silently maximize it).
CRITICIDAD_DEFAULT = 50.0

# §10.3/§8 -- v1 ML-only anomaly policy: 'Patrón Irregular' (reserved for the ML branch, §8's own
# note) fires when the ML score alone lands in the Crítico band AND no rule already explains this
# suministro this lote (dedup: rule hits are canonical). Reuses DEC-015's own upper Alto-Riesgo
# bound as the "Crítico" threshold -- single source of truth with `bandear_clasificacion`.
UMBRAL_ANOMALIA_ML = BANDA_ALTO_RIESGO_MAX


def debe_generar_anomalia_ml(clasificacion_ml: str, *, tiene_hits_reglas: bool) -> bool:
    """v1 policy (§8/§10.3 note): an ML-only 'Patrón Irregular' anomaly is generated when the ML
    branch's OWN preliminary verdict (`predicciones.clasificacion`, DEC-015 bands on
    `ml_score_0_100` alone) is `"Crítico"` AND no R1-R6 rule already fired for this suministro
    this lote -- rule hits are canonical/explicable (RN-012), so a rule-covered suministro does
    NOT also get a second, less-explicable ML-only anomaly for the same signal."""
    return clasificacion_ml == CLASIFICACION_CRITICO and not tiene_hits_reglas


# ---------------------------------------------------------------------------------------------
# §11 -- Impacto Económico Estimado (IEE), en kWh (DEC-017).
# ---------------------------------------------------------------------------------------------


def calcular_iee_kwh(vector: FeatureVector) -> float | None:
    """§11.1: `IEE_kwh = max(0, kwh_esperado - kwh_facturado)`. `kwh_esperado` is the suministro's
    OWN baseline: `moving_avg_12m` (F8), falling back to `moving_avg_6m` (F7) if F8 is null (never
    happens under this implementation's `domain/features.py` -- F8 always resolves to SOME value,
    even a 1-period "average" -- kept as a defensive fallback matching the mission's literal
    wording, mirrors other `pragma: no cover`-shaped guards elsewhere in this bounded context).
    Cold-start suministros (`is_cold_start`, fewer than `MINIMO_PERIODOS_DESVIO` = 3 periods of
    history, `domain/features.py`) get NO IEE at all (`None`) -- there is not enough history yet
    to call any level "expected", so no loss estimate is safer than a fabricated one."""
    if vector.is_cold_start:
        return None
    esperado = vector.features.get("moving_avg_12m")
    if not isinstance(esperado, int | float):
        esperado = vector.features.get("moving_avg_6m")
    if not isinstance(esperado, int | float):
        return None  # pragma: no cover — defensive; F7/F8 always resolve in this implementation
    return max(0.0, float(esperado) - vector.kwh_actual)


def normalizar_iee_lote(iees: Mapping[UUID, float | None]) -> dict[UUID, float]:
    """IEE normalization for the IRE's `impacto_economico` factor (§10.1, weight 0.10): min-max
    within the lote, computed ONLY over the NONZERO IEEs (a suministro with `0`/`None` IEE has no
    estimated loss at all -- including it in the min-max range would compress the differentiation
    among suministros that DO have real, nonzero loss). All-zero/all-`None` lote -> every factor
    `0.0` (no loss anywhere, nothing to differentiate).

    **Degenerate tie, documented divergence from the ML branch's own convention.** When every
    NONZERO IEE in the lote is identical (including the single-nonzero-value case), this maps
    them to `100.0`, not `0.5`/`50.0` (`normalizar_scores_min_max_invertido`'s own degenerate
    case, `domain/isolation_forest.py`): there, a tied score means "no differentiating signal
    exists among possibly-normal-or-anomalous suministros", a genuinely neutral case. Here, a
    tied NONZERO IEE means "every one of these suministros already has real, confirmed loss,
    just not distinguishable in magnitude from its peers" -- understating that as a neutral
    midpoint would silently discount confirmed loss, so this implementation reports the top of
    the scale instead. Note the minimum NONZERO IEE itself still maps to `0.0` (a genuine min-max
    property, not a special case) -- the smallest real loss in the lote is not distinguishable
    from "no loss" on this 0-100 scale, a known v1 approximation."""
    valores_no_cero = [valor for valor in iees.values() if valor is not None and valor > 0]
    if not valores_no_cero:
        return dict.fromkeys(iees, 0.0)

    minimo = min(valores_no_cero)
    maximo = max(valores_no_cero)
    resultado: dict[UUID, float] = {}
    for suministro_id, iee in iees.items():
        if iee is None or iee <= 0:
            resultado[suministro_id] = 0.0
        elif maximo == minimo:
            resultado[suministro_id] = 100.0
        else:
            resultado[suministro_id] = (iee - minimo) / (maximo - minimo) * 100.0
    return resultado


def normalizar_consumo_promedio_lote(vectores: Sequence[FeatureVector]) -> dict[UUID, float]:
    """`consumo_promedio` factor (§10.1: "avg_consumption (F1) como factor de exposición") --
    plain min-max of F1 across every ANALYZED suministro of the CURRENT lote (mirrors DEC-013's
    own per-lote min-max idiom for the ML score, `domain/isolation_forest.py`, reused here for
    consistency rather than inventing a second normalization style). Degenerate case (every
    suministro's `avg_consumption` identical) maps to the NEUTRAL midpoint `50.0` -- same
    reasoning as `normalizar_scores_min_max_invertido`'s own degenerate case: no differentiating
    signal among them, neither "least exposed" nor "most exposed" is a truthful claim."""
    if not vectores:
        return {}
    valores = {
        vector.suministro_id: _a_float(vector.features["avg_consumption"]) for vector in vectores
    }
    minimo = min(valores.values())
    maximo = max(valores.values())
    if maximo == minimo:
        return dict.fromkeys(valores, 50.0)
    return {
        suministro_id: (valor - minimo) / (maximo - minimo) * 100.0
        for suministro_id, valor in valores.items()
    }


def calcular_percentil_real_consumo_promedio_lote(
    vectores: Sequence[FeatureVector],
) -> dict[UUID, float]:
    """RN-012 honesty fix (reviewer finding, CRITICAL): `_factor_consumo_promedio`'s explicability
    TEXT used to quote `normalizar_consumo_promedio_lote`'s MIN-MAX score AS IF it were a
    percentile (e.g. SYN-S42-SUM-00005/SUM-00049: the text said "percentil 1"/"percentil 5" when
    the TRUE percentile of that suministro's `avg_consumption` within its lote was ~66/~70 -- off
    by ~65 points on a right-skewed lote, where min-max compresses everyone below the extreme
    outlier toward 0). This function computes the ACTUAL percentile RANK (0-100, linear-
    interpolation convention -- the k-th order statistic of `n` sits at `k / (n-1) * 100`, the
    SAME convention `_percentil` below uses in reverse for `DistribucionIRE`'s p50/p95) of each
    suministro's `avg_consumption` (F1) within the lote -- used ONLY for the `consumo_promedio`
    factor's TEXT (`_factor_consumo_promedio`), NEVER for the weight math, which stays on
    `normalizar_consumo_promedio_lote`'s min-max scale (§10.1's factor formula is defined on
    min-max, unchanged by this fix). The two numbers are deliberately different scales and can
    diverge sharply on a skewed lote -- reporting one as if it were the other was the bug.

    Ties: `bisect.bisect_left` -- the FIRST (lowest) index among equal values, a deterministic,
    documented tie-break (same "pick one convention and document it" spirit as `_percentil`'s own
    floor/ceil handling). Degenerate single-suministro lote -> neutral midpoint `50.0` (mirrors
    `normalizar_consumo_promedio_lote`'s own all-equal degenerate case): no peer exists to rank
    against."""
    if not vectores:
        return {}
    if len(vectores) == 1:
        return {vectores[0].suministro_id: 50.0}
    valores_ordenados = sorted(_a_float(vector.features["avg_consumption"]) for vector in vectores)
    n = len(valores_ordenados)
    return {
        vector.suministro_id: bisect.bisect_left(
            valores_ordenados, _a_float(vector.features["avg_consumption"])
        )
        / (n - 1)
        * 100.0
        for vector in vectores
    }


# ---------------------------------------------------------------------------------------------
# §10.1/§10.2 -- Composición del IRE.
# ---------------------------------------------------------------------------------------------


def _a_float(valor: object) -> float:
    """Narrows a `FeatureVector.features` value (typed `object`, jsonb has no fixed schema mypy
    can see through) to `float` -- mirrors `domain/features.py`'s/`domain/isolation_forest.py`'s
    own `isinstance`-narrowing helpers for the same structural reason. Only ever called on keys
    this module already knows are numeric and non-null by construction (`avg_consumption`,
    `domain/features.py`'s F1, never null)."""
    assert isinstance(valor, int | float)
    return float(valor)


def redondear_half_up(valor: float) -> int:
    """`ire.valor` is `numeric(5,2)` (accepts decimals), but this implementation persists an
    INTEGER (documented v1 choice, mission directive #4): the GENERATED `ire.nivel` column bands
    on integer-shaped ranges (`0-20`, `21-40`, ...), and DEC-015's `resultados_ia.clasificacion`
    bridge is likewise defined on integer boundaries -- rounding once, consistently, before
    persistence keeps both derived readings unambiguous. Half-up (`floor(x + 0.5)`), NOT Python's
    `round()` (banker's rounding, which would round `61.5` down to `62`... actually to `62` too by
    luck, but `62.5` DOWN to `62` -- half-up always rounds `x.5` up, matching the conventional
    "round half up" business rule, not IEEE 754's round-half-to-even). Safe unguarded: `componer_
    ire`'s weighted sum is always `>= 0` (every factor `>= 0`, every weight `>= 0`) at these
    magnitudes."""
    return math.floor(valor + 0.5)


def bandear_nivel_ire(valor: int) -> str:
    """Mirrors `ire.nivel`'s GENERATED column formula EXACTLY (`docker/postgres/init/
    01_schema.sql`, §10.2's table) -- a deliberate small duplication (documented, not accidental):
    the informe (mission directive #7) needs to report `nivel` counts/labels in Python, before the
    row is even persisted, and this 5-band CASE expression is fixed at the schema level (would
    require a migration to ever change) -- the SAME "each module stays self-contained rather than
    sharing a private helper across files" precedent `domain/isolation_forest.py`'s own `_percentil`
    docstring already establishes for this codebase."""
    if valor <= 20:
        return "Muy Bajo"
    if valor <= 40:
        return "Bajo"
    if valor <= 60:
        return "Medio"
    if valor <= 80:
        return "Alto"
    return "Crítico"


@dataclass(frozen=True, slots=True)
class FactorContribucion:
    """One factor's entry in the DEC-016 explicability breakdown -- `contribution` is `peso_
    efectivo * valor_0_100` (points contributed to the composed IRE, §10.1's own `IRE = Σ(wᵢ ·
    factorᵢ)` formula), `reason` a neutral-Spanish, human-readable sentence built from the actual
    numbers that produced it (RN-012). The ML factor's `reason` is marked "(aproximación)" per
    §10.3's honesty note on Isolation Forest's non-causal explainability."""

    factor: str
    contribution: float
    reason: str


@dataclass(frozen=True, slots=True)
class ComposicionIRE:
    """One suministro's Etapa 7 outcome: `ire_valor` is the ROUNDED (half-up) integer persisted to
    `ire.valor`; `valor_crudo` is the un-rounded weighted sum (kept for tests/debugging, never
    persisted directly); `clasificacion` is DEC-015's 4-band mapping of `ire_valor`, reused
    (single source of truth) from `domain/isolation_forest.py`'s `bandear_clasificacion` --
    `resultados_ia.clasificacion` persists this value (NOT `predicciones.clasificacion`, which
    bands the ML score ALONE, §9.4's distinction note). `factores` keeps only NONZERO-contribution
    entries (DEC-016) -- this IS the list serialized into `resultados_ia.observaciones`."""

    ire_valor: int
    valor_crudo: float
    clasificacion: str
    factores: tuple[FactorContribucion, ...]


def _factor_score_ia(ml_score_0_100: float) -> tuple[float, str]:
    return ml_score_0_100, (
        f"Score del modelo de IA: {ml_score_0_100:.1f}/100 "
        "(aproximación por atribución de features)"
    )


def _factor_historial_consumos(vector: FeatureVector) -> tuple[float, str] | None:
    desvio = vector.features.get("deviation_from_baseline")
    if not isinstance(desvio, int | float):
        return None
    valor = min(abs(desvio), HISTORIAL_Z_CAP) * HISTORIAL_ESCALA
    return valor, (
        f"Desviación de {desvio:+.2f} desvíos estándar respecto de la línea base histórica"
    )


def _factor_persistencia_anomalias(vector: FeatureVector) -> tuple[float, str]:
    # F15/F10 are never null (domain/features.py) -- always computable, unlike the two guards
    # above.
    conteo_previo = vector.features["prior_anomaly_count"]
    racha_cero = vector.features["zero_consumption_streak"]
    assert isinstance(conteo_previo, int)  # narrows for mypy; guaranteed by domain/features.py
    assert isinstance(racha_cero, int)
    valor = min(
        conteo_previo * PERSISTENCIA_PESO_PRIOR_COUNT + racha_cero * PERSISTENCIA_PESO_ZERO_STREAK,
        PERSISTENCIA_CAP,
    )
    return valor, (
        f"{conteo_previo} anomalía(s) previa(s) registrada(s) y racha actual de {racha_cero} "
        "período(s) consecutivos en cero consumo"
    )


def _factor_variacion_porcentual(vector: FeatureVector) -> tuple[float, str] | None:
    f5 = vector.features.get("pct_change_prev_period")
    f6 = vector.features.get("pct_change_yoy")
    candidatos: list[tuple[float, str]] = []
    if isinstance(f5, int | float):
        candidatos.append((f5, "el período anterior"))
    if isinstance(f6, int | float):
        candidatos.append((f6, "el mismo período del año anterior"))
    if not candidatos:
        return None
    pct_elegido, referencia = max(candidatos, key=lambda par: abs(par[0]))
    valor = min(abs(pct_elegido), VARIACION_CAP) * VARIACION_ESCALA
    return valor, f"Variación de {pct_elegido:+.0%} respecto de {referencia}"


def _factor_impacto_economico(
    iee_kwh: float | None, iee_normalizado_0_100: float
) -> tuple[float, str]:
    descripcion_kwh = (
        f"{iee_kwh:.1f} kWh no facturados" if iee_kwh is not None else "sin estimar (cold-start)"
    )
    return iee_normalizado_0_100, f"Impacto económico estimado: {descripcion_kwh}"


def _factor_consumo_promedio(
    vector: FeatureVector,
    consumo_promedio_normalizado_0_100: float,
    consumo_promedio_percentil_real: float,
) -> tuple[float, str]:
    """RN-012 fix: the CONTRIBUTION value stays `consumo_promedio_normalizado_0_100` (min-max,
    §10.1's weight formula, unchanged) -- only the TEXT now quotes `consumo_promedio_percentil_
    real` (`calcular_percentil_real_consumo_promedio_lote`, the ACTUAL percentile), never the
    min-max score, which used to be mislabeled as one (module-level function docstring)."""
    avg = _a_float(vector.features["avg_consumption"])
    return consumo_promedio_normalizado_0_100, (
        f"Consumo promedio de {avg:.1f} kWh "
        f"(percentil {consumo_promedio_percentil_real:.0f} respecto del lote)"
    )


def _factor_categoria_tarifaria(categoria_nombre: str) -> tuple[float, str]:
    criticidad = CRITICIDAD_CATEGORIA.get(categoria_nombre, CRITICIDAD_DEFAULT)
    return criticidad, f"Categoría tarifaria {categoria_nombre} (criticidad {criticidad:.0f}/100)"


def componer_ire(
    vector: FeatureVector,
    *,
    ml_score_0_100: float,
    iee_kwh: float | None,
    iee_normalizado_0_100: float,
    consumo_promedio_normalizado_0_100: float,
    consumo_promedio_percentil_real: float,
    categoria_nombre: str,
    pesos_efectivos: Mapping[str, float] = PESOS_EFECTIVOS_V1,
) -> ComposicionIRE:
    """§10.1: compose the 8-factor IRE for ONE suministro. `ml_score_0_100` (Etapa 6),
    `iee_kwh`/`iee_normalizado_0_100` (Etapa 8, THIS module's own `calcular_iee_kwh`/
    `normalizar_iee_lote`, computed BEFORE this call per the module docstring's execution-order
    note), `consumo_promedio_normalizado_0_100` (`normalizar_consumo_promedio_lote`, drives the
    factor's WEIGHT) and `consumo_promedio_percentil_real` (`calcular_percentil_real_consumo_
    promedio_lote`, drives ONLY the factor's explicability TEXT, RN-012 fix -- see that
    function's docstring) are precomputed per-lote inputs; every other factor reads directly off
    `vector.features`. `pesos_efectivos` defaults to `PESOS_EFECTIVOS_V1` (redistributed weights)
    -- exposed as a parameter only for test isolation, never expected to differ in production."""
    valores_y_razones: dict[str, tuple[float, str]] = {
        CLAVE_SCORE_IA: _factor_score_ia(ml_score_0_100),
        CLAVE_PERSISTENCIA_ANOMALIAS: _factor_persistencia_anomalias(vector),
        CLAVE_IMPACTO_ECONOMICO: _factor_impacto_economico(iee_kwh, iee_normalizado_0_100),
        CLAVE_CONSUMO_PROMEDIO: _factor_consumo_promedio(
            vector, consumo_promedio_normalizado_0_100, consumo_promedio_percentil_real
        ),
        CLAVE_CATEGORIA_TARIFARIA: _factor_categoria_tarifaria(categoria_nombre),
    }
    historial = _factor_historial_consumos(vector)
    if historial is not None:
        valores_y_razones[CLAVE_HISTORIAL_CONSUMOS] = historial
    variacion = _factor_variacion_porcentual(vector)
    if variacion is not None:
        valores_y_razones[CLAVE_VARIACION_PORCENTUAL] = variacion
    # CLAVE_INSPECCIONES_ANTERIORES: v1 always 0 (no feedback_modelo data yet) -- weight already
    # redistributed to 0.0 in `pesos_efectivos`, so it is simply never added here at all (its
    # contribution would be `0.0 * 0.0 = 0.0` either way; omitting the entry entirely is the
    # same net effect, expressed directly rather than computed-then-discarded).

    valor_crudo = 0.0
    factores: list[FactorContribucion] = []
    # Fixed, deterministic emission order (§10.1's own table order) -- not sorted by contribution
    # size: DEC-016 does not mandate a particular order, and this keeps `observaciones` stable/
    # diffable across runs of the SAME suministro.
    for clave in PESOS_IRE:
        entrada = valores_y_razones.get(clave)
        if entrada is None:
            continue
        valor_0_100, razon = entrada
        contribution = pesos_efectivos[clave] * valor_0_100
        valor_crudo += contribution
        if contribution > 0:
            factores.append(
                FactorContribucion(factor=clave, contribution=contribution, reason=razon)
            )

    ire_valor = redondear_half_up(valor_crudo)
    # Defensive clamp: mathematically unreachable (every factor in [0,100], weights sum to 1), but
    # a documented invariant guard mirrors this codebase's other never-triggered defensive checks
    # (e.g. `domain/features.py`'s `_desvio_estandar`/`domain/isolation_forest.py`'s assert).
    ire_valor = max(0, min(100, ire_valor))
    clasificacion = bandear_clasificacion(float(ire_valor))

    return ComposicionIRE(
        ire_valor=ire_valor,
        valor_crudo=valor_crudo,
        clasificacion=clasificacion,
        factores=tuple(factores),
    )


# ---------------------------------------------------------------------------------------------
# Mission directive #7 -- the `ire`/`iee` sections of the `POST /procesar` informe. Mirrors
# `domain/isolation_forest.py`'s own "counts/summary, bounded top-N detail" shape (`InformeML`).
# ---------------------------------------------------------------------------------------------

# Exact literal list of `ck_anomalias_tipo` (`docker/postgres/init/01_schema.sql`) -- pre-declared
# here (not derived dynamically) so `anomalias_persistidas_por_tipo` always reports EVERY type at
# `0` when absent this lote, mirroring `domain/reglas.py`'s `construir_informe_reglas`'s own
# pre-initialized `hits_por_regla` pattern.
TIPOS_ANOMALIA: tuple[str, ...] = (
    "Consumo Muy Bajo",
    "Consumo Muy Alto",
    "Caída Brusca",
    "Incremento Brusco",
    "Patrón Irregular",
    "Persistencia Anómala",
    "Desvío Estadístico",
)

# Mirrors `bandear_nivel_ire`'s own 5 bands, in ascending order -- pre-initialized the same way.
NIVELES_IRE: tuple[str, ...] = ("Muy Bajo", "Bajo", "Medio", "Alto", "Crítico")

_TOP_N_IRE = 10
_TOP_N_FACTORES = 3
_TOP_N_IEE = 5


@dataclass(frozen=True, slots=True)
class ResultadoSuministroParaInforme:
    """One suministro's Etapa 7-8 outcome, as needed by the informe builders below -- deliberately
    NOT the full persistence payload (`ports.py`'s `ResultadoIAParaGuardar`, application layer):
    keeps this module's own informe-building free of any persistence-shape coupling, the same
    "transient reporting projection, not the persisted row" split `domain/isolation_forest.py`'s
    `PrediccionSuministro` vs. `PrediccionParaGuardar` already establishes."""

    suministro_id: UUID
    numero_suministro: str
    composicion: ComposicionIRE
    iee_kwh: float | None
    anomalia_tipos: tuple[str, ...]


def _percentil(ordenados: Sequence[float], p: float) -> float:
    """Linear-interpolation percentile over an ALREADY-SORTED sequence -- deliberately mirrors
    `domain/features.py`'s/`domain/isolation_forest.py`'s own `_percentile`/`_percentil` (same
    formula, same tie-break convention) rather than importing either: each domain module in this
    bounded context stays self-contained (their own docstrings/`domain/isolation_forest.py`'s
    module docstring already establish this precedent for a small pure-math helper)."""
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


@dataclass(frozen=True, slots=True)
class DistribucionIRE:
    """`ire_valor`'s distribution across every suministro analyzed this lote (mission directive
    #7: "distribution (min/p50/p95/max)")."""

    minimo: int
    p50: float
    p95: float
    maximo: int


@dataclass(frozen=True, slots=True)
class TopFactorResumen:
    """One factor's entry inside `IreSuministroResumen.top_factores` -- the top-3-by-contribution
    slice of `ComposicionIRE.factores` (already DEC-016-filtered to nonzero contributions)."""

    factor: str
    contribution: float


@dataclass(frozen=True, slots=True)
class IreSuministroResumen:
    """One suministro's entry in `InformeIRE.top_10` -- `numero_suministro`, `ire`, `nivel`,
    `clasificacion` plus its top-3 contributing factors (mission directive #7)."""

    suministro_id: UUID
    numero_suministro: str
    ire: int
    nivel: str
    clasificacion: str
    top_factores: tuple[TopFactorResumen, ...]


@dataclass(frozen=True, slots=True)
class InformeIRE:
    """Etapa 7's full report for one lote -- mirrors `InformeML`'s "counts/summary, bounded top-N
    detail" shape. `distribucion` is `None` and `top_10` empty when `suministros_evaluados == 0`
    (mirrors `InformeML`'s/`ResumenFeatures`'s own all-zero-counts convention for that edge case).
    """

    lote_id: UUID
    suministros_evaluados: int
    distribucion: DistribucionIRE | None
    conteo_por_nivel: dict[str, int]
    top_10: tuple[IreSuministroResumen, ...]
    anomalias_persistidas_por_tipo: dict[str, int]


def construir_informe_ire(
    lote_id: UUID, entradas: Sequence[ResultadoSuministroParaInforme]
) -> InformeIRE:
    """Assemble Etapa 7's informe from EVERY suministro analyzed this lote (mission directive #7)
    -- `conteo_por_nivel`/`anomalias_persistidas_por_tipo` count across ALL of `entradas`; `top_10`
    is the bounded, actionable slice (10 highest `ire_valor`, each with its own top-3 factors)."""
    conteo_por_nivel: dict[str, int] = dict.fromkeys(NIVELES_IRE, 0)
    anomalias_por_tipo: dict[str, int] = dict.fromkeys(TIPOS_ANOMALIA, 0)
    for entrada in entradas:
        conteo_por_nivel[bandear_nivel_ire(entrada.composicion.ire_valor)] += 1
        for tipo in entrada.anomalia_tipos:
            anomalias_por_tipo[tipo] += 1

    if not entradas:
        return InformeIRE(
            lote_id=lote_id,
            suministros_evaluados=0,
            distribucion=None,
            conteo_por_nivel=conteo_por_nivel,
            top_10=(),
            anomalias_persistidas_por_tipo=anomalias_por_tipo,
        )

    valores_ordenados = sorted(entrada.composicion.ire_valor for entrada in entradas)
    distribucion = DistribucionIRE(
        minimo=valores_ordenados[0],
        p50=_percentil([float(v) for v in valores_ordenados], 0.5),
        p95=_percentil([float(v) for v in valores_ordenados], 0.95),
        maximo=valores_ordenados[-1],
    )

    ordenadas_desc = sorted(entradas, key=lambda e: e.composicion.ire_valor, reverse=True)
    top_10 = tuple(
        IreSuministroResumen(
            suministro_id=entrada.suministro_id,
            numero_suministro=entrada.numero_suministro,
            ire=entrada.composicion.ire_valor,
            nivel=bandear_nivel_ire(entrada.composicion.ire_valor),
            clasificacion=entrada.composicion.clasificacion,
            top_factores=tuple(
                TopFactorResumen(factor=f.factor, contribution=f.contribution)
                for f in sorted(
                    entrada.composicion.factores, key=lambda f: f.contribution, reverse=True
                )[:_TOP_N_FACTORES]
            ),
        )
        for entrada in ordenadas_desc[:_TOP_N_IRE]
    )

    return InformeIRE(
        lote_id=lote_id,
        suministros_evaluados=len(entradas),
        distribucion=distribucion,
        conteo_por_nivel=conteo_por_nivel,
        top_10=top_10,
        anomalias_persistidas_por_tipo=anomalias_por_tipo,
    )


@dataclass(frozen=True, slots=True)
class IeeSuministroResumen:
    """One suministro's entry in `InformeIEE.top_5` (mission directive #7)."""

    suministro_id: UUID
    numero_suministro: str
    iee_kwh: float


@dataclass(frozen=True, slots=True)
class InformeIEE:
    """Etapa 8's full report for one lote: `total_kwh_estimado` sums every suministro's IEE
    (`None`, cold-start, contributes `0`); `suministros_con_iee` counts only STRICTLY POSITIVE
    IEEs (mission directive #7: "suministros con IEE > 0"); `top_5` is the bounded, actionable
    slice -- suministros with `iee_kwh is None` (cold-start, no estimate) never appear in it."""

    lote_id: UUID
    total_kwh_estimado: float
    suministros_con_iee: int
    top_5: tuple[IeeSuministroResumen, ...]


def construir_informe_iee(
    lote_id: UUID, entradas: Sequence[ResultadoSuministroParaInforme]
) -> InformeIEE:
    """Assemble Etapa 8's informe from EVERY suministro analyzed this lote (mission directive
    #7)."""
    total_kwh_estimado = sum(entrada.iee_kwh for entrada in entradas if entrada.iee_kwh is not None)
    suministros_con_iee = sum(
        1 for entrada in entradas if entrada.iee_kwh is not None and entrada.iee_kwh > 0
    )
    # Pairs (entrada, iee_kwh-as-float) -- keeps the sort key/`IeeSuministroResumen` construction
    # below working with a narrowed `float`, not `float | None` (mypy cannot narrow an attribute
    # access repeated across a separate `sorted(..., key=...)` callable the way it can within a
    # single comprehension's own `if`/element clauses).
    con_iee: list[tuple[ResultadoSuministroParaInforme, float]] = [
        (entrada, entrada.iee_kwh) for entrada in entradas if entrada.iee_kwh is not None
    ]
    con_iee.sort(key=lambda par: par[1], reverse=True)
    top_5 = tuple(
        IeeSuministroResumen(
            suministro_id=entrada.suministro_id,
            numero_suministro=entrada.numero_suministro,
            iee_kwh=valor,
        )
        for entrada, valor in con_iee[:_TOP_N_IEE]
    )
    return InformeIEE(
        lote_id=lote_id,
        total_kwh_estimado=total_kwh_estimado,
        suministros_con_iee=suministros_con_iee,
        top_5=top_5,
    )
