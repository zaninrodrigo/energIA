"""SklearnIsolationForestScorer: `IsolationForestScorer`'s implementation (Etapa 6, US-011/RF-006,
AI_ENGINE_SPEC.md §9) -- the ONE non-pure piece of the ML branch (`domain/isolation_forest.py`'s
module docstring): `RobustScaler` (DEC-006) + `IsolationForest` (DEC-011/DEC-012), fit and scored
on a worker thread.

**Why `asyncio.to_thread`, not a bare synchronous call.** Fitting/scoring is CPU-bound (AI_ENGINE_
SPEC.md §2.4/§12): a bare synchronous call here would block the event loop for the entire fit,
stalling every OTHER concurrent request this API process is serving (dashboards included) for as
long as the forest takes to build. `asyncio.to_thread` runs it on a worker thread instead, letting
the event loop keep serving other requests while this one waits. **This is the v1 compromise, not
the destination** -- AI_ENGINE_SPEC.md §2.4 already documents that Etapa 6 (a CPU-bound stage) is
exactly the trigger for moving the motor into ADR-006's dedicated worker process/container, still
pending until real production volumes approach RNF-007 (500.000 suministros); in-request
threading only avoids blocking the SAME process's event loop, it does NOT give this stage its own
CPU/process isolation the way a separate worker would. See that section for the full note.

**`max_samples` pre-clipping.** DEC-012 fixes `max_samples=256`, but a training group can have
FEWER rows than that (e.g. this repo's own small synthetic dataset's single "global" group, 100
suministros -- `agrupar_para_entrenamiento` never guarantees a group reaches 256). sklearn itself
clips silently-but-warned (`max_samples = min(max_samples, n_samples)` with a `UserWarning`) when
given an `int` larger than the row count -- this adapter pre-clips to avoid that warning (and to
make the actual value used explicit rather than implicit sklearn behavior).
"""

import asyncio
from collections.abc import Sequence

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler

from energia.contexts.motor.domain.isolation_forest import (
    IF_CONTAMINATION,
    IF_MAX_SAMPLES,
    IF_N_ESTIMATORS,
    RANDOM_STATE,
)


def _ajustar_y_puntuar_sync(matriz: Sequence[Sequence[float]]) -> list[float]:
    """The actual sklearn work -- runs on a worker thread (`ajustar_y_puntuar` below), never on
    the event loop directly."""
    arreglo = np.asarray(matriz, dtype=float)
    escalado = RobustScaler().fit_transform(arreglo)

    modelo = IsolationForest(
        contamination=IF_CONTAMINATION,
        n_estimators=IF_N_ESTIMATORS,
        max_samples=min(IF_MAX_SAMPLES, arreglo.shape[0]),
        random_state=RANDOM_STATE,
    )
    modelo.fit(escalado)
    return [float(score) for score in modelo.decision_function(escalado)]


class SklearnIsolationForestScorer:
    """`IsolationForestScorer` (domain/ports.py) backed by scikit-learn, fit fresh per call (one
    call per training group, `application/procesar_lote.py`'s `_ejecutar_isolation_forest`) --
    Etapa 6's v1 policy is "(re)fit per lote, unsupervised" (DEC-018), never a persisted/reloaded
    model object."""

    async def ajustar_y_puntuar(self, matriz: Sequence[Sequence[float]]) -> Sequence[float]:
        return await asyncio.to_thread(_ajustar_y_puntuar_sync, matriz)
