"""Etapa 6 -- Isolation Forest (US-011/RF-006, AI_ENGINE_SPEC.md §9): the ML branch's PURE logic
-- feature matrix construction, training-strategy grouping (DEC-010), score normalization
(DEC-013) and classification banding (DEC-015). The actual model fit/score (sklearn,
`RobustScaler` + `IsolationForest`) is NOT here: it is a thin infrastructure adapter
(`infrastructure/isolation_forest_scorer.py`) this module's callers invoke through a port
(`domain/ports.py`'s `IsolationForestScorer`) -- sklearn itself is not unit-tested here (mission
directive: "sklearn itself is not under test, fixed random_state makes end-to-end
deterministic"), only the matrix/grouping/normalization/banding math this module owns.

**Feature matrix (mission directive #2).** `construir_matriz` takes the SAME stage-3 in-memory
`FeatureVector`s Etapa 5 (`domain/reglas.py`) already reuses -- never recomputed -- and builds a
numeric-only matrix: every key of `FeatureVector.features` EXCEPT `categoria_tarifaria` (a
string; excluded because the per-categoria training strategy below already makes it redundant as
a MODEL INPUT -- a categoria-scoped model never needs to learn its own categoria as a feature,
and the global model's categoria-mix is exactly the population DEC-010 chose NOT to split
further, so encoding it back in as a feature would just re-introduce the split the training
strategy already decided against). `is_cold_start`/`has_conflicted_periods`/`iqr_outlier_flag`
(booleans in the jsonb) are encoded `True/False -> 1.0/0.0`; every other nullable numeric feature
(`stddev_consumption`, `pct_change_prev_period`, `trend_slope`, `supply_age_days`,
`percentile_peer`, `peer_ratio`, ...) has its `None`s imputed with the COHORT MEDIAN of that
column WITHIN THE GROUP BEING BUILT (AI_ENGINE_SPEC.md §6.2's scoring-time imputation policy;
"cohort" here is the training population of THAT model -- see `agrupar_para_entrenamiento`
below, NOT the whole lote when per-categoria models exist alongside the global one). Column
order is `NUMERIC_FEATURE_KEYS`, a SORTED tuple -- deterministic regardless of dict insertion
order, so two runs over the same population always produce byte-identical matrices (feeds
`RANDOM_STATE`-reproducible fitting, RD-028).

**Training strategy (DEC-010, mission directive #3).** `agrupar_para_entrenamiento` partitions
the lote's analyzed suministros by `categoria_tarifaria_id`: a categoria with >=
`MODELO_POR_CATEGORIA_MINIMO` (1.000) suministros IN THIS LOTE gets its OWN group (`scope` = the
categoria's `nombre`, RD-009 cohort comparability by construction); every suministro whose
categoria falls short of that threshold is pooled into exactly ONE `"global"` group (`scope =
SCOPE_GLOBAL`) -- never one group per small categoria, which would defeat the whole point of
requiring a minimum training population per model. Groups are returned in deterministic `scope`
order (never dict/insertion order) so persistence and reporting are reproducible across runs.

**Score normalization (DEC-013, mission directive #5).** `IsolationForest.decision_function`
returns higher = LESS anomalous, lower (more negative) = MORE anomalous.
`normalizar_scores_min_max_invertido` maps a LOTE-WIDE collection of raw scores (across EVERY
scope group scored this run -- DEC-013 says "por lote", not "per scope", precisely so the IRE
stays comparable across suministros scored by different models within the SAME lote) to `[0,1]`
via INVERTED min-max: `(max - x) / (max - min)` -- the raw maximum (least anomalous) maps to
`0.0`, the raw minimum (most anomalous) maps to `1.0`. **Degenerate case, decided and documented
here (mission directive #5):** when every raw score in the lote is IDENTICAL (`max == min`), the
formula divides by zero -- this implementation returns `0.5` for every suministro instead. `0.5`
is the deliberate choice over `0.0`/`1.0`: with no relative differentiation available among the
scores, reporting `0.0` would falsely claim "definitely normal" and `1.0` "definitely anomalous"
-- `0.5` (feeding `ml_score_0_100 = 50`, banded "Alto Riesgo" by `bandear_clasificacion`, actually
sitting just past the "Atención" boundary) honestly represents "no signal to differentiate this
population" rather than manufacturing a false extreme.

**Banding (DEC-015, mission directive #5) -- the ML branch's OWN preliminary verdict, distinct
from `resultados_ia.clasificacion`.** `bandear_clasificacion` applies DEC-015's bands (0-20
Normal / 21-40 Atención / 41-70 Alto Riesgo / 71-100 Crítico) directly to `ml_score_0_100` (the
normalized-then-scaled ML score alone). This is NOT the same `clasificacion` Etapa 7 will persist
on `resultados_ia` -- that one derives from the composed IRE (§10, all 8 factors, ML being only
one of them at weight 0.30). `predicciones.clasificacion` (this stage) is the ML branch's own,
narrower, preliminary read -- see AI_ENGINE_SPEC.md §9.4 for this distinction spelled out for the
schema/API consumer.

**Persistence naming (mission directive #6).** `construir_nombre_modelo`/
`construir_version_modelo` build `modelos_ia.nombre`/`.version` deterministically.
`construir_version_modelo` deliberately does NOT embed `codigo_lote` verbatim (unlike the
mission's illustrative `'v1-{codigo_lote}[-{scope}]'` pattern): `lotes.codigo_lote` is
`varchar(50)` (`docker/postgres/init/01_schema.sql`) while `modelos_ia.version` is `varchar(30)`
-- a literal `f"v1-{codigo_lote}-{scope}"` concatenation overflows that hard Postgres limit for
any codigo_lote longer than a few characters (this repo's OWN synthetic dataset's codigo_lote,
`LOTE-SYN-S{seed}-{year}-{month}`, already consumes 20 of those 30 characters before any scope
suffix is even added). `nombre` already fully encodes `scope` (`isolation-forest-{scope}`), so
`version` only needs to disambiguate this lote's fit from every OTHER lote's fit of the SAME
`nombre` (`UNIQUE (nombre, version)`) -- it does not need to re-encode scope at all. The
implementation hashes `codigo_lote` (SHA-256, first 8 hex chars) for guaranteed uniqueness/
boundedness regardless of input length, prefixed by a human-readable, truncated hint of
`codigo_lote` itself (first 16 chars) so a `modelos_ia` row is not fully opaque to a human
skimming the table -- deterministic (same `codigo_lote` -> same `version`, satisfying "retry the
same lote -> same version" idempotent-reprocess semantics, `infrastructure/
modelos_ia_repository.py`), but never dependent on the actual LENGTH of `codigo_lote`.
"""

import hashlib
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from energia.contexts.motor.domain.features import FeatureVector

# DEC-010 (AI_ENGINE_SPEC.md §9.1/§15): minimum analyzed-suministros-in-this-lote count for a
# categoria tarifaria to get its OWN model; short of this, its suministros fall into the pooled
# "global" group instead.
MODELO_POR_CATEGORIA_MINIMO = 1000

# DEC-011 (§9.2/§15): expected anomaly rate.
IF_CONTAMINATION = 0.03
# DEC-012 (§9.2/§15): forest size / per-tree subsample size (the technique's own default).
IF_N_ESTIMATORS = 200
IF_MAX_SAMPLES = 256
# RD-028 (reproducibility): fixed seed, never left to sklearn's own default (which would be
# nondeterministic across runs/processes).
RANDOM_STATE = 42

# Exact literal of `ck_modelos_ia_algoritmo` (docker/postgres/init/01_schema.sql).
ALGORITMO_ISOLATION_FOREST = "Isolation Forest"

NOMBRE_MODELO_PREFIJO = "isolation-forest"
SCOPE_GLOBAL = "global"
VERSION_MODELO_PREFIJO = "v1"
# modelos_ia.version varchar(30) -- hard Postgres limit (docker/postgres/init/01_schema.sql),
# not just a display convention: exceeding it raises at INSERT time. See module docstring,
# "Persistence naming".
_VERSION_MAX_LEN = 30
_VERSION_HINT_LEN = 16
_VERSION_DIGEST_LEN = 8

# DEC-015 (§10.2/§15): IRE-band-shaped thresholds applied directly to `ml_score_0_100` (module
# docstring, "Banding") -- the ML branch's OWN preliminary verdict, distinct from
# `resultados_ia.clasificacion` (derived from the composed IRE at Etapa 7).
BANDA_NORMAL_MAX = 20
BANDA_ATENCION_MAX = 40
BANDA_ALTO_RIESGO_MAX = 70
CLASIFICACION_NORMAL = "Normal"
CLASIFICACION_ATENCION = "Atención"
CLASIFICACION_ALTO_RIESGO = "Alto Riesgo"
CLASIFICACION_CRITICO = "Crítico"

# Every `FeatureVector.features` key EXCEPT `categoria_tarifaria` (a string -- see module
# docstring for why it is excluded as a MODEL INPUT, not merely "not numeric"). Built via
# `sorted()` over an explicit literal (not derived dynamically from a sample dict) so the "stable
# sorted order" contract (mission directive #2) is self-evident at the definition site;
# `test_isolation_forest.py`'s `test_numeric_feature_keys_matches_real_feature_vector_minus_
# categoria` is the anti-drift guard against `domain/features.py` changing shape independently.
NUMERIC_FEATURE_KEYS: tuple[str, ...] = tuple(
    sorted(
        [
            "avg_consumption",
            "max_consumption",
            "min_consumption",
            "stddev_consumption",
            "pct_change_prev_period",
            "pct_change_yoy",
            "moving_avg_6m",
            "moving_avg_12m",
            "deviation_from_baseline",
            "zero_consumption_streak",
            "trend_slope",
            "seasonality_index",
            "peer_ratio",
            "supply_age_days",
            "prior_anomaly_count",
            "billed_days",
            "is_cold_start",
            "has_conflicted_periods",
            "zscore_self",
            "percentile_peer",
            "iqr_outlier_flag",
        ]
    )
)


def _percentil(ordenados: list[float], p: float) -> float:
    """Linear-interpolation percentile over an ALREADY-SORTED list -- deliberately mirrors
    `domain/features.py`'s own `_percentile` (same formula, same tie-break convention) rather
    than importing it: each domain module in this bounded context stays self-contained rather
    than sharing private helpers across files (the same discipline `infrastructure/
    duplicidades_data_source.py`'s repeated-CTE docstring documents for SQL, applied here to a
    small pure-math helper instead). Used for the cohort MEDIAN (`p=0.5`, `construir_matriz`'s
    imputation) and the informe's score distribution (`p50`/`p95`, `construir_informe_ml`)."""
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


def _valor_numerico(valor: object) -> float | None:
    """Encode one raw `features[key]` value for the matrix: booleans -> `1.0`/`0.0` (mission
    directive #2 -- `is_cold_start`/`has_conflicted_periods`, and `iqr_outlier_flag` for the same
    reason, all boolean-valued keys in the jsonb), `int`/`float` -> `float`, `None` -> `None`
    (imputed by the caller). `bool` is checked BEFORE `int` because `bool` is an `int` subclass
    in Python -- `isinstance(True, int)` is `True`, which would silently encode a boolean as
    `1`/`0` via the int branch anyway (same numeric result), but checking `bool` first keeps the
    encoding decision explicit rather than accidental."""
    if isinstance(valor, bool):
        return 1.0 if valor else 0.0
    if isinstance(valor, int | float):
        return float(valor)
    return None


@dataclass(frozen=True, slots=True)
class MatrizEntrenamiento:
    """One training group's numeric feature matrix -- `filas[i]` is `suministro_ids[i]`'s row,
    column `j` is `feature_keys[j]` (always `NUMERIC_FEATURE_KEYS`, kept as an explicit field
    rather than assumed by callers, for self-description)."""

    suministro_ids: tuple[UUID, ...]
    filas: tuple[tuple[float, ...], ...]
    feature_keys: tuple[str, ...] = NUMERIC_FEATURE_KEYS


def construir_matriz(vectores: Sequence[FeatureVector]) -> MatrizEntrenamiento:
    """Build `vectores`' numeric feature matrix (module docstring, "Feature matrix"). `vectores`
    IS the cohort/training population for imputation purposes -- callers pass exactly the group
    (`GrupoEntrenamiento.vectores`) whose median should fill its own nulls, never the whole
    lote's population when per-categoria groups exist alongside the global one (AI_ENGINE_SPEC.md
    §6.2: "cohort = the training population of that model"). A column with ZERO non-null values
    across the ENTIRE given cohort (every suministro null on that one feature -- a degenerate
    edge case) falls back to `0.0`: there is no signal at all for that feature in this cohort, so
    it contributes no variance to the scaled matrix either way, and `0.0` is a documented neutral
    default rather than an arbitrary guess.
    """
    suministro_ids = tuple(vector.suministro_id for vector in vectores)
    columnas: dict[str, list[float | None]] = {
        key: [_valor_numerico(vector.features.get(key)) for vector in vectores]
        for key in NUMERIC_FEATURE_KEYS
    }
    medianas: dict[str, float] = {}
    for key, columna in columnas.items():
        no_nulos = [valor for valor in columna if valor is not None]
        medianas[key] = _percentil(sorted(no_nulos), 0.5) if no_nulos else 0.0

    filas = tuple(
        tuple(_resolver_valor(columnas[key][indice], medianas[key]) for key in NUMERIC_FEATURE_KEYS)
        for indice in range(len(vectores))
    )
    return MatrizEntrenamiento(suministro_ids=suministro_ids, filas=filas)


def _resolver_valor(valor: float | None, mediana: float) -> float:
    """`valor` if present, else the column's `mediana` -- a tiny named helper (rather than an
    inline ternary at the call site) purely so mypy can narrow `valor`'s type through the `is not
    None` check; a ternary re-indexing a dict/list inline does not narrow reliably inside a
    generator expression."""
    return valor if valor is not None else mediana


@dataclass(frozen=True, slots=True)
class GrupoEntrenamiento:
    """One training group (module docstring, "Training strategy"): either a single categoria
    tarifaria (`categoria_tarifaria_id` set, `scope` = its `nombre`) at or above
    `MODELO_POR_CATEGORIA_MINIMO`, or the pooled fallback (`categoria_tarifaria_id is None`,
    `scope == SCOPE_GLOBAL`) for every suministro whose own categoria fell short."""

    scope: str
    categoria_tarifaria_id: UUID | None
    vectores: tuple[FeatureVector, ...]


def agrupar_para_entrenamiento(
    vectores: Sequence[FeatureVector],
    categoria_nombres: Mapping[UUID, str],
    *,
    minimo_categoria: int = MODELO_POR_CATEGORIA_MINIMO,
) -> list[GrupoEntrenamiento]:
    """Partition `vectores` per DEC-010 (module docstring). `categoria_nombres` maps
    `categoria_tarifaria_id -> nombre` (the human-readable label `modelos_ia.nombre` needs --
    `FeatureVector` itself only carries the UUID, `domain/features.py`'s F17 `categoria_tarifaria`
    is the UUID string, not the name); a categoria missing from this mapping (should not happen
    in practice -- `categorias_tarifarias` is a small seeded catalog, `contexts/README.md`) falls
    back to `str(categoria_tarifaria_id)` rather than raising, so a data gap degrades to a less
    readable but still-unique `scope` instead of aborting Etapa 6 entirely.

    Returns groups sorted by `scope` (never insertion/dict order) for deterministic downstream
    persistence/reporting. Omits the `SCOPE_GLOBAL` group entirely when every suministro landed
    in its own qualifying categoria group (nothing left to pool)."""
    por_categoria: dict[UUID, list[FeatureVector]] = defaultdict(list)
    for vector in vectores:
        por_categoria[vector.categoria_tarifaria_id].append(vector)

    grupos: list[GrupoEntrenamiento] = []
    vectores_global: list[FeatureVector] = []
    for categoria_id, vecs in por_categoria.items():
        if len(vecs) >= minimo_categoria:
            nombre = categoria_nombres.get(categoria_id, str(categoria_id))
            grupos.append(
                GrupoEntrenamiento(
                    scope=nombre, categoria_tarifaria_id=categoria_id, vectores=tuple(vecs)
                )
            )
        else:
            vectores_global.extend(vecs)

    if vectores_global:
        grupos.append(
            GrupoEntrenamiento(
                scope=SCOPE_GLOBAL, categoria_tarifaria_id=None, vectores=tuple(vectores_global)
            )
        )

    return sorted(grupos, key=lambda grupo: grupo.scope)


def normalizar_scores_min_max_invertido(scores_crudos: Sequence[float]) -> tuple[float, ...]:
    """DEC-013 (module docstring, "Score normalization"): per-lote inverted min-max, `[0,1]`.
    Empty input returns empty output (no suministro was scored this run -- an empty lote after
    Etapa 1's exclusions, mirroring `resumir_features`'s all-zero-counts behavior for the same
    edge case elsewhere in this bounded context)."""
    if not scores_crudos:
        return ()
    minimo = min(scores_crudos)
    maximo = max(scores_crudos)
    if maximo == minimo:
        return tuple(0.5 for _ in scores_crudos)
    return tuple((maximo - score) / (maximo - minimo) for score in scores_crudos)


def bandear_clasificacion(score_0_100: float) -> str:
    """DEC-015 applied to `ml_score_0_100` (module docstring, "Banding") -- inclusive upper
    bounds at each threshold (`<= 20` Normal, `<= 40` Atención, `<= 70` Alto Riesgo, else
    Crítico), matching AI_ENGINE_SPEC.md §10.2's own inclusive-band convention for `ire.nivel`."""
    if score_0_100 <= BANDA_NORMAL_MAX:
        return CLASIFICACION_NORMAL
    if score_0_100 <= BANDA_ATENCION_MAX:
        return CLASIFICACION_ATENCION
    if score_0_100 <= BANDA_ALTO_RIESGO_MAX:
        return CLASIFICACION_ALTO_RIESGO
    return CLASIFICACION_CRITICO


def construir_nombre_modelo(scope: str) -> str:
    """`modelos_ia.nombre` for one scope (module docstring, "Persistence naming") --
    `isolation-forest-{scope}`, e.g. `isolation-forest-global`/`isolation-forest-Residencial`.
    `categorias_tarifarias.nombre` is `varchar(50)` (`docker/postgres/init/01_schema.sql`); even
    at that maximum width, `"isolation-forest-" + 50 chars` (67) stays comfortably under
    `modelos_ia.nombre`'s own `varchar(100)` -- no truncation/hashing needed here, unlike
    `construir_version_modelo` below."""
    return f"{NOMBRE_MODELO_PREFIJO}-{scope}"


def construir_version_modelo(codigo_lote: str) -> str:
    """`modelos_ia.version` for one lote's fit (module docstring, "Persistence naming") --
    deterministic (same `codigo_lote` always yields the same `version`, required for idempotent
    Error-retry reprocess, `infrastructure/modelos_ia_repository.py`) and BOUNDED to `varchar(30)`
    regardless of `codigo_lote`'s own length (up to `lotes.codigo_lote`'s `varchar(50)`)."""
    digest = hashlib.sha256(codigo_lote.encode("utf-8")).hexdigest()[:_VERSION_DIGEST_LEN]
    pista_legible = codigo_lote[:_VERSION_HINT_LEN]
    version = f"{VERSION_MODELO_PREFIJO}-{pista_legible}-{digest}"
    assert len(version) <= _VERSION_MAX_LEN, (  # pragma: no cover — guaranteed by construction
        f"version {version!r} ({len(version)} chars) exceeds modelos_ia.version's varchar(30)"
    )
    return version


@dataclass(frozen=True, slots=True)
class ModeloEntrenadoInfo:
    """One scope's fit outcome this run -- surfaced in the informe's `ml.modelos` (mission
    directive #7) and used by the application layer to map each `PrediccionSuministro` to its
    `modelo_ia_id`."""

    scope: str
    modelo_ia_id: UUID
    version: str
    suministros_entrenados: int


@dataclass(frozen=True, slots=True)
class PrediccionSuministro:
    """One suministro's Etapa 6 outcome -- the FULL per-suministro detail (mirrors
    `domain/reglas.py`'s `ReglasSuministro`/`domain/features.py`'s `FeatureVector` role: computed
    once, reused both for persistence -- `infrastructure/predicciones_repository.py` -- and for
    `construir_informe_ml`'s bounded summary, never recomputed twice). `score_crudo` is the RAW
    `decision_function` value (kept for a FUTURE Etapa 7's `resultados_ia.score_anomalia`, §9.3 --
    not persisted by THIS stage, `resultados_ia` stays untouched here); `score_normalizado` is the
    `[0,1]` DEC-013 value persisted verbatim to `predicciones.score`; `ml_score_0_100` is
    `score_normalizado * 100`, the informe/display value; `clasificacion` is DEC-015's banding of
    `ml_score_0_100` -- persisted to `predicciones.clasificacion` (module docstring, "Banding",
    for why this is NOT `resultados_ia.clasificacion`)."""

    suministro_id: UUID
    numero_suministro: str
    modelo_ia_id: UUID
    scope: str
    score_crudo: float
    score_normalizado: float
    ml_score_0_100: float
    clasificacion: str


@dataclass(frozen=True, slots=True)
class DistribucionScore:
    """`ml_score_0_100`'s distribution across every suministro scored this lote (mission
    directive #7: "score distribution (min/p50/p95/max of 0-100)")."""

    minimo: float
    p50: float
    p95: float
    maximo: float


@dataclass(frozen=True, slots=True)
class InformeML:
    """Etapa 6's full report for one lote -- mirrors `InformeReglas`/`ResumenFeatures`'s
    "counts/summary, bounded detail" shape: `top_10` is the ONLY per-suministro detail exposed
    (mission directive #7), never the full `suministros_scored`-sized list (response-size
    discipline, same reasoning `ResumenFeatures`'s module docstring documents)."""

    lote_id: UUID
    modelos: tuple[ModeloEntrenadoInfo, ...]
    suministros_scored: int
    distribucion: DistribucionScore | None
    top_10: tuple[PrediccionSuministro, ...]


_TOP_N = 10


def construir_informe_ml(
    lote_id: UUID,
    modelos: Sequence[ModeloEntrenadoInfo],
    predicciones: Sequence[PrediccionSuministro],
) -> InformeML:
    """Assemble Etapa 6's informe from the FULL `predicciones` list (every non-excluded
    suministro scored this run, across every scope group) -- `distribucion` is `None` and
    `top_10` is empty when `predicciones` is empty (a lote with zero non-excluded suministros
    receiving a `FeatureVector`, mirroring `ResumenFeatures`'s all-zero-counts behavior for the
    same edge case)."""
    if not predicciones:
        return InformeML(
            lote_id=lote_id,
            modelos=tuple(modelos),
            suministros_scored=0,
            distribucion=None,
            top_10=(),
        )

    scores_ordenados = sorted(prediccion.ml_score_0_100 for prediccion in predicciones)
    distribucion = DistribucionScore(
        minimo=scores_ordenados[0],
        p50=_percentil(scores_ordenados, 0.5),
        p95=_percentil(scores_ordenados, 0.95),
        maximo=scores_ordenados[-1],
    )
    ordenadas_desc = sorted(predicciones, key=lambda p: p.ml_score_0_100, reverse=True)
    return InformeML(
        lote_id=lote_id,
        modelos=tuple(modelos),
        suministros_scored=len(predicciones),
        distribucion=distribucion,
        top_10=tuple(ordenadas_desc[:_TOP_N]),
    )
