"""Unit tests for Etapa 7 (IRE composition, AI_ENGINE_SPEC.md §10) + Etapa 8 (IEE, §11) --
`domain/ire.py`. Every function here is pure (no I/O) -- hand-computed expectations throughout,
per the project's strict-TDD contract for IRE/IEE math.
"""

from uuid import uuid4

import pytest

from energia.contexts.motor.domain.ire import (
    CRITICIDAD_CATEGORIA,
    CRITICIDAD_DEFAULT,
    PESOS_EFECTIVOS_V1,
    PESOS_IRE,
    UMBRAL_ANOMALIA_ML,
    ComposicionIRE,
    FactorContribucion,
    ResultadoSuministroParaInforme,
    bandear_nivel_ire,
    calcular_iee_kwh,
    calcular_percentil_real_consumo_promedio_lote,
    componer_ire,
    construir_informe_iee,
    construir_informe_ire,
    debe_generar_anomalia_ml,
    normalizar_consumo_promedio_lote,
    normalizar_iee_lote,
    redistribuir_pesos,
    redondear_half_up,
)
from energia.contexts.motor.domain.isolation_forest import CLASIFICACION_CRITICO


def _vector(overrides: dict[str, object] | None = None) -> object:
    """A minimal object exposing `.features`/`.is_cold_start`, mirroring `FeatureVector` just
    enough for these pure functions -- avoids importing `construir_feature_vector` machinery for
    tests that only care about a handful of keys."""
    from energia.contexts.motor.domain.features import FeatureVector

    base: dict[str, object] = {
        "avg_consumption": 100.0,
        "moving_avg_12m": 100.0,
        "moving_avg_6m": 100.0,
        "deviation_from_baseline": None,
        "zero_consumption_streak": 0,
        "prior_anomaly_count": 0,
        "pct_change_prev_period": None,
        "pct_change_yoy": None,
    }
    if overrides:
        base.update(overrides)
    return FeatureVector(
        suministro_id=uuid4(),
        lote_id=uuid4(),
        features=base,
        is_cold_start=bool(base.get("is_cold_start", False)),
        has_conflicted_periods=False,
        categoria_tarifaria_id=uuid4(),
        localidad="Formosa",
        kwh_actual=100.0,
    )


# -------------------------------------------------------------------------------------------
# redistribuir_pesos -- v1 weight redistribution (mission directive #3, §10.1's explicit note)
# -------------------------------------------------------------------------------------------


def test_pesos_ire_sums_to_one() -> None:
    assert sum(PESOS_IRE.values()) == pytest.approx(1.0)


def test_redistribuir_pesos_hand_computed() -> None:
    pesos = {"a": 0.5, "b": 0.3, "c": 0.2}
    efectivos = redistribuir_pesos(pesos, frozenset({"c"}))

    # remaining mass = 1 - 0.2 = 0.8; a -> 0.5/0.8, b -> 0.3/0.8, c -> 0
    assert efectivos["a"] == pytest.approx(0.625)
    assert efectivos["b"] == pytest.approx(0.375)
    assert efectivos["c"] == 0.0
    assert sum(efectivos.values()) == pytest.approx(1.0)


def test_pesos_efectivos_v1_redistributes_inspecciones_anteriores() -> None:
    """v1: 'inspecciones anteriores' (0.08) is always 0 -- its weight is redistributed
    PROPORTIONALLY among the other 7 (§10.1's explicit note), not dropped."""
    assert PESOS_EFECTIVOS_V1["inspecciones_anteriores"] == 0.0
    assert PESOS_EFECTIVOS_V1["score_ia"] == pytest.approx(0.30 / 0.92)
    assert PESOS_EFECTIVOS_V1["historial_consumos"] == pytest.approx(0.15 / 0.92)
    assert PESOS_EFECTIVOS_V1["persistencia_anomalias"] == pytest.approx(0.15 / 0.92)
    assert PESOS_EFECTIVOS_V1["variacion_porcentual"] == pytest.approx(0.15 / 0.92)
    assert PESOS_EFECTIVOS_V1["impacto_economico"] == pytest.approx(0.10 / 0.92)
    assert PESOS_EFECTIVOS_V1["consumo_promedio"] == pytest.approx(0.04 / 0.92)
    assert PESOS_EFECTIVOS_V1["categoria_tarifaria"] == pytest.approx(0.03 / 0.92)
    assert sum(PESOS_EFECTIVOS_V1.values()) == pytest.approx(1.0)


# -------------------------------------------------------------------------------------------
# calcular_iee_kwh -- §11.1: max(0, kwh_esperado - kwh_facturado)
# -------------------------------------------------------------------------------------------


def test_iee_cold_start_yields_no_iee() -> None:
    vector = _vector({"moving_avg_12m": 500.0})
    # Force is_cold_start True via the real dataclass field, not just the features dict.
    from dataclasses import replace

    vector = replace(vector, is_cold_start=True)
    assert calcular_iee_kwh(vector) is None


def test_iee_positive_deviation_hand_computed() -> None:
    vector = _vector({"moving_avg_12m": 120.0})
    vector.features["is_cold_start"] = False
    assert calcular_iee_kwh(vector) == pytest.approx(20.0)  # max(0, 120-100)


def test_iee_negative_deviation_clips_to_zero() -> None:
    vector = _vector({"moving_avg_12m": 80.0})
    assert calcular_iee_kwh(vector) == pytest.approx(0.0)  # max(0, 80-100) = -20 -> 0


def test_iee_falls_back_to_moving_avg_6m_when_12m_is_null() -> None:
    vector = _vector({"moving_avg_12m": None, "moving_avg_6m": 150.0})
    assert calcular_iee_kwh(vector) == pytest.approx(50.0)  # max(0, 150-100)


def test_iee_both_baselines_null_yields_no_iee() -> None:
    vector = _vector({"moving_avg_12m": None, "moving_avg_6m": None})
    assert calcular_iee_kwh(vector) is None


# -------------------------------------------------------------------------------------------
# normalizar_iee_lote -- min-max over nonzero IEEs within the lote
# -------------------------------------------------------------------------------------------


def test_normalizar_iee_lote_hand_computed() -> None:
    a, b, c, d = uuid4(), uuid4(), uuid4(), uuid4()
    iees = {a: 0.0, b: 50.0, c: 150.0, d: None}

    normalizados = normalizar_iee_lote(iees)

    assert normalizados[a] == 0.0
    assert normalizados[b] == pytest.approx(0.0)  # the nonzero minimum still maps to 0
    assert normalizados[c] == pytest.approx(100.0)
    assert normalizados[d] == 0.0


def test_normalizar_iee_lote_all_zero_or_none_yields_all_zero() -> None:
    a, b = uuid4(), uuid4()
    normalizados = normalizar_iee_lote({a: 0.0, b: None})
    assert normalizados == {a: 0.0, b: 0.0}


def test_normalizar_iee_lote_tied_nonzero_values_map_to_100() -> None:
    """Degenerate case: every nonzero IEE identical -- no basis to differentiate among them, but
    they ARE distinguishable from the zero-IEE group, so they map to the top of the scale (100),
    not the ML branch's neutral-midpoint convention (documented divergence, `normalizar_iee_lote`
    docstring)."""
    a, b, c = uuid4(), uuid4(), uuid4()
    normalizados = normalizar_iee_lote({a: 0.0, b: 40.0, c: 40.0})
    assert normalizados[a] == 0.0
    assert normalizados[b] == pytest.approx(100.0)
    assert normalizados[c] == pytest.approx(100.0)


def test_normalizar_iee_lote_empty_returns_empty() -> None:
    assert normalizar_iee_lote({}) == {}


# -------------------------------------------------------------------------------------------
# normalizar_consumo_promedio_lote -- per-lote min-max of F1 (avg_consumption)
# -------------------------------------------------------------------------------------------


def test_normalizar_consumo_promedio_lote_hand_computed() -> None:
    v1 = _vector({"avg_consumption": 100.0})
    v2 = _vector({"avg_consumption": 150.0})
    v3 = _vector({"avg_consumption": 200.0})

    normalizados = normalizar_consumo_promedio_lote([v1, v2, v3])

    assert normalizados[v1.suministro_id] == pytest.approx(0.0)
    assert normalizados[v2.suministro_id] == pytest.approx(50.0)
    assert normalizados[v3.suministro_id] == pytest.approx(100.0)


def test_normalizar_consumo_promedio_lote_degenerate_all_equal_yields_neutral_50() -> None:
    v1 = _vector({"avg_consumption": 100.0})
    v2 = _vector({"avg_consumption": 100.0})
    normalizados = normalizar_consumo_promedio_lote([v1, v2])
    assert normalizados[v1.suministro_id] == pytest.approx(50.0)
    assert normalizados[v2.suministro_id] == pytest.approx(50.0)


def test_normalizar_consumo_promedio_lote_empty_returns_empty() -> None:
    assert normalizar_consumo_promedio_lote([]) == {}


# -------------------------------------------------------------------------------------------
# calcular_percentil_real_consumo_promedio_lote -- RN-012 fix (FIX 1): the TRUE percentile, a
# DIFFERENT scale from `normalizar_consumo_promedio_lote`'s min-max above, used ONLY for the
# `consumo_promedio` factor's explicability TEXT.
# -------------------------------------------------------------------------------------------


def test_percentil_real_consumo_promedio_diverges_from_min_max_on_a_skewed_lote() -> None:
    """Reviewer finding (SUM-00005/SUM-00049): min-max and the TRUE percentile can diverge
    sharply on a right-skewed lote. avgs [100, 110, 120, 9000] -- min-max compresses 110/120
    near 0 (dominated by the 9000 outlier's range), while their TRUE percentile rank (linear
    interpolation, `k / (n-1) * 100`) is 33.33/66.67."""
    v1 = _vector({"avg_consumption": 100.0})
    v2 = _vector({"avg_consumption": 110.0})
    v3 = _vector({"avg_consumption": 120.0})
    v4 = _vector({"avg_consumption": 9000.0})
    vectores = [v1, v2, v3, v4]

    minmax = normalizar_consumo_promedio_lote(vectores)
    percentiles = calcular_percentil_real_consumo_promedio_lote(vectores)

    assert minmax[v2.suministro_id] == pytest.approx(10 / 8900 * 100.0)  # ~0.11
    assert minmax[v3.suministro_id] == pytest.approx(20 / 8900 * 100.0)  # ~0.22
    assert percentiles[v1.suministro_id] == pytest.approx(0.0)
    assert percentiles[v2.suministro_id] == pytest.approx(100 / 3)  # 33.33
    assert percentiles[v3.suministro_id] == pytest.approx(200 / 3)  # 66.67
    assert percentiles[v4.suministro_id] == pytest.approx(100.0)
    # the whole point of the fix: these two scales must NOT be interchangeable for v2/v3.
    assert percentiles[v2.suministro_id] != pytest.approx(minmax[v2.suministro_id])
    assert percentiles[v3.suministro_id] != pytest.approx(minmax[v3.suministro_id])


def test_calcular_percentil_real_consumo_promedio_lote_single_vector_yields_neutral_50() -> None:
    v1 = _vector({"avg_consumption": 100.0})
    assert calcular_percentil_real_consumo_promedio_lote([v1]) == {v1.suministro_id: 50.0}


def test_calcular_percentil_real_consumo_promedio_lote_empty_returns_empty() -> None:
    assert calcular_percentil_real_consumo_promedio_lote([]) == {}


def test_factor_consumo_promedio_reason_quotes_the_true_percentile_not_the_minmax_score() -> None:
    """FIX 1 regression (RN-012 honesty): on the same skewed lote, the `consumo_promedio`
    factor's `reason` text must quote the TRUE percentile (~33 for v2), NOT the min-max score
    (~0, the OLD buggy text's number -- `"percentil 0"` would be the bug reasserting itself)."""
    v1 = _vector({"avg_consumption": 100.0})
    v2 = _vector({"avg_consumption": 110.0})
    v3 = _vector({"avg_consumption": 120.0})
    v4 = _vector({"avg_consumption": 9000.0})
    vectores = [v1, v2, v3, v4]
    minmax = normalizar_consumo_promedio_lote(vectores)
    percentiles = calcular_percentil_real_consumo_promedio_lote(vectores)

    composicion = componer_ire(
        v2,
        ml_score_0_100=0.0,
        iee_kwh=None,
        iee_normalizado_0_100=0.0,
        consumo_promedio_normalizado_0_100=minmax[v2.suministro_id],
        consumo_promedio_percentil_real=percentiles[v2.suministro_id],
        categoria_nombre="Residencial",
    )

    factor = next(f for f in composicion.factores if f.factor == "consumo_promedio")
    assert "percentil 33" in factor.reason
    assert "percentil 0 " not in factor.reason
    # the WEIGHT contribution keeps using the min-max value, unaffected by the text fix.
    assert factor.contribution == pytest.approx(
        PESOS_EFECTIVOS_V1["consumo_promedio"] * minmax[v2.suministro_id]
    )


# -------------------------------------------------------------------------------------------
# redondear_half_up
# -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [(61.4, 61), (61.5, 62), (61.684782608695652, 62), (0.0, 0), (100.0, 100), (99.5, 100)],
)
def test_redondear_half_up(valor: float, esperado: int) -> None:
    assert redondear_half_up(valor) == esperado


# -------------------------------------------------------------------------------------------
# bandear_nivel_ire -- mirrors ire.nivel's GENERATED column (§10.2), 5 bands
# -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        (0, "Muy Bajo"),
        (20, "Muy Bajo"),
        (21, "Bajo"),
        (40, "Bajo"),
        (41, "Medio"),
        (60, "Medio"),
        (61, "Alto"),
        (80, "Alto"),
        (81, "Crítico"),
        (100, "Crítico"),
    ],
)
def test_bandear_nivel_ire_boundaries(valor: int, esperado: str) -> None:
    assert bandear_nivel_ire(valor) == esperado


# -------------------------------------------------------------------------------------------
# debe_generar_anomalia_ml -- v1 ML-only anomaly policy (§8/§10.3 note)
# -------------------------------------------------------------------------------------------


def test_debe_generar_anomalia_ml_true_when_critico_and_no_rule_hits() -> None:
    assert debe_generar_anomalia_ml(CLASIFICACION_CRITICO, tiene_hits_reglas=False) is True


def test_debe_generar_anomalia_ml_false_when_rule_already_fired() -> None:
    """Dedup policy: rule hits are canonical -- an ML-flagged Crítico score does NOT also get a
    'Patrón Irregular' anomaly when a rule already explains this suministro this lote."""
    assert debe_generar_anomalia_ml(CLASIFICACION_CRITICO, tiene_hits_reglas=True) is False


def test_debe_generar_anomalia_ml_false_when_not_critico() -> None:
    assert debe_generar_anomalia_ml("Alto Riesgo", tiene_hits_reglas=False) is False


def test_umbral_anomalia_ml_matches_critico_band_lower_bound() -> None:
    from energia.contexts.motor.domain.isolation_forest import BANDA_ALTO_RIESGO_MAX

    assert UMBRAL_ANOMALIA_ML == BANDA_ALTO_RIESGO_MAX


# -------------------------------------------------------------------------------------------
# componer_ire -- the full 8-factor composition (§10.1), hand-computed end to end
# -------------------------------------------------------------------------------------------


def test_componer_ire_hand_computed_full_example() -> None:
    """Hand-computed against `PESOS_EFECTIVOS_V1`:
    score_ia=80 -> 30/92*80; historial=min(4,5)*20=80 -> 15/92*80;
    persistencia=min(1*25+2*10,100)=45 -> 15/92*45;
    variacion=min(max(.5,.1),3)/3*100=50/3 -> 15/92*(50/3);
    impacto=60 (precomputed) -> 10/92*60; inspecciones=0;
    consumo_promedio=70 (precomputed) -> 4/92*70; categoria "Industrial"=90 -> 3/92*90.
    Sum = 5675/92 = 61.684782608695652 -> round half up -> 62 -> "Alto Riesgo" (41-70).
    """
    vector = _vector(
        {
            "deviation_from_baseline": 4.0,
            "zero_consumption_streak": 2,
            "prior_anomaly_count": 1,
            "pct_change_prev_period": 0.5,
            "pct_change_yoy": -0.1,
        }
    )

    composicion = componer_ire(
        vector,
        ml_score_0_100=80.0,
        iee_kwh=30.0,
        iee_normalizado_0_100=60.0,
        consumo_promedio_normalizado_0_100=70.0,
        consumo_promedio_percentil_real=70.0,
        categoria_nombre="Industrial",
    )

    assert composicion.valor_crudo == pytest.approx(5675 / 92, abs=1e-9)
    assert composicion.ire_valor == 62
    assert composicion.clasificacion == "Alto Riesgo"
    factores_por_nombre = {f.factor: f for f in composicion.factores}
    assert set(factores_por_nombre) == {
        "score_ia",
        "historial_consumos",
        "persistencia_anomalias",
        "variacion_porcentual",
        "impacto_economico",
        "consumo_promedio",
        "categoria_tarifaria",
    }  # "inspecciones_anteriores" never appears -- always-zero contribution (DEC-016 filter)
    assert factores_por_nombre["score_ia"].contribution == pytest.approx(30 / 92 * 80)
    assert factores_por_nombre["historial_consumos"].contribution == pytest.approx(15 / 92 * 80)
    assert factores_por_nombre["persistencia_anomalias"].contribution == pytest.approx(15 / 92 * 45)
    assert factores_por_nombre["variacion_porcentual"].contribution == pytest.approx(
        15 / 92 * (50 / 3)
    )
    assert factores_por_nombre["impacto_economico"].contribution == pytest.approx(10 / 92 * 60)
    assert factores_por_nombre["consumo_promedio"].contribution == pytest.approx(4 / 92 * 70)
    assert factores_por_nombre["categoria_tarifaria"].contribution == pytest.approx(3 / 92 * 90)


def test_componer_ire_null_features_contribute_zero_and_are_excluded() -> None:
    """Cold-start-shaped vector (F9/F5/F6 all null): `historial_consumos`/`variacion_porcentual`
    contribute 0 and are excluded from `factores` (DEC-016: only nonzero-contribution factors) --
    never penalized for missing history (mission directive #3)."""
    vector = _vector()  # every optional field defaults to None/0 in the `_vector` helper

    composicion = componer_ire(
        vector,
        ml_score_0_100=0.0,
        iee_kwh=None,
        iee_normalizado_0_100=0.0,
        consumo_promedio_normalizado_0_100=50.0,
        consumo_promedio_percentil_real=50.0,
        categoria_nombre="Residencial",
    )

    factores_por_nombre = {f.factor: f for f in composicion.factores}
    assert "historial_consumos" not in factores_por_nombre
    assert "variacion_porcentual" not in factores_por_nombre
    assert "impacto_economico" not in factores_por_nombre  # iee_normalizado_0_100 == 0.0


def test_componer_ire_variacion_uses_larger_absolute_of_f5_f6() -> None:
    vector = _vector({"pct_change_prev_period": -0.1, "pct_change_yoy": 0.9})
    composicion = componer_ire(
        vector,
        ml_score_0_100=0.0,
        iee_kwh=None,
        iee_normalizado_0_100=0.0,
        consumo_promedio_normalizado_0_100=0.0,
        consumo_promedio_percentil_real=0.0,
        categoria_nombre="Residencial",
    )
    factor = next(f for f in composicion.factores if f.factor == "variacion_porcentual")
    # |0.9| > |-0.1| -> uses 0.9, capped at 3.0 -> min(0.9,3)/3*100 = 30.0
    esperado_valor = 30.0
    assert factor.contribution == pytest.approx(
        PESOS_EFECTIVOS_V1["variacion_porcentual"] * esperado_valor
    )


def test_componer_ire_variacion_caps_at_300_percent() -> None:
    vector = _vector({"pct_change_prev_period": 5.0, "pct_change_yoy": None})
    composicion = componer_ire(
        vector,
        ml_score_0_100=0.0,
        iee_kwh=None,
        iee_normalizado_0_100=0.0,
        consumo_promedio_normalizado_0_100=0.0,
        consumo_promedio_percentil_real=0.0,
        categoria_nombre="Residencial",
    )
    factor = next(f for f in composicion.factores if f.factor == "variacion_porcentual")
    assert factor.contribution == pytest.approx(PESOS_EFECTIVOS_V1["variacion_porcentual"] * 100.0)


def test_componer_ire_historial_caps_at_z_5() -> None:
    vector = _vector({"deviation_from_baseline": -9.0})
    composicion = componer_ire(
        vector,
        ml_score_0_100=0.0,
        iee_kwh=None,
        iee_normalizado_0_100=0.0,
        consumo_promedio_normalizado_0_100=0.0,
        consumo_promedio_percentil_real=0.0,
        categoria_nombre="Residencial",
    )
    factor = next(f for f in composicion.factores if f.factor == "historial_consumos")
    # min(abs(-9), 5) * 20 = 100
    assert factor.contribution == pytest.approx(PESOS_EFECTIVOS_V1["historial_consumos"] * 100.0)


def test_componer_ire_categoria_unknown_falls_back_to_default() -> None:
    vector = _vector()
    composicion = componer_ire(
        vector,
        ml_score_0_100=0.0,
        iee_kwh=None,
        iee_normalizado_0_100=0.0,
        consumo_promedio_normalizado_0_100=0.0,
        consumo_promedio_percentil_real=0.0,
        categoria_nombre="Categoria Desconocida",
    )
    factor = next(f for f in composicion.factores if f.factor == "categoria_tarifaria")
    assert factor.contribution == pytest.approx(
        PESOS_EFECTIVOS_V1["categoria_tarifaria"] * CRITICIDAD_DEFAULT
    )


def test_criticidad_categoria_covers_every_seeded_categoria() -> None:
    """Anti-drift guard: every seeded `categorias_tarifarias.nombre` (`docker/postgres/init/
    04_seed.sql`) has an explicit v1 criticidad entry, not silently falling back to the default."""
    assert set(CRITICIDAD_CATEGORIA) == {
        "Residencial",
        "Comercial",
        "Industrial",
        "Grandes Demandas",
        "Alumbrado Público",
    }


def test_componer_ire_valor_never_exceeds_100_or_goes_negative() -> None:
    """Defensive invariant (RD: 'el IRE siempre debe estar entre 0 y 100', DOMAIN_MODEL.md §14):
    even at every factor's maximum simultaneously, the weighted sum cannot exceed 100 by
    construction (weights sum to 1, each factor capped at [0,100]) -- this test locks that in."""
    vector = _vector(
        {
            "deviation_from_baseline": 999.0,
            "zero_consumption_streak": 999,
            "prior_anomaly_count": 999,
            "pct_change_prev_period": 999.0,
        }
    )
    composicion = componer_ire(
        vector,
        ml_score_0_100=100.0,
        iee_kwh=999.0,
        iee_normalizado_0_100=100.0,
        consumo_promedio_normalizado_0_100=100.0,
        consumo_promedio_percentil_real=100.0,
        categoria_nombre="Grandes Demandas",
    )
    assert 0 <= composicion.ire_valor <= 100


# -------------------------------------------------------------------------------------------
# construir_informe_ire / construir_informe_iee -- mission directive #7 (API response sections)
# -------------------------------------------------------------------------------------------


def _composicion(
    ire_valor: int, *, factores: tuple[FactorContribucion, ...] = ()
) -> ComposicionIRE:
    from energia.contexts.motor.domain.isolation_forest import bandear_clasificacion

    return ComposicionIRE(
        ire_valor=ire_valor,
        valor_crudo=float(ire_valor),
        clasificacion=bandear_clasificacion(float(ire_valor)),
        factores=factores,
    )


def _entrada(
    *,
    numero_suministro: str,
    ire_valor: int,
    iee_kwh: float | None = None,
    anomalia_tipos: tuple[str, ...] = (),
    factores: tuple[FactorContribucion, ...] = (),
) -> ResultadoSuministroParaInforme:
    return ResultadoSuministroParaInforme(
        suministro_id=uuid4(),
        numero_suministro=numero_suministro,
        composicion=_composicion(ire_valor, factores=factores),
        iee_kwh=iee_kwh,
        anomalia_tipos=anomalia_tipos,
    )


def test_construir_informe_ire_empty_entradas() -> None:
    informe = construir_informe_ire(uuid4(), [])
    assert informe.suministros_evaluados == 0
    assert informe.distribucion is None
    assert informe.top_10 == ()
    assert informe.conteo_por_nivel == {
        "Muy Bajo": 0,
        "Bajo": 0,
        "Medio": 0,
        "Alto": 0,
        "Crítico": 0,
    }
    assert informe.anomalias_persistidas_por_tipo == {
        "Consumo Muy Bajo": 0,
        "Consumo Muy Alto": 0,
        "Caída Brusca": 0,
        "Incremento Brusco": 0,
        "Patrón Irregular": 0,
        "Persistencia Anómala": 0,
        "Desvío Estadístico": 0,
    }


def test_construir_informe_ire_distribution_and_nivel_counts_hand_computed() -> None:
    lote_id = uuid4()
    entradas = [
        _entrada(numero_suministro="SUM-1", ire_valor=10),  # Muy Bajo
        _entrada(numero_suministro="SUM-2", ire_valor=30),  # Bajo
        _entrada(numero_suministro="SUM-3", ire_valor=90),  # Crítico
        _entrada(numero_suministro="SUM-4", ire_valor=90),  # Crítico
    ]
    informe = construir_informe_ire(lote_id, entradas)

    assert informe.lote_id == lote_id
    assert informe.suministros_evaluados == 4
    assert informe.conteo_por_nivel["Muy Bajo"] == 1
    assert informe.conteo_por_nivel["Bajo"] == 1
    assert informe.conteo_por_nivel["Crítico"] == 2
    assert informe.conteo_por_nivel["Medio"] == 0
    # sorted [10, 30, 90, 90] -- p50 index=0.5*3=1.5 -> interpolate 30/90 at .5 -> 60.0
    # p95 index=0.95*3=2.85 -> interpolate 90/90 at .85 -> 90.0
    assert informe.distribucion is not None
    assert informe.distribucion.minimo == 10
    assert informe.distribucion.maximo == 90
    assert informe.distribucion.p50 == pytest.approx(60.0)
    assert informe.distribucion.p95 == pytest.approx(90.0)


def test_construir_informe_ire_top_10_sorted_desc_with_top_3_factores() -> None:
    factores = (
        FactorContribucion(factor="score_ia", contribution=30.0, reason="r1"),
        FactorContribucion(factor="historial_consumos", contribution=20.0, reason="r2"),
        FactorContribucion(factor="variacion_porcentual", contribution=10.0, reason="r3"),
        FactorContribucion(factor="consumo_promedio", contribution=1.0, reason="r4"),
    )
    entradas = [_entrada(numero_suministro=f"SUM-{i}", ire_valor=i) for i in range(15)]
    entradas[-1] = _entrada(numero_suministro="SUM-TOP", ire_valor=99, factores=factores)

    informe = construir_informe_ire(uuid4(), entradas)

    assert len(informe.top_10) == 10
    assert informe.top_10[0].numero_suministro == "SUM-TOP"
    assert informe.top_10[0].ire == 99
    assert informe.top_10[0].nivel == "Crítico"
    # only the top 3 BY CONTRIBUTION, descending -- "consumo_promedio" (1.0) dropped.
    assert [f.factor for f in informe.top_10[0].top_factores] == [
        "score_ia",
        "historial_consumos",
        "variacion_porcentual",
    ]


def test_construir_informe_ire_anomalias_persistidas_counts_by_tipo() -> None:
    entradas = [
        _entrada(numero_suministro="SUM-1", ire_valor=50, anomalia_tipos=("Persistencia Anómala",)),
        _entrada(
            numero_suministro="SUM-2",
            ire_valor=60,
            anomalia_tipos=("Persistencia Anómala", "Patrón Irregular"),
        ),
        _entrada(numero_suministro="SUM-3", ire_valor=10, anomalia_tipos=()),
    ]
    informe = construir_informe_ire(uuid4(), entradas)

    assert informe.anomalias_persistidas_por_tipo["Persistencia Anómala"] == 2
    assert informe.anomalias_persistidas_por_tipo["Patrón Irregular"] == 1
    assert informe.anomalias_persistidas_por_tipo["Caída Brusca"] == 0


def test_construir_informe_iee_empty_entradas() -> None:
    informe = construir_informe_iee(uuid4(), [])
    assert informe.total_kwh_estimado == 0.0
    assert informe.suministros_con_iee == 0
    assert informe.top_5 == ()


def test_construir_informe_iee_hand_computed() -> None:
    lote_id = uuid4()
    entradas = [
        _entrada(numero_suministro="SUM-1", ire_valor=1, iee_kwh=0.0),
        _entrada(numero_suministro="SUM-2", ire_valor=1, iee_kwh=50.0),
        _entrada(numero_suministro="SUM-3", ire_valor=1, iee_kwh=150.0),
        _entrada(numero_suministro="SUM-4", ire_valor=1, iee_kwh=None),  # cold-start, no IEE
    ]
    informe = construir_informe_iee(lote_id, entradas)

    assert informe.lote_id == lote_id
    assert informe.total_kwh_estimado == pytest.approx(200.0)  # 0 + 50 + 150
    assert informe.suministros_con_iee == 2  # only IEE > 0
    assert [e.numero_suministro for e in informe.top_5] == ["SUM-3", "SUM-2", "SUM-1"]


def test_construir_informe_iee_top_5_capped_and_excludes_none() -> None:
    entradas = [
        _entrada(numero_suministro=f"SUM-{i}", ire_valor=1, iee_kwh=float(i)) for i in range(7)
    ] + [_entrada(numero_suministro="SUM-NONE", ire_valor=1, iee_kwh=None)]

    informe = construir_informe_iee(uuid4(), entradas)

    assert len(informe.top_5) == 5
    assert [e.numero_suministro for e in informe.top_5] == [
        "SUM-6",
        "SUM-5",
        "SUM-4",
        "SUM-3",
        "SUM-2",
    ]
