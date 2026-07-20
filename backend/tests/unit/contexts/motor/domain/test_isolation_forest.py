"""Unit tests for Etapa 6's pure domain logic (US-011/RF-006, AI_ENGINE_SPEC.md §9) --
`domain/isolation_forest.py`. Every function here is pure (no I/O, no sklearn): the actual
model fit/score is a thin infrastructure adapter (`infrastructure/isolation_forest_scorer.py`,
its own smoke test) -- sklearn itself is not under test here (mission directive), only the
matrix-building/grouping/normalization/banding math this module owns.
"""

from datetime import date
from uuid import UUID, uuid4

import pytest

from energia.contexts.motor.domain.features import FeatureVector, construir_feature_vector
from energia.contexts.motor.domain.isolation_forest import (
    BANDA_ALTO_RIESGO_MAX,
    BANDA_ATENCION_MAX,
    BANDA_NORMAL_MAX,
    CLASIFICACION_ALTO_RIESGO,
    CLASIFICACION_ATENCION,
    CLASIFICACION_CRITICO,
    CLASIFICACION_NORMAL,
    MODELO_POR_CATEGORIA_MINIMO,
    NUMERIC_FEATURE_KEYS,
    SCOPE_GLOBAL,
    DistribucionScore,
    GrupoEntrenamiento,
    ModeloEntrenadoInfo,
    PrediccionSuministro,
    agrupar_para_entrenamiento,
    bandear_clasificacion,
    construir_informe_ml,
    construir_matriz,
    construir_nombre_modelo,
    construir_version_modelo,
    normalizar_scores_min_max_invertido,
)


def _vector(
    suministro_id: UUID | None = None,
    *,
    categoria_tarifaria_id: UUID | None = None,
    overrides: dict[str, object] | None = None,
) -> FeatureVector:
    """A minimal, hand-controlled `FeatureVector` -- direct dataclass construction (not
    `construir_feature_vector`) so every feature value is exactly what the test says, including
    deliberately null ones for the imputation tests below."""
    base_features: dict[str, object] = {
        "avg_consumption": 100.0,
        "max_consumption": 120.0,
        "min_consumption": 80.0,
        "stddev_consumption": 10.0,
        "pct_change_prev_period": 0.1,
        "pct_change_yoy": 0.2,
        "moving_avg_6m": 100.0,
        "moving_avg_12m": 100.0,
        "deviation_from_baseline": 0.5,
        "zero_consumption_streak": 0,
        "trend_slope": 1.0,
        "seasonality_index": 1.0,
        "peer_ratio": 1.0,
        "supply_age_days": 365,
        "prior_anomaly_count": 0,
        "billed_days": 30,
        "categoria_tarifaria": str(categoria_tarifaria_id or uuid4()),
        "is_cold_start": False,
        "has_conflicted_periods": False,
        "zscore_self": 0.0,
        "percentile_peer": 0.5,
        "iqr_outlier_flag": False,
    }
    if overrides:
        base_features.update(overrides)
    return FeatureVector(
        suministro_id=suministro_id or uuid4(),
        lote_id=uuid4(),
        features=base_features,
        is_cold_start=bool(base_features["is_cold_start"]),
        has_conflicted_periods=bool(base_features["has_conflicted_periods"]),
        categoria_tarifaria_id=categoria_tarifaria_id or uuid4(),
        localidad="Formosa",
        kwh_actual=100.0,
    )


# -------------------------------------------------------------------------------------------
# NUMERIC_FEATURE_KEYS -- contract test against the REAL FeatureVector shape
# -------------------------------------------------------------------------------------------


def test_numeric_feature_keys_matches_real_feature_vector_minus_categoria() -> None:
    """Anti-drift contract: build a REAL `FeatureVector` via `construir_feature_vector` (not the
    hand-rolled `_vector` helper) and diff its actual jsonb keys against `NUMERIC_FEATURE_KEYS` --
    if `domain/features.py` ever adds/renames a feature, this test catches the drift instead of
    the two modules silently disagreeing."""
    from energia.contexts.motor.domain.ports import FeatureConsumoRow

    historial = [
        FeatureConsumoRow(
            consumo_id=uuid4(),
            suministro_id=uuid4(),
            lote_id=uuid4(),
            fecha_inicio=date(2024, 1, 1),
            fecha_fin=date(2024, 1, 31),
            dias_facturados=31,
            kwh=__import__("decimal").Decimal("100.000"),
        )
    ]
    vector = construir_feature_vector(
        suministro_id=historial[0].suministro_id,
        lote_id=historial[0].lote_id,
        historial_suministro=historial,
        categoria_tarifaria_id=uuid4(),
        localidad="Formosa",
        fecha_alta=date(2020, 1, 1),
        prior_anomaly_count=0,
        tiene_periodos_conflictivos=False,
    )
    assert vector is not None
    assert set(NUMERIC_FEATURE_KEYS) == set(vector.features) - {"categoria_tarifaria"}


def test_numeric_feature_keys_is_sorted_and_has_21_entries() -> None:
    assert list(NUMERIC_FEATURE_KEYS) == sorted(NUMERIC_FEATURE_KEYS)
    assert len(NUMERIC_FEATURE_KEYS) == 21
    assert "categoria_tarifaria" not in NUMERIC_FEATURE_KEYS
    assert "is_cold_start" in NUMERIC_FEATURE_KEYS
    assert "has_conflicted_periods" in NUMERIC_FEATURE_KEYS


# -------------------------------------------------------------------------------------------
# construir_matriz -- feature matrix construction (mission directive #2)
# -------------------------------------------------------------------------------------------


def test_construir_matriz_preserves_suministro_order_and_column_order() -> None:
    s1, s2 = uuid4(), uuid4()
    matriz = construir_matriz([_vector(s1), _vector(s2)])

    assert matriz.suministro_ids == (s1, s2)
    assert matriz.feature_keys == NUMERIC_FEATURE_KEYS
    assert len(matriz.filas) == 2
    assert len(matriz.filas[0]) == len(NUMERIC_FEATURE_KEYS)


def test_construir_matriz_encodes_booleans_as_0_1() -> None:
    vector = _vector(overrides={"is_cold_start": True, "has_conflicted_periods": False})
    matriz = construir_matriz([vector])

    indice_cold_start = matriz.feature_keys.index("is_cold_start")
    indice_conflicto = matriz.feature_keys.index("has_conflicted_periods")
    assert matriz.filas[0][indice_cold_start] == 1.0
    assert matriz.filas[0][indice_conflicto] == 0.0


def test_construir_matriz_imputes_null_with_cohort_median_of_non_null_values() -> None:
    """Three vectors, `trend_slope` = 10.0, None, 30.0 -- the null must be imputed with the
    MEDIAN of the two non-null values (10.0, 30.0) = 20.0, hand-computed."""
    v1 = _vector(overrides={"trend_slope": 10.0})
    v2 = _vector(overrides={"trend_slope": None})
    v3 = _vector(overrides={"trend_slope": 30.0})
    matriz = construir_matriz([v1, v2, v3])

    indice = matriz.feature_keys.index("trend_slope")
    assert matriz.filas[0][indice] == 10.0
    assert matriz.filas[1][indice] == 20.0  # imputed
    assert matriz.filas[2][indice] == 30.0


def test_construir_matriz_all_null_column_falls_back_to_zero() -> None:
    """Every vector in this (degenerate) cohort has `supply_age_days = None` -- no signal exists
    for this feature in this cohort at all, so it contributes no variance: falls back to `0.0`
    (documented neutral default, `domain/isolation_forest.py`'s `construir_matriz` docstring)."""
    v1 = _vector(overrides={"supply_age_days": None})
    v2 = _vector(overrides={"supply_age_days": None})
    matriz = construir_matriz([v1, v2])

    indice = matriz.feature_keys.index("supply_age_days")
    assert matriz.filas[0][indice] == 0.0
    assert matriz.filas[1][indice] == 0.0


# -------------------------------------------------------------------------------------------
# agrupar_para_entrenamiento -- training strategy (DEC-010, mission directive #3)
# -------------------------------------------------------------------------------------------


def test_all_below_threshold_yields_a_single_global_group() -> None:
    categoria_id = uuid4()
    vectores = [_vector(categoria_tarifaria_id=categoria_id) for _ in range(5)]

    grupos = agrupar_para_entrenamiento(vectores, {categoria_id: "Residencial"})

    assert len(grupos) == 1
    assert grupos[0].scope == SCOPE_GLOBAL
    assert grupos[0].categoria_tarifaria_id is None
    assert len(grupos[0].vectores) == 5


def test_categoria_at_or_above_minimo_gets_its_own_group() -> None:
    categoria_grande = uuid4()
    categoria_chica = uuid4()
    vectores = [
        _vector(categoria_tarifaria_id=categoria_grande) for _ in range(MODELO_POR_CATEGORIA_MINIMO)
    ] + [_vector(categoria_tarifaria_id=categoria_chica) for _ in range(3)]

    grupos = agrupar_para_entrenamiento(
        vectores, {categoria_grande: "Industrial", categoria_chica: "Residencial"}
    )

    assert len(grupos) == 2
    por_scope = {g.scope: g for g in grupos}
    assert set(por_scope) == {"Industrial", SCOPE_GLOBAL}
    assert len(por_scope["Industrial"].vectores) == MODELO_POR_CATEGORIA_MINIMO
    assert por_scope["Industrial"].categoria_tarifaria_id == categoria_grande
    assert len(por_scope[SCOPE_GLOBAL].vectores) == 3


def test_categoria_one_below_minimo_falls_into_global() -> None:
    categoria_id = uuid4()
    vectores = [
        _vector(categoria_tarifaria_id=categoria_id) for _ in range(MODELO_POR_CATEGORIA_MINIMO - 1)
    ]

    grupos = agrupar_para_entrenamiento(vectores, {categoria_id: "Comercial"})

    assert len(grupos) == 1
    assert grupos[0].scope == SCOPE_GLOBAL


def test_groups_are_returned_in_deterministic_scope_order() -> None:
    cat_z = uuid4()
    cat_a = uuid4()
    vectores = [_vector(categoria_tarifaria_id=cat_z) for _ in range(MODELO_POR_CATEGORIA_MINIMO)]
    vectores += [_vector(categoria_tarifaria_id=cat_a) for _ in range(MODELO_POR_CATEGORIA_MINIMO)]

    grupos = agrupar_para_entrenamiento(vectores, {cat_z: "Zeta", cat_a: "Alfa"})

    assert [g.scope for g in grupos] == ["Alfa", "Zeta"]


def test_no_leftover_vectors_means_no_global_group() -> None:
    categoria_id = uuid4()
    vectores = [
        _vector(categoria_tarifaria_id=categoria_id) for _ in range(MODELO_POR_CATEGORIA_MINIMO)
    ]

    grupos = agrupar_para_entrenamiento(vectores, {categoria_id: "Industrial"})

    assert len(grupos) == 1
    assert grupos[0].scope == "Industrial"


def test_missing_categoria_nombre_falls_back_to_the_uuid_string() -> None:
    categoria_id = uuid4()
    vectores = [
        _vector(categoria_tarifaria_id=categoria_id) for _ in range(MODELO_POR_CATEGORIA_MINIMO)
    ]

    grupos = agrupar_para_entrenamiento(vectores, {})

    assert grupos[0].scope == str(categoria_id)


# -------------------------------------------------------------------------------------------
# normalizar_scores_min_max_invertido -- DEC-013
# -------------------------------------------------------------------------------------------


def test_normalizacion_min_max_invertida_hand_computed() -> None:
    # raw decision_function scores: more negative = more anomalous. min=-0.5 (most anomalous),
    # max=0.3 (least anomalous). Formula: (max - x) / (max - min).
    scores = [-0.5, -0.1, 0.3]
    normalizados = normalizar_scores_min_max_invertido(scores)

    assert normalizados[0] == pytest.approx(1.0)  # most negative -> most anomalous -> 1.0
    assert normalizados[2] == pytest.approx(0.0)  # max raw -> least anomalous -> 0.0
    assert normalizados[1] == pytest.approx((0.3 - (-0.1)) / (0.3 - (-0.5)))  # 0.5


def test_normalizacion_all_equal_scores_yields_all_point_five() -> None:
    """Degenerate case (mission directive #5): every raw score identical -- no relative
    anomalousness signal exists among them, so a NEUTRAL midpoint (0.5) is reported, rather than
    an arbitrary extreme (0.0 would falsely claim 'definitely normal', 1.0 'definitely
    anomalous')."""
    normalizados = normalizar_scores_min_max_invertido([0.2, 0.2, 0.2])
    assert normalizados == (0.5, 0.5, 0.5)


def test_normalizacion_single_score_is_also_degenerate() -> None:
    assert normalizar_scores_min_max_invertido([0.42]) == (0.5,)


def test_normalizacion_empty_returns_empty() -> None:
    assert normalizar_scores_min_max_invertido([]) == ()


# -------------------------------------------------------------------------------------------
# bandear_clasificacion -- DEC-015 bands applied to ml_score_0_100
# -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "esperado"),
    [
        (0, CLASIFICACION_NORMAL),
        (BANDA_NORMAL_MAX, CLASIFICACION_NORMAL),
        (BANDA_NORMAL_MAX + 0.01, CLASIFICACION_ATENCION),
        (BANDA_ATENCION_MAX, CLASIFICACION_ATENCION),
        (BANDA_ATENCION_MAX + 0.01, CLASIFICACION_ALTO_RIESGO),
        (BANDA_ALTO_RIESGO_MAX, CLASIFICACION_ALTO_RIESGO),
        (BANDA_ALTO_RIESGO_MAX + 0.01, CLASIFICACION_CRITICO),
        (100, CLASIFICACION_CRITICO),
    ],
)
def test_bandear_clasificacion_boundaries(score: float, esperado: str) -> None:
    assert bandear_clasificacion(score) == esperado


# -------------------------------------------------------------------------------------------
# construir_nombre_modelo / construir_version_modelo -- persistence naming (mission directive #6)
# -------------------------------------------------------------------------------------------


def test_construir_nombre_modelo() -> None:
    assert construir_nombre_modelo("global") == "isolation-forest-global"
    assert construir_nombre_modelo("Residencial") == "isolation-forest-Residencial"


def test_construir_version_modelo_is_deterministic_and_bounded() -> None:
    codigo_largo = "LOTE-" + "X" * 45  # lotes.codigo_lote's own varchar(50) max width
    version_1 = construir_version_modelo(codigo_largo)
    version_2 = construir_version_modelo(codigo_largo)

    assert version_1 == version_2  # deterministic: same input -> same output
    assert len(version_1) <= 30  # modelos_ia.version varchar(30) -- hard Postgres limit
    assert version_1.startswith("v1-")


def test_construir_version_modelo_differs_for_different_lotes() -> None:
    assert construir_version_modelo("LOTE-A") != construir_version_modelo("LOTE-B")


# -------------------------------------------------------------------------------------------
# construir_informe_ml -- the response's `ml` section aggregation
# -------------------------------------------------------------------------------------------


def _prediccion(score_0_100: float, *, numero: str | None = None) -> PrediccionSuministro:
    return PrediccionSuministro(
        id=uuid4(),
        suministro_id=uuid4(),
        numero_suministro=numero or f"SUM-{score_0_100}",
        modelo_ia_id=uuid4(),
        scope=SCOPE_GLOBAL,
        score_crudo=-score_0_100 / 100,
        score_normalizado=score_0_100 / 100,
        ml_score_0_100=score_0_100,
        clasificacion=bandear_clasificacion(score_0_100),
    )


def test_construir_informe_ml_empty_predicciones() -> None:
    informe = construir_informe_ml(uuid4(), modelos=(), predicciones=())

    assert informe.suministros_scored == 0
    assert informe.distribucion is None
    assert informe.top_10 == ()


def test_construir_informe_ml_distribution_hand_computed() -> None:
    """4 scores: 0, 20, 80, 100 -- p50 (linear interpolation, same convention as
    `domain/features.py`'s `_percentile`) over [0, 20, 80, 100] at p=0.5: index = 0.5*3 = 1.5,
    interpolates between position 1 (20) and 2 (80) at weight 0.5 -> 50.0. p95: index = 0.95*3 =
    2.85, interpolates between position 2 (80) and 3 (100) at weight 0.85 -> 80 + 20*0.85 = 97.0.
    """
    predicciones = [_prediccion(0), _prediccion(20), _prediccion(80), _prediccion(100)]
    lote_id = uuid4()
    informe = construir_informe_ml(lote_id, modelos=(), predicciones=predicciones)

    assert informe.lote_id == lote_id
    assert informe.suministros_scored == 4
    assert informe.distribucion == DistribucionScore(minimo=0, p50=50.0, p95=97.0, maximo=100)


def test_construir_informe_ml_top_10_sorted_desc_and_capped() -> None:
    predicciones = [_prediccion(float(i)) for i in range(15)]
    informe = construir_informe_ml(uuid4(), modelos=(), predicciones=predicciones)

    assert len(informe.top_10) == 10
    assert [p.ml_score_0_100 for p in informe.top_10] == list(range(14, 4, -1))


def test_construir_informe_ml_passes_through_modelos() -> None:
    modelo = ModeloEntrenadoInfo(
        scope=SCOPE_GLOBAL, modelo_ia_id=uuid4(), version="v1-abc", suministros_entrenados=3
    )
    informe = construir_informe_ml(uuid4(), modelos=(modelo,), predicciones=(_prediccion(50),))
    assert informe.modelos == (modelo,)


def test_grupo_entrenamiento_is_importable_dataclass() -> None:
    """Smoke test: `GrupoEntrenamiento` is constructible directly (used by
    `agrupar_para_entrenamiento`'s own tests above, and by `application/procesar_lote.py`)."""
    grupo = GrupoEntrenamiento(scope=SCOPE_GLOBAL, categoria_tarifaria_id=None, vectores=())
    assert grupo.scope == SCOPE_GLOBAL
