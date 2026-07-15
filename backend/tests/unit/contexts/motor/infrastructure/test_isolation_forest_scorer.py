"""Smoke tests for `SklearnIsolationForestScorer` (Etapa 6, AI_ENGINE_SPEC.md §9). Per mission
directive, sklearn's own fitting/scoring logic is NOT unit-tested against hand-computed values --
only that the adapter (a) returns one score per input row, (b) is deterministic given
`RANDOM_STATE` (RD-028), and (c) produces a materially more negative (more anomalous) score for
an obvious outlier row than for a tight cluster of normal rows -- a coarse sanity check on the
WIRING (RobustScaler -> IsolationForest -> decision_function), not on sklearn's internals.
"""

from energia.contexts.motor.infrastructure.isolation_forest_scorer import (
    SklearnIsolationForestScorer,
)


def _matriz_con_un_outlier(n_normales: int = 30) -> list[list[float]]:
    """`n_normales` tightly-clustered rows (small jitter around `1.0`, deterministic -- no RNG
    used here, this is test data, not the model itself) plus ONE obvious outlier row (`1000.0`
    for every feature)."""
    normales = [[1.0 + (i % 3) * 0.01, 2.0 - (i % 2) * 0.01] for i in range(n_normales)]
    outlier = [1000.0, 1000.0]
    return [*normales, outlier]


async def test_returns_one_score_per_row_same_order() -> None:
    scorer = SklearnIsolationForestScorer()
    matriz = _matriz_con_un_outlier()

    scores = await scorer.ajustar_y_puntuar(matriz)

    assert len(scores) == len(matriz)
    assert all(isinstance(score, float) for score in scores)


async def test_deterministic_given_fixed_random_state() -> None:
    scorer = SklearnIsolationForestScorer()
    matriz = _matriz_con_un_outlier()

    scores_1 = await scorer.ajustar_y_puntuar(matriz)
    scores_2 = await scorer.ajustar_y_puntuar(matriz)

    assert list(scores_1) == list(scores_2)


async def test_obvious_outlier_scores_more_negative_than_the_normal_cluster() -> None:
    """Coarse sanity check on the wiring (module docstring) -- NOT a hand-computed sklearn
    assertion: the planted outlier's raw `decision_function` score must be more negative (more
    anomalous, AI_ENGINE_SPEC.md §9.3) than every normal row's own score."""
    scorer = SklearnIsolationForestScorer()
    matriz = _matriz_con_un_outlier()

    scores = await scorer.ajustar_y_puntuar(matriz)

    *scores_normales, score_outlier = scores
    assert score_outlier < min(scores_normales)


async def test_max_samples_larger_than_the_group_does_not_raise() -> None:
    """DEC-012's `max_samples=256` must not blow up (or emit sklearn's clipping warning as an
    error under `-W error`) when a training group -- e.g. this repo's own small synthetic
    dataset, 100 suministros -- has FEWER rows than 256; the adapter pre-clips instead of relying
    on sklearn's own silent-but-warned clipping (`domain/isolation_forest.py` module docstring;
    `infrastructure/isolation_forest_scorer.py`'s own docstring for the reasoning)."""
    scorer = SklearnIsolationForestScorer()
    matriz = _matriz_con_un_outlier(n_normales=5)  # 6 rows total, well under 256

    scores = await scorer.ajustar_y_puntuar(matriz)

    assert len(scores) == 6
