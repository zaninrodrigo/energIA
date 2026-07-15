"""Etapa 5 -- Reglas de negocio (AI_ENGINE_SPEC.md §8).

The **rule branch** (ADR-005): explicit, named thresholds over the Etapa 3-4 feature vector,
directly satisfying RN-012 (every automated decision must be explicable) -- each hit traces to a
named rule (`R1`..`R6`) with a human-readable `descripcion` built from the actual numbers that
fired it, not a black-box score.

**Architecture decision: COMPUTE + REPORT only, no persistence here (mirrors §4.2's precedent).**
`anomalias.resultado_ia_id` is `NOT NULL` (`docker/postgres/init/01_schema.sql`) -- persisting an
`Anomalia` row REQUIRES a `ResultadoIA` row, which in turn requires `modelo_ia_id` (`NOT NULL`,
FK) and `clasificacion` (`NOT NULL`, CHECK), neither of which exists until Etapas 6-7 (Isolation
Forest scoring + IRE composition) run. Etapa 5, run in isolation (as it is here, before Etapas 6-7
exist), cannot write `anomalias` without fabricating a `modelo_ia_id`/`clasificacion` that would
not represent anything real -- exactly the same reasoning `informe_validacion.py`'s module
docstring and AI_ENGINE_SPEC.md §4.2 already applied to Etapa 1's `InformeValidacion`. This
module's rule hits therefore live ONLY in the `POST /procesar` response body (the `reglas` field,
`ProcesarLote`/`presentation/schemas.py`) for this implementation; persistence of `anomalias`
converges into Etapa 7's single atomic `ResultadoIA` write, once a real `modelo_ia_id` and
`clasificacion` exist to hang the FK off of.

**Pure functions, no I/O.** Every `_r*` function below takes the suministro's own, ALREADY-BUILT
`FeatureVector` (Etapa 3-4, `domain/features.py`) -- `ProcesarLote` calls this module AFTER
building and cohort-enriching every vector, and NEVER recomputes F1-F17/the Etapa 4 indicators to
evaluate a rule; every rule reads whatever the vector already says.

**R2 recalibrated 2026-07-15 (calibración v1.1, evidencia sintética) -- single-period cliff, no
longer "sostenida >= 2 períodos".** The original "sostenida >= 2 períodos" condition (requiring
BOTH the current period's `pct_change_prev_period` AND the immediately previous period's own
`pct_change` to breach -60%) measured ZERO firings across 2.321 real evaluations, including the
planted `sudden_drop` anomaly it was meant to catch (AI_ENGINE_SPEC.md §8.2): a real "caída
brusca" is a STEP -- one cliff month, then the level HOLDS at the new low (the following month's
own `pct_change` compares low-against-low, a normal value, never another -60% breach) -- so the
second breaching period R2 demanded never appears. R2 now fires on the CURRENT period's
`pct_change_prev_period` alone, exactly like R3 does for the opposite direction; the "sostenida"
INTENT (flagging that the low level persists) is served by R6 (`deviation_from_baseline`), which
keeps flagging the following months while the suministro stays off its historical baseline. R2
therefore no longer needs any extra period of history -- `evaluar_reglas` still accepts
`historia_filtrada` (kept for signature stability with `ProcesarLote._evaluar_reglas`'s existing
call site), but no rule reads it as of this recalibration.

**Nulls NEVER fire rules.** Every rule below narrows its input with an `isinstance` check before
comparing against its threshold (mirrors `domain/features.py`'s `resumir_features` -- `features`
is typed `dict[str, object]`, jsonb has no fixed schema mypy can see through). A cold-start
suministro (DEC-006/DEC-007: desvío-based features `null` until enough history exists) therefore
produces NO hits at all, for ANY rule that reads a nullable feature -- there is no separate
`is_cold_start` special case anywhere in this module; the guard is implicit in each rule's own
null check, exactly as the mission calls for.

**One suministro, multiple rules, no dedup.** `evaluar_reglas` evaluates all six rules
independently and returns every hit -- a suministro whose zero-streak AND percentile AND
deviation all breach their thresholds at once gets THREE hits (`R1`+`R4`+`R6`), not the "worst
one": each rule is its own named, independently-explicable signal (RN-012), and collapsing them
would hide information an inspector could use.

**DEC-009 thresholds are calibration-pending candidates, not validated numbers** -- the same
caveat every other implementation-level constant in this bounded context documents
(`domain/checks.py`'s `TOLERANCIA_KWH_LECTURA`, `domain/duplicidades.py`'s
`VENTANA_DIAS_LECTURA_NEAR_DUPLICATE`, `domain/features.py`'s `COHORTE_MINIMA`): AI_ENGINE_SPEC.md
§8 names them "candidatas, no verdades" pending real data (PROJECT_MASTER_SPEC.md #8). **R2's and
R4/R5's thresholds were recalibrated 2026-07-15 (calibración v1.1) against real synthetic-data
evidence** (AI_ENGINE_SPEC.md §8.2) -- still candidates, now grounded in a measured run rather than
theory alone; revisit again once real production cohort sizes/distributions exist.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from energia.contexts.motor.domain.features import FeatureVector
from energia.contexts.motor.domain.ports import FeatureConsumoRow, SuministroMetadataRow

# R1 (AI_ENGINE_SPEC.md §8/§15, DEC-009): zero_consumption_streak >= this, AND suministro active,
# fires "Persistencia Anómala" / Alta.
R1_UMBRAL_RACHA = 3

# R2 (DEC-009, recalibrado 2026-07-15 -- calibración v1.1): pct_change_prev_period <= this (a 60%
# drop), on the CURRENT period ALONE -- single-period cliff. Fires "Caída Brusca" / Alta. Was
# originally "sostenida >= 2 períodos" (current AND previous period both breaching); measured 0
# firings across 2.321 real evaluations, including the planted `sudden_drop` anomaly it targets
# (module docstring, AI_ENGINE_SPEC.md §8.2) -- a real cliff HOLDS at the new low level rather
# than dropping again the following month, so the second breach never appeared.
R2_UMBRAL_CAIDA = -0.60

# R3 (DEC-009): pct_change_prev_period >= this (a 200% jump), on the current period alone. Fires
# "Incremento Brusco" / Media.
R3_UMBRAL_INCREMENTO = 2.00

# R4/R5 (DEC-009, recalibrado 2026-07-15 -- calibración v1.1): percentile-only thresholds measured
# false in BOTH directions against this dataset's ~10-member cohorts (DEC-008): the cohort MAXIMUM
# always lands at percentile_peer = 1.0 (inclusive definition, §7) regardless of whether its
# consumption is genuinely anomalous, and R4's original 1%/99% pair is structurally unreachable
# below a 100-member cohort (module docstring, AI_ENGINE_SPEC.md §8.2). Both rules now ALSO
# require `peer_ratio` (F13, kwh_actual / cohort median) to confirm the magnitude -- a percentile
# extreme with a peer_ratio close to 1 (merely the highest/lowest of a small, similar group) no
# longer fires; null in EITHER component means the rule is not evaluable.
R4_PERCENTIL_BAJO = 0.05
R4_PEER_RATIO_BAJO = 0.4
R5_PERCENTIL_ALTO = 0.95
R5_PEER_RATIO_ALTO = 2.5

# R6 (DEC-009): |deviation_from_baseline| >= this many standard deviations. Fires "Desvío
# Estadístico" / Media.
R6_UMBRAL_DESVIO = 3

# R1's "suministro activo" condition (`suministros.estado`, `docker/postgres/init/01_schema.sql`):
# the DDL deliberately does NOT enumerate an estado value list (unlike `clientes.estado`) -- exact
# string match against the seeded default is the only contract that exists today.
ESTADO_SUMINISTRO_ACTIVO = "Activo"


@dataclass(frozen=True, slots=True)
class ReglaHit:
    """One rule firing for one suministro. `regla` is the stable id (`"R1"`..`"R6"`) RN-012
    explicability traces to; `tipo`/`severidad` are the EXACT literals the `anomalias` DDL CHECK
    constraints list (`docker/postgres/init/01_schema.sql`); `descripcion` is a human-readable,
    neutral-Spanish sentence built from the ACTUAL numbers that fired this hit (RN-012)."""

    regla: str
    tipo: str
    severidad: str
    descripcion: str


def _r1_persistencia_anomala(
    vector: FeatureVector, metadata: SuministroMetadataRow
) -> ReglaHit | None:
    """R1 (AI_ENGINE_SPEC.md §8): `zero_consumption_streak >= R1_UMBRAL_RACHA` AND the suministro
    is `Activo` -- a long-running zero on a suministro nobody bills anymore (`Baja`, or any other
    non-`Activo` estado) is expected, not anomalous."""
    racha = vector.features.get("zero_consumption_streak")
    if not isinstance(racha, int) or racha < R1_UMBRAL_RACHA:
        return None
    if metadata.estado != ESTADO_SUMINISTRO_ACTIVO:
        return None
    return ReglaHit(
        regla="R1",
        tipo="Persistencia Anómala",
        severidad="Alta",
        descripcion=(
            f"R1: racha de {racha} períodos consecutivos con consumo cero (umbral "
            f"{R1_UMBRAL_RACHA}), suministro en estado {metadata.estado}"
        ),
    )


def _r2_caida_brusca(vector: FeatureVector) -> ReglaHit | None:
    """R2 (AI_ENGINE_SPEC.md §8, recalibrado 2026-07-15 -- calibración v1.1, evidencia sintética):
    `pct_change_prev_period <= R2_UMBRAL_CAIDA` on the CURRENT period ALONE (the vector's own F5
    value, never recomputed) -- a single-period cliff is enough, exactly like R3 for the opposite
    direction. No longer requires the previous period to also breach ("sostenida >= 2 períodos");
    that condition measured 0 firings against the planted `sudden_drop` anomaly (module docstring)
    because a real cliff HOLDS at its new low level rather than dropping again the month after.
    The persisting-low-level intent is served by R6 (`deviation_from_baseline`) instead."""
    pct_actual = vector.features.get("pct_change_prev_period")
    if not isinstance(pct_actual, int | float) or pct_actual > R2_UMBRAL_CAIDA:
        return None
    return ReglaHit(
        regla="R2",
        tipo="Caída Brusca",
        severidad="Alta",
        descripcion=(
            f"R2: caída de {pct_actual:.0%} respecto del período anterior, umbral "
            f"{R2_UMBRAL_CAIDA:.0%}"
        ),
    )


def _r3_incremento_brusco(vector: FeatureVector) -> ReglaHit | None:
    """R3 (AI_ENGINE_SPEC.md §8): `pct_change_prev_period >= R3_UMBRAL_INCREMENTO` on the current
    period alone -- unlike R2, a single-period spike is enough."""
    pct = vector.features.get("pct_change_prev_period")
    if not isinstance(pct, int | float) or pct < R3_UMBRAL_INCREMENTO:
        return None
    return ReglaHit(
        regla="R3",
        tipo="Incremento Brusco",
        severidad="Media",
        descripcion=(
            f"R3: incremento de {pct:+.0%} respecto del período anterior, umbral "
            f"{R3_UMBRAL_INCREMENTO:+.0%}"
        ),
    )


def _r4_consumo_muy_bajo(vector: FeatureVector) -> ReglaHit | None:
    """R4 (AI_ENGINE_SPEC.md §8, recalibrado 2026-07-15 -- calibración v1.1): `percentile_peer <=
    R4_PERCENTIL_BAJO` AND `peer_ratio <= R4_PEER_RATIO_BAJO` -- BOTH must breach (constants'
    comment explains why percentile alone is not enough: the cohort minimum always lands at
    percentile_peer's low end regardless of whether it is genuinely anomalous). Null in EITHER
    component (small cohort, DEC-008; or peer_ratio's own null case, `domain/features.py`) means
    the rule is not evaluable -- no hit, never a false negative treated as a pass."""
    percentil = vector.features.get("percentile_peer")
    peer_ratio = vector.features.get("peer_ratio")
    if not isinstance(percentil, int | float) or not isinstance(peer_ratio, int | float):
        return None
    if percentil > R4_PERCENTIL_BAJO or peer_ratio > R4_PEER_RATIO_BAJO:
        return None
    return ReglaHit(
        regla="R4",
        tipo="Consumo Muy Bajo",
        severidad="Media",
        descripcion=(
            f"R4: percentil {percentil:.0%} de su cohorte (umbral {R4_PERCENTIL_BAJO:.0%}) y "
            f"peer_ratio {peer_ratio:.2f} respecto de la mediana (umbral "
            f"{R4_PEER_RATIO_BAJO:.2f})"
        ),
    )


def _r5_consumo_muy_alto(vector: FeatureVector) -> ReglaHit | None:
    """R5 (AI_ENGINE_SPEC.md §8, recalibrado 2026-07-15 -- calibración v1.1): `percentile_peer >=
    R5_PERCENTIL_ALTO` AND `peer_ratio >= R5_PEER_RATIO_ALTO`, symmetric to R4."""
    percentil = vector.features.get("percentile_peer")
    peer_ratio = vector.features.get("peer_ratio")
    if not isinstance(percentil, int | float) or not isinstance(peer_ratio, int | float):
        return None
    if percentil < R5_PERCENTIL_ALTO or peer_ratio < R5_PEER_RATIO_ALTO:
        return None
    return ReglaHit(
        regla="R5",
        tipo="Consumo Muy Alto",
        severidad="Media",
        descripcion=(
            f"R5: percentil {percentil:.0%} de su cohorte (umbral {R5_PERCENTIL_ALTO:.0%}) y "
            f"peer_ratio {peer_ratio:.2f} respecto de la mediana (umbral "
            f"{R5_PEER_RATIO_ALTO:.2f})"
        ),
    )


def _r6_desvio_estadistico(vector: FeatureVector) -> ReglaHit | None:
    """R6 (AI_ENGINE_SPEC.md §8): `|deviation_from_baseline| >= R6_UMBRAL_DESVIO`. Null (F4/F9's
    own cold-start null, `domain/features.py`) means not evaluable -- no hit."""
    desvio = vector.features.get("deviation_from_baseline")
    if not isinstance(desvio, int | float) or abs(desvio) < R6_UMBRAL_DESVIO:
        return None
    return ReglaHit(
        regla="R6",
        tipo="Desvío Estadístico",
        severidad="Media",
        descripcion=(
            f"R6: desviación de {desvio:+.2f} desvíos estándar respecto de su línea base, "
            f"umbral ±{R6_UMBRAL_DESVIO}"
        ),
    )


def evaluar_reglas(
    vector: FeatureVector,
    historia_filtrada: Sequence[FeatureConsumoRow],
    metadata: SuministroMetadataRow,
) -> tuple[ReglaHit, ...]:
    """Evaluate all six rules for ONE suministro's ALREADY-BUILT `vector` (Etapa 3-4) -- pure,
    no I/O, no feature recomputation. `historia_filtrada` is this suministro's FILTERED kwh
    history (same source as F5, `domain/features.py`'s module docstring) -- kept as a parameter
    for signature stability with `ProcesarLote._evaluar_reglas`'s existing call site, but no rule
    reads it as of the 2026-07-15 recalibration (`_r2_caida_brusca`'s docstring): R2 no longer
    needs a second period of history now that it fires on a single-period cliff. Returns every
    rule that fired, in `R1`..`R6` order -- independent, no dedup (module docstring)."""
    del historia_filtrada
    hits = (
        _r1_persistencia_anomala(vector, metadata),
        _r2_caida_brusca(vector),
        _r3_incremento_brusco(vector),
        _r4_consumo_muy_bajo(vector),
        _r5_consumo_muy_alto(vector),
        _r6_desvio_estadistico(vector),
    )
    return tuple(hit for hit in hits if hit is not None)


@dataclass(frozen=True, slots=True)
class ReglasSuministro:
    """One suministro's rule-evaluation outcome for this lote -- `hits` may be empty (no rule
    fired); `construir_informe_reglas` is what decides whether an entry with no hits is worth
    reporting (it is not, mission: "bounded... this IS the actionable output")."""

    suministro_id: UUID
    numero_suministro: str
    hits: tuple[ReglaHit, ...]


@dataclass(frozen=True, slots=True)
class ResumenReglas:
    """Etapa 5's counts-only summary -- mirrors `domain/features.py`'s `ResumenFeatures` (counts,
    never the full per-suministro detail) for the informe's summary section."""

    suministros_evaluados: int
    suministros_con_hits: int
    hits_por_regla: dict[str, int]


@dataclass(frozen=True, slots=True)
class InformeReglas:
    """Etapa 5's full report for one lote: the counts-only `resumen` plus the bounded,
    actionable per-suministro detail (`suministros` -- only suministros with at least one hit,
    see `construir_informe_reglas`). Unlike Etapas 3-4's `feature_vectors`, there is no separate
    persisted table this detail also lives in (module docstring's architecture decision) -- this
    `InformeReglas` IS the only place a rule hit is visible until Etapa 7 persists `anomalias`."""

    lote_id: UUID
    resumen: ResumenReglas
    suministros: tuple[ReglasSuministro, ...]


def construir_informe_reglas(
    lote_id: UUID, entradas: Sequence[ReglasSuministro], *, suministros_evaluados: int
) -> InformeReglas:
    """Assemble Etapa 5's full report: `entradas` is expected to carry ONE `ReglasSuministro` per
    suministro evaluated this run (including suministros with ZERO hits, so `hits_por_regla`/
    `suministros_con_hits` can be counted correctly against `suministros_evaluados`) -- only the
    ones with at least one hit are kept in `InformeReglas.suministros` (mission: bounded,
    actionable output; a suministro with no hits carries nothing an inspector needs to see)."""
    hits_por_regla: dict[str, int] = {f"R{numero}": 0 for numero in range(1, 7)}
    con_hits = tuple(entrada for entrada in entradas if entrada.hits)
    for entrada in con_hits:
        for hit in entrada.hits:
            hits_por_regla[hit.regla] += 1

    resumen = ResumenReglas(
        suministros_evaluados=suministros_evaluados,
        suministros_con_hits=len(con_hits),
        hits_por_regla=hits_por_regla,
    )
    return InformeReglas(lote_id=lote_id, resumen=resumen, suministros=con_hits)
