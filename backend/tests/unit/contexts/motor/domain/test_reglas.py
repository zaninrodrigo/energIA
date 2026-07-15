"""Unit tests for Etapa 5 -- Reglas de negocio (AI_ENGINE_SPEC.md §8, DEC-009 thresholds).

Every rule is a pure function; every expected value below is hand-computed (STRICT TDD), not
copied from the implementation. `evaluar_reglas` is exercised against `FeatureVector`s built
directly (never through `construir_feature_vector`) -- Etapa 5 must consume whatever the
ALREADY-BUILT vector says, never recompute Etapa 3-4's math (mission directive: "do not recompute
features").
"""

from datetime import date
from uuid import UUID, uuid4

from energia.contexts.motor.domain.features import FeatureVector
from energia.contexts.motor.domain.ports import SuministroMetadataRow
from energia.contexts.motor.domain.reglas import (
    R1_UMBRAL_RACHA,
    R2_UMBRAL_CAIDA,
    R3_UMBRAL_INCREMENTO,
    R4_PEER_RATIO_BAJO,
    R4_PERCENTIL_BAJO,
    R5_PEER_RATIO_ALTO,
    R5_PERCENTIL_ALTO,
    R6_UMBRAL_DESVIO,
    InformeReglas,
    ReglasSuministro,
    ResumenReglas,
    construir_informe_reglas,
    evaluar_reglas,
)


def _vector(
    *,
    suministro_id: UUID | None = None,
    lote_id: UUID | None = None,
    zero_streak: int = 0,
    pct_change_prev: float | None = None,
    percentile_peer: float | None = None,
    peer_ratio: float | None = None,
    deviation: float | None = None,
) -> FeatureVector:
    features: dict[str, object] = {
        "zero_consumption_streak": zero_streak,
        "pct_change_prev_period": pct_change_prev,
        "percentile_peer": percentile_peer,
        "peer_ratio": peer_ratio,
        "deviation_from_baseline": deviation,
    }
    return FeatureVector(
        suministro_id=suministro_id or uuid4(),
        lote_id=lote_id or uuid4(),
        features=features,
        is_cold_start=False,
        has_conflicted_periods=False,
        categoria_tarifaria_id=uuid4(),
        localidad=None,
        kwh_actual=100.0,
    )


def _metadata(
    suministro_id: UUID, *, estado: str = "Activo", numero_suministro: str = "SUM-1"
) -> SuministroMetadataRow:
    return SuministroMetadataRow(
        suministro_id=suministro_id,
        categoria_tarifaria_id=uuid4(),
        localidad=None,
        fecha_alta=date(2020, 1, 1),
        numero_suministro=numero_suministro,
        estado=estado,
    )


# ---------------------------------------------------------------------------------------------
# R1 -- Persistencia Anómala (racha de cero >= 3, suministro activo)
# ---------------------------------------------------------------------------------------------


def test_r1_fires_at_exactly_the_threshold_when_suministro_active() -> None:
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, zero_streak=R1_UMBRAL_RACHA)
    metadata = _metadata(suministro_id, estado="Activo")

    hits = evaluar_reglas(vector, [], metadata)

    assert len(hits) == 1
    hit = hits[0]
    assert hit.regla == "R1"
    assert hit.tipo == "Persistencia Anómala"
    assert hit.severidad == "Alta"
    assert "3" in hit.descripcion


def test_r1_does_not_fire_below_the_threshold() -> None:
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, zero_streak=R1_UMBRAL_RACHA - 1)
    metadata = _metadata(suministro_id, estado="Activo")

    hits = evaluar_reglas(vector, [], metadata)

    assert hits == ()


def test_r1_does_not_fire_when_suministro_is_not_active() -> None:
    """A long zero-streak on a suministro that is no longer `Activo` (e.g. `Baja`) is not an
    anomaly to chase -- it is expected: nobody is being billed there anymore."""
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, zero_streak=10)
    metadata = _metadata(suministro_id, estado="Baja")

    hits = evaluar_reglas(vector, [], metadata)

    assert hits == ()


# ---------------------------------------------------------------------------------------------
# R2 -- Caída Brusca (pct_change_prev_period <= -60%, período único -- recalibrado 2026-07-15,
# calibración v1.1: ya NO exige "sostenida >= 2 períodos", ver domain/reglas.py's docstrings)
# ---------------------------------------------------------------------------------------------


def test_r2_fires_at_exactly_the_threshold() -> None:
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, pct_change_prev=R2_UMBRAL_CAIDA)
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert len(hits) == 1
    hit = hits[0]
    assert hit.regla == "R2"
    assert hit.tipo == "Caída Brusca"
    assert hit.severidad == "Alta"
    assert f"{R2_UMBRAL_CAIDA:.0%}" in hit.descripcion


def test_r2_fires_on_a_single_cliff_period() -> None:
    """Recalibrado (calibración v1.1): a SINGLE period breaching -60% is enough -- R2 no longer
    demands the previous period also breach ("sostenida >= 2 períodos"). This is exactly the
    shape `tools/synthetic/anomalies.py`'s `inject_sudden_drop` plants: one cliff month at
    pct_change ~= -0.63, never a second consecutive breach (see the next test)."""
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, pct_change_prev=-0.63)
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert len(hits) == 1
    assert hits[0].regla == "R2"


def test_r2_does_not_fire_on_the_flat_month_following_the_cliff() -> None:
    """The realistic shape of a "caída brusca": the month right after the cliff holds at the NEW
    LOW LEVEL -- its own pct_change_prev_period compares low-against-low (a normal value, e.g.
    -2%), never another -60% breach. R2 (single-period cliff) correctly does not fire on that
    following month; R6 (deviation_from_baseline) is the rule that keeps flagging the persisting
    low level instead (AI_ENGINE_SPEC.md §8)."""
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, pct_change_prev=-0.02)
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert hits == ()


def test_r2_does_not_fire_above_the_threshold() -> None:
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, pct_change_prev=R2_UMBRAL_CAIDA + 0.01)
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert hits == ()


def test_r2_does_not_fire_when_the_current_pct_change_is_null() -> None:
    """Cold-start (no previous period at all for F5 itself) -- `pct_change_prev_period` is
    `None`; R2 must not fire."""
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, pct_change_prev=None)
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert hits == ()


# ---------------------------------------------------------------------------------------------
# R3 -- Incremento Brusco (pct_change_prev_period >= +200%, un período)
# ---------------------------------------------------------------------------------------------


def test_r3_fires_at_exactly_the_threshold() -> None:
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, pct_change_prev=R3_UMBRAL_INCREMENTO)
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert len(hits) == 1
    hit = hits[0]
    assert hit.regla == "R3"
    assert hit.tipo == "Incremento Brusco"
    assert hit.severidad == "Media"
    assert "200%" in hit.descripcion


def test_r3_does_not_fire_below_the_threshold() -> None:
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, pct_change_prev=R3_UMBRAL_INCREMENTO - 0.01)
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert hits == ()


def test_r3_does_not_fire_when_null() -> None:
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, pct_change_prev=None)
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert hits == ()


# ---------------------------------------------------------------------------------------------
# R4 -- Consumo Muy Bajo (percentile_peer <= percentil 5 AND peer_ratio <= 0.4 -- recalibrado
# 2026-07-15, calibración v1.1: conjunción con peer_ratio, ver domain/reglas.py's docstrings)
# ---------------------------------------------------------------------------------------------


def test_r4_fires_at_exactly_the_threshold() -> None:
    suministro_id = uuid4()
    vector = _vector(
        suministro_id=suministro_id,
        percentile_peer=R4_PERCENTIL_BAJO,
        peer_ratio=R4_PEER_RATIO_BAJO,
    )
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert len(hits) == 1
    hit = hits[0]
    assert hit.regla == "R4"
    assert hit.tipo == "Consumo Muy Bajo"
    assert hit.severidad == "Media"


def test_r4_does_not_fire_above_the_percentile_threshold() -> None:
    suministro_id = uuid4()
    vector = _vector(
        suministro_id=suministro_id,
        percentile_peer=R4_PERCENTIL_BAJO + 0.01,
        peer_ratio=R4_PEER_RATIO_BAJO,
    )
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert hits == ()


def test_r4_does_not_fire_when_percentile_extreme_but_peer_ratio_moderate() -> None:
    """Reviewer scenario, mirrored from R5: a cohort-minimum member with peer_ratio 0.7 -- only
    mildly below the cohort median. percentile_peer alone (the low end of a small cohort, always
    inclusive-low by construction, §7) is NOT enough; peer_ratio must ALSO confirm the magnitude.
    Above R4's peer_ratio threshold -> no hit, even though the pre-recalibration, percentile-only
    condition would have fired."""
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, percentile_peer=0.0, peer_ratio=0.7)
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert hits == ()


def test_r4_fires_when_percentile_and_peer_ratio_both_breach() -> None:
    """Reviewer scenario: a cohort-minimum member with peer_ratio 0.3 -- genuinely far below its
    peers, not just the lowest of a small group. Both components breach -> R4 fires."""
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, percentile_peer=0.0, peer_ratio=0.3)
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert len(hits) == 1
    assert hits[0].regla == "R4"


def test_r4_does_not_fire_for_sudden_drop_leve_shaped_ratio() -> None:
    """Reviewer scenario, mirrored from R5's spike_leve case: a sudden_drop_leve-shaped
    peer_ratio (~0.55, deliberately sub-threshold for R2 too, tools/synthetic/anomalies.py) --
    above R4_PEER_RATIO_BAJO (0.4) -> no hit, even at percentile 0."""
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, percentile_peer=0.0, peer_ratio=0.55)
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert hits == ()


def test_r4_does_not_fire_when_percentile_is_null() -> None:
    """Null percentile (cohort too small, DEC-008) -- rule not evaluable, no hit, even with a
    breaching peer_ratio present."""
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, percentile_peer=None, peer_ratio=0.3)
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert hits == ()


def test_r4_does_not_fire_when_peer_ratio_is_null() -> None:
    """Null peer_ratio (its own null case, `domain/features.py`) -- rule not evaluable, no hit,
    even with a breaching percentile present."""
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, percentile_peer=0.0, peer_ratio=None)
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert hits == ()


# ---------------------------------------------------------------------------------------------
# R5 -- Consumo Muy Alto (percentile_peer >= percentil 95 AND peer_ratio >= 2.5 -- recalibrado
# 2026-07-15, calibración v1.1: conjunción con peer_ratio, ver domain/reglas.py's docstrings)
# ---------------------------------------------------------------------------------------------


def test_r5_fires_at_exactly_the_threshold() -> None:
    suministro_id = uuid4()
    vector = _vector(
        suministro_id=suministro_id,
        percentile_peer=R5_PERCENTIL_ALTO,
        peer_ratio=R5_PEER_RATIO_ALTO,
    )
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert len(hits) == 1
    hit = hits[0]
    assert hit.regla == "R5"
    assert hit.tipo == "Consumo Muy Alto"
    assert hit.severidad == "Media"


def test_r5_does_not_fire_below_the_percentile_threshold() -> None:
    suministro_id = uuid4()
    vector = _vector(
        suministro_id=suministro_id,
        percentile_peer=R5_PERCENTIL_ALTO - 0.01,
        peer_ratio=R5_PEER_RATIO_ALTO,
    )
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert hits == ()


def test_r5_does_not_fire_when_percentile_extreme_but_peer_ratio_moderate() -> None:
    """Reviewer scenario: a cohort-maximum member with peer_ratio 1.3 -- only mildly above the
    cohort median. percentile_peer alone (1.0, the max of a small cohort, always inclusive-1.0 by
    construction, §7) is NOT enough; peer_ratio must ALSO confirm the magnitude. Below R5's
    peer_ratio threshold -> no hit, even though the pre-recalibration, percentile-only condition
    would have fired (AI_ENGINE_SPEC.md §8.2: this was 142/144, ~98.6%, of R5's disparos)."""
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, percentile_peer=1.0, peer_ratio=1.3)
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert hits == ()


def test_r5_fires_when_percentile_and_peer_ratio_both_breach() -> None:
    """Reviewer scenario: a cohort-maximum member with peer_ratio 3.0 -- genuinely far above its
    peers, not just the highest of a small group. Both components breach -> R5 fires."""
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, percentile_peer=1.0, peer_ratio=3.0)
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert len(hits) == 1
    assert hits[0].regla == "R5"


def test_r5_does_not_fire_for_spike_leve_shaped_ratio() -> None:
    """Reviewer scenario: a spike_leve-shaped peer_ratio (1.98, deliberately sub-threshold for R3
    too, tools/synthetic/anomalies.py) -- below R5_PEER_RATIO_ALTO (2.5) -> no hit, even at
    percentile 1.0. This is the exact case AI_ENGINE_SPEC.md §8.2 flagged as an "incidental
    capture" (SYN-S42-SUM-00005) under the pre-recalibration, percentile-only R5."""
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, percentile_peer=1.0, peer_ratio=1.98)
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert hits == ()


def test_r5_does_not_fire_when_percentile_is_null() -> None:
    """Null percentile (cohort too small, DEC-008) -- rule not evaluable, no hit, even with a
    breaching peer_ratio present."""
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, percentile_peer=None, peer_ratio=3.0)
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert hits == ()


def test_r5_does_not_fire_when_peer_ratio_is_null() -> None:
    """Null peer_ratio -- rule not evaluable, no hit, even with a breaching percentile present."""
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, percentile_peer=1.0, peer_ratio=None)
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert hits == ()


# ---------------------------------------------------------------------------------------------
# R6 -- Desvío Estadístico (|deviation_from_baseline| >= 3)
# ---------------------------------------------------------------------------------------------


def test_r6_fires_at_exactly_the_positive_threshold() -> None:
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, deviation=float(R6_UMBRAL_DESVIO))
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert len(hits) == 1
    hit = hits[0]
    assert hit.regla == "R6"
    assert hit.tipo == "Desvío Estadístico"
    assert hit.severidad == "Media"


def test_r6_fires_at_exactly_the_negative_threshold() -> None:
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, deviation=-float(R6_UMBRAL_DESVIO))
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert len(hits) == 1
    assert hits[0].regla == "R6"


def test_r6_does_not_fire_inside_the_band() -> None:
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, deviation=R6_UMBRAL_DESVIO - 0.01)
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert hits == ()


def test_r6_does_not_fire_when_null() -> None:
    suministro_id = uuid4()
    vector = _vector(suministro_id=suministro_id, deviation=None)
    metadata = _metadata(suministro_id)

    hits = evaluar_reglas(vector, [], metadata)

    assert hits == ()


# ---------------------------------------------------------------------------------------------
# evaluar_reglas -- aggregation across rules (independent, no dedup)
# ---------------------------------------------------------------------------------------------


def test_evaluar_reglas_fires_every_independently_breaching_rule_at_once() -> None:
    """One suministro can fire MULTIPLE rules -- they are independent, no dedup across rules
    (mission directive)."""
    suministro_id = uuid4()
    vector = _vector(
        suministro_id=suministro_id,
        zero_streak=5,  # R1
        percentile_peer=0.0,  # R4 (with peer_ratio below, recalibrado 2026-07-15)
        peer_ratio=0.3,  # R4
        deviation=4.0,  # R6
    )
    metadata = _metadata(suministro_id, estado="Activo")

    hits = evaluar_reglas(vector, [], metadata)

    reglas_disparadas = {hit.regla for hit in hits}
    assert reglas_disparadas == {"R1", "R4", "R6"}


def test_evaluar_reglas_returns_empty_tuple_for_a_cold_start_suministro() -> None:
    """Nulls NEVER fire rules -- a cold-start suministro (every desvío-based feature `null`, only
    `zero_consumption_streak` defaulting to `0`, DEC-006/DEC-007) produces no hits at all, with no
    special-cased `is_cold_start` guard anywhere in this module: it falls out naturally from every
    rule's own null check."""
    suministro_id = uuid4()
    vector = _vector(
        suministro_id=suministro_id,
        zero_streak=0,
        pct_change_prev=None,
        percentile_peer=None,
        peer_ratio=None,
        deviation=None,
    )
    metadata = _metadata(suministro_id, estado="Activo")

    hits = evaluar_reglas(vector, [], metadata)

    assert hits == ()


# ---------------------------------------------------------------------------------------------
# ResumenReglas / InformeReglas
# ---------------------------------------------------------------------------------------------


def test_construir_informe_reglas_counts_hits_per_regla_and_suministros_con_hits() -> None:
    lote_id = uuid4()
    sin_hits = ReglasSuministro(suministro_id=uuid4(), numero_suministro="SUM-A", hits=())
    con_r1 = ReglasSuministro(
        suministro_id=uuid4(),
        numero_suministro="SUM-B",
        hits=evaluar_reglas(
            _vector(zero_streak=5), [], _metadata(uuid4(), numero_suministro="SUM-B")
        ),
    )
    con_r1_y_r6 = ReglasSuministro(
        suministro_id=uuid4(),
        numero_suministro="SUM-C",
        hits=evaluar_reglas(
            _vector(zero_streak=10, deviation=5.0),
            [],
            _metadata(uuid4(), numero_suministro="SUM-C"),
        ),
    )

    informe = construir_informe_reglas(
        lote_id, [sin_hits, con_r1, con_r1_y_r6], suministros_evaluados=3
    )

    assert isinstance(informe, InformeReglas)
    assert informe.lote_id == lote_id
    assert informe.resumen.suministros_evaluados == 3
    assert informe.resumen.suministros_con_hits == 2  # sin_hits excluded
    assert informe.resumen.hits_por_regla["R1"] == 2  # con_r1 + con_r1_y_r6
    assert informe.resumen.hits_por_regla["R6"] == 1
    assert informe.resumen.hits_por_regla["R2"] == 0
    # every rule key is always present, even with zero hits.
    assert set(informe.resumen.hits_por_regla) == {"R1", "R2", "R3", "R4", "R5", "R6"}


def test_construir_informe_reglas_only_lists_suministros_with_hits() -> None:
    """Bounded, actionable output (mission directive): a suministro with zero hits carries no
    signal, so it is not listed in `InformeReglas.suministros` -- only in the summary counts."""
    lote_id = uuid4()
    sin_hits = ReglasSuministro(suministro_id=uuid4(), numero_suministro="SUM-A", hits=())
    con_hits = ReglasSuministro(
        suministro_id=uuid4(),
        numero_suministro="SUM-B",
        hits=evaluar_reglas(
            _vector(zero_streak=5), [], _metadata(uuid4(), numero_suministro="SUM-B")
        ),
    )

    informe = construir_informe_reglas(lote_id, [sin_hits, con_hits], suministros_evaluados=2)

    assert len(informe.suministros) == 1
    assert informe.suministros[0].numero_suministro == "SUM-B"


def test_construir_informe_reglas_with_no_entries_is_all_zero() -> None:
    lote_id = uuid4()

    informe = construir_informe_reglas(lote_id, [], suministros_evaluados=0)

    assert informe.resumen.suministros_evaluados == 0
    assert informe.resumen.suministros_con_hits == 0
    assert informe.suministros == ()
    assert isinstance(informe.resumen, ResumenReglas)
