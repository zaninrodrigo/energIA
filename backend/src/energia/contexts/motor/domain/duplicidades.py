"""Etapa 2 — Detección de duplicidades (US-007, AI_ENGINE_SPEC.md §5).

Each `detectar_*` function is pure: it takes rows already fetched by
`infrastructure/duplicidades_data_source.py` (`PeriodoHistorialRow`/`LecturaHistorialRow`/
`LoteConteoRow`, `domain/ports.py`) and returns the marks/findings it detects, with no I/O of its
own -- mirrors `domain/checks.py`'s split between pure detection and set-based SQL fetching.

**Scope: which suministros get marks -- deliberate departure from the mission's implementation
note.** `periodos_conflictivos`/`lecturas_near_duplicate` are computed for EVERY suministro with
an active consumo in the CURRENT lote, regardless of whether Etapa 1 excluded it -- `ProcesarLote`
(application layer) does NOT filter the fetched rows by `informe.exclusiones` before calling
`detectar_periodos_conflictivos`/`detectar_lecturas_near_duplicate` here. This is the opposite of
a "compute for non-excluded only" reading, for a concrete reason: V5 (Etapa 1) and
`detectar_periodos_conflictivos` (Etapa 2) detect the SAME underlying phenomenon (overlapping
periods) at two different scopes -- V5 excludes the suministro from THIS lote's 95% scoring gate
the moment the overlap touches the current lote's own data; Etapa 2's mark is the durable,
cross-lote annotation that must "survive for feature windows of FUTURE lotes" (mission), i.e. for
a LATER lote where this same suministro is NOT excluded. If Etapa 2 skipped suministros V5 just
excluded, the exact case that motivates Etapa 2's existence -- a suministro excluded THIS run for
the very overlap Etapa 3 needs marked -- would never get marked at all, and the mark would be lost
permanently (nothing re-computes it once this lote finishes processing). `detectar_drift_lotes` is
unaffected either way: it reports on OTHER lotes' own bookkeeping integrity, not on marks for THIS
lote's suministros, so it is computed over every suministro of the current lote unconditionally
(see that function's docstring) -- exclusion never entered into its scope to begin with.

**Why "consumo repetido entre lotes" cannot mean an exact repeat surviving under two lotes.**
AI_ENGINE_SPEC.md §5 originally defined this duplicate type as "the same period re-imported
under a different lote_id" -- structurally impossible today: `consumos`' natural-key upsert
(`uq_consumos_suministro_periodo` on `(suministro_id, fecha_inicio, fecha_fin) WHERE deleted_at
IS NULL`, `contexts.consumos.infrastructure.consumo_repository.SqlAlchemyConsumoRepository.save`)
makes a re-import of the SAME period UPDATE the existing row in place -- including its `lote_id`,
which migrates to whichever lote re-imported it. There is never a moment where the same period
exists twice, once per lote; there is only ever one row, whose `lote_id` column changed. The
DETECTABLE residue of that migration is `detectar_drift_lotes` below: the OTHER lote (the one the
row used to belong to) now has fewer active consumos than its own `cantidad_registros` declares
(or, symmetrically, gains rows if history migrates INTO it instead) -- see AI_ENGINE_SPEC.md §5's
amended table row and `docs/03-architecture/API_SPEC.md`'s motor section for the full write-up.
This is best-effort, not exhaustive: `infrastructure/duplicidades_data_source.py`'s
`fetch_conteos_lotes_relacionados` can only discover an OTHER lote through a suministro it still
CURRENTLY shares with the lote being processed -- a lote that loses its ENTIRE shared link (every
one of its periods for those suministros migrated away, with no trace left behind, since the
upsert never soft-deletes) becomes undiscoverable this way. See that method's own docstring.

**DEC-005 holds unchanged** (AI_ENGINE_SPEC.md §5/§15): every function below only ANNOTATES --
none of them delete or modify `consumos`/`lecturas` rows (the motor never writes to those tables,
§1.2). `periodos_conflictivos` is the one shape Etapa 3 (§6, feature windows) must actually READ
to skip a conflicting period so it is not double-counted; `lecturas_near_duplicate`/`drift_lotes`
are informational only, not consumed by any later stage yet.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from energia.contexts.motor.domain.ports import (
    LecturaHistorialRow,
    LoteConteoRow,
    PeriodoHistorialRow,
)

# Near-duplicate lecturas (AI_ENGINE_SPEC.md §5): same suministro, `fecha_lectura` within this
# many days of each other, identical `lectura_actual`. An implementation-level constant (not one
# of AI_ENGINE_SPEC.md's DEC-0xx decisions), calibration pending against real data
# (PROJECT_MASTER_SPEC.md #8) -- the same caveat `domain/checks.py`'s `TOLERANCIA_KWH_LECTURA`/
# `_UN_DIA` document: a placeholder candidate, not a validated one.
VENTANA_DIAS_LECTURA_NEAR_DUPLICATE = 3


@dataclass(frozen=True, slots=True)
class PeriodoConflictivo:
    """One period of THIS suministro (`consumo_id`/`fecha_inicio`/`fecha_fin`/`lote_id`) that
    overlaps -- without being identical, DEC-005 -- another period of the SAME suministro,
    possibly from a DIFFERENT lote (`conflicto_con_*`). This is the stable mark Etapa 3
    (AI_ENGINE_SPEC.md §6, feature windows) must consume: a window that iterates a suministro's
    periods needs to know THIS one is conflictive so it can skip it (or otherwise avoid
    double-counting the same real-world consumption reported twice under two overlapping
    periods) -- see `PeriodosConflictivosSuministro`'s docstring for the per-suministro shape.
    """

    consumo_id: UUID
    fecha_inicio: date
    fecha_fin: date
    lote_id: UUID
    conflicto_con_consumo_id: UUID
    conflicto_con_fecha_inicio: date
    conflicto_con_fecha_fin: date
    conflicto_con_lote_id: UUID


@dataclass(frozen=True, slots=True)
class PeriodosConflictivosSuministro:
    """Etapa 2's per-suministro mark shape (AI_ENGINE_SPEC.md §5 DEC-005; §6 windows consume
    this) -- mirrors `informe_validacion.ExclusionSuministro`'s "one entry per affected
    suministro, every finding kept" shape, so a consumer that already knows how to look up an
    `ExclusionSuministro` by `suministro_id` can look up this the same way.
    """

    suministro_id: UUID
    periodos: tuple[PeriodoConflictivo, ...]


@dataclass(frozen=True, slots=True)
class LecturaNearDuplicate:
    """One pair of near-duplicate lecturas of the same suministro (AI_ENGINE_SPEC.md §5) --
    informational only (DEC-005: annotate), not consumed by any later stage."""

    suministro_id: UUID
    lectura_id: UUID
    fecha_lectura: date
    lectura_actual: Decimal
    conflicto_con_lectura_id: UUID
    conflicto_con_fecha_lectura: date


@dataclass(frozen=True, slots=True)
class DriftLote:
    """One OTHER lote (not the one being processed) whose current active `consumos` count no
    longer matches its own declared `cantidad_registros` -- the detectable residue of a
    cross-lote period migration (see module docstring). Informational only: this implementation
    does not change `lote_id`'s own `estado` (DEC-005: annotate, never delete/reprocess)."""

    lote_id: UUID
    codigo_lote: str
    cantidad_registros: int
    consumos_activos: int

    @property
    def diferencia(self) -> int:
        """`consumos_activos - cantidad_registros`: positive means rows migrated IN (from some
        other lote's re-import), negative means rows migrated OUT (re-imported under a
        different lote)."""
        return self.consumos_activos - self.cantidad_registros


@dataclass(frozen=True, slots=True)
class InformeDuplicidades:
    """Etapa 2's full duplicidades report for one lote (AI_ENGINE_SPEC.md §5)."""

    lote_id: UUID
    periodos_conflictivos: tuple[PeriodosConflictivosSuministro, ...]
    lecturas_near_duplicate: tuple[LecturaNearDuplicate, ...]
    drift_lotes: tuple[DriftLote, ...]


def detectar_periodos_conflictivos(
    filas: list[PeriodoHistorialRow],
) -> tuple[PeriodosConflictivosSuministro, ...]:
    """Flag every PAIR of overlapping (not identical -- structurally impossible, module
    docstring) periods across a suministro's FULL history, via a sorted sweep per suministro
    (FIX 1, reviewer finding) -- NOT a `LAG`-adjacency comparison against only the immediate
    predecessor, which misses overlaps between non-adjacent periods: a long period (e.g.
    2024-01-01 to 2024-12-31) overlapping two shorter, mutually disjoint periods elsewhere in the
    same year would only ever be paired with the first one under LAG; the second overlap would be
    silently missed.

    Algorithm: group `filas` by `suministro_id`, sort each group by `fecha_inicio` (ties broken by
    `fecha_fin`), then for every period scan FORWARD while the next period's `fecha_inicio <=`
    this period's `fecha_fin` -- the exact same boundary condition Etapa 1's V5 uses
    (`fecha_inicio <= prev_fecha_fin`), generalized here to every pair, not just adjacent ones.
    Sorted order lets the scan stop (`break`) at the first non-overlapping period: since
    `fecha_inicio` is non-decreasing, every period after that one also starts after the current
    period ends, so it cannot overlap either. This makes the whole suministro group
    O(n log n + pairs) -- sort once, then only touch pairs that actually overlap -- fine at the
    expected volumes (~36 periods/suministro/year, module docstring).

    BOTH directions of a detected pair are recorded (this period marked against the other, AND the
    other marked against this period) so a consumer that only has one side's `consumo_id` at hand
    still finds its mark, without having to search every entry's `conflicto_con_consumo_id` too.
    """
    filas_por_suministro: dict[UUID, list[PeriodoHistorialRow]] = defaultdict(list)
    for fila in filas:
        filas_por_suministro[fila.suministro_id].append(fila)

    marcas_por_suministro: dict[UUID, list[PeriodoConflictivo]] = defaultdict(list)
    for suministro_id, periodos in filas_por_suministro.items():
        ordenados = sorted(periodos, key=lambda p: (p.fecha_inicio, p.fecha_fin))
        for indice, actual in enumerate(ordenados):
            for siguiente in ordenados[indice + 1 :]:
                if siguiente.fecha_inicio > actual.fecha_fin:
                    break
                marcas_por_suministro[suministro_id].append(
                    PeriodoConflictivo(
                        consumo_id=actual.consumo_id,
                        fecha_inicio=actual.fecha_inicio,
                        fecha_fin=actual.fecha_fin,
                        lote_id=actual.lote_id,
                        conflicto_con_consumo_id=siguiente.consumo_id,
                        conflicto_con_fecha_inicio=siguiente.fecha_inicio,
                        conflicto_con_fecha_fin=siguiente.fecha_fin,
                        conflicto_con_lote_id=siguiente.lote_id,
                    )
                )
                marcas_por_suministro[suministro_id].append(
                    PeriodoConflictivo(
                        consumo_id=siguiente.consumo_id,
                        fecha_inicio=siguiente.fecha_inicio,
                        fecha_fin=siguiente.fecha_fin,
                        lote_id=siguiente.lote_id,
                        conflicto_con_consumo_id=actual.consumo_id,
                        conflicto_con_fecha_inicio=actual.fecha_inicio,
                        conflicto_con_fecha_fin=actual.fecha_fin,
                        conflicto_con_lote_id=actual.lote_id,
                    )
                )

    return tuple(
        PeriodosConflictivosSuministro(suministro_id=suministro_id, periodos=tuple(periodos))
        for suministro_id, periodos in marcas_por_suministro.items()
    )


def detectar_lecturas_near_duplicate(
    filas: list[LecturaHistorialRow],
) -> tuple[LecturaNearDuplicate, ...]:
    """Flag every PAIR of lecturas of the same suministro within
    `VENTANA_DIAS_LECTURA_NEAR_DUPLICATE` days of each other AND with an identical
    `lectura_actual` -- AI_ENGINE_SPEC.md §5's "near-duplicate de lecturas" -- via a sorted sweep
    per suministro (FIX 1, reviewer finding), NOT a `LAG`-adjacency comparison against only the
    immediate predecessor: an interleaved lectura with a DIFFERENT `lectura_actual` sitting
    between two genuine near-duplicates would otherwise break the LAG chain and hide the pair
    (e.g. lecturas at day 0 and day 2 with the SAME value, separated by an unrelated lectura at
    day 1 with a DIFFERENT value).

    Algorithm: group `filas` by `suministro_id`, sort each group by `fecha_lectura` (ties broken
    by `lectura_id`), then for every lectura scan FORWARD while the next lectura's
    `fecha_lectura` is within `VENTANA_DIAS_LECTURA_NEAR_DUPLICATE` days -- emitting a pair
    whenever `lectura_actual` also matches, but continuing the scan (not stopping) past a
    non-matching value still inside the window. Sorted order lets the scan stop (`break`) once a
    later lectura falls outside the window: since `fecha_lectura` is non-decreasing, every lectura
    after that one is at least as far away, so it cannot be near-duplicate either -- the same
    O(n log n + pairs) shape `detectar_periodos_conflictivos` uses. Only ONE direction is recorded
    per pair (the later lectura marked against the earlier one, `PeriodoConflictivo`'s BOTH-
    directions choice does not apply here -- this mirrors the original single-direction shape,
    informational only per DEC-005).
    """
    filas_por_suministro: dict[UUID, list[LecturaHistorialRow]] = defaultdict(list)
    for fila in filas:
        filas_por_suministro[fila.suministro_id].append(fila)

    hallazgos = []
    for lecturas in filas_por_suministro.values():
        ordenadas = sorted(
            lecturas, key=lambda lectura: (lectura.fecha_lectura, lectura.lectura_id)
        )
        for indice, actual in enumerate(ordenadas):
            for siguiente in ordenadas[indice + 1 :]:
                dias = (siguiente.fecha_lectura - actual.fecha_lectura).days
                if dias > VENTANA_DIAS_LECTURA_NEAR_DUPLICATE:
                    break
                if siguiente.lectura_actual != actual.lectura_actual:
                    continue
                hallazgos.append(
                    LecturaNearDuplicate(
                        suministro_id=siguiente.suministro_id,
                        lectura_id=siguiente.lectura_id,
                        fecha_lectura=siguiente.fecha_lectura,
                        lectura_actual=siguiente.lectura_actual,
                        conflicto_con_lectura_id=actual.lectura_id,
                        conflicto_con_fecha_lectura=actual.fecha_lectura,
                    )
                )
    return tuple(hallazgos)


def detectar_drift_lotes(filas: list[LoteConteoRow]) -> tuple[DriftLote, ...]:
    """Flag every OTHER lote whose current active `consumos` count no longer matches its own
    declared `cantidad_registros` -- see `DriftLote`'s docstring for what the mismatch means.

    FIX 2 (reviewer finding): lotes with `cantidad_registros == 0` are SKIPPED here, never
    flagged. `cantidad_registros == 0` is the legitimate "count not declared yet" default
    (`docker/postgres/init/01_schema.sql`'s column default; AI_ENGINE_SPEC.md §2.1's completeness
    gate treats it the same way for the lote BEING processed: never "ready", regardless of its
    actual `consumos` count) -- such a lote never declared a count to drift FROM, so comparing its
    real `consumos_activos` against a `0` it never meant as a real declaration would flag every
    lote that simply has not gotten around to declaring its count yet, drowning genuine drift in
    false positives.
    """
    return tuple(
        DriftLote(
            lote_id=fila.lote_id,
            codigo_lote=fila.codigo_lote,
            cantidad_registros=fila.cantidad_registros,
            consumos_activos=fila.consumos_activos,
        )
        for fila in filas
        if fila.cantidad_registros != 0 and fila.consumos_activos != fila.cantidad_registros
    )


def construir_informe_duplicidades(
    lote_id: UUID,
    *,
    periodos: list[PeriodoHistorialRow],
    lecturas: list[LecturaHistorialRow],
    conteos_lotes: list[LoteConteoRow],
) -> InformeDuplicidades:
    """Run every Etapa 2 detector and assemble the full `InformeDuplicidades` -- the single entry
    point `ProcesarLote` (application layer) calls once the relevant history has been fetched."""
    return InformeDuplicidades(
        lote_id=lote_id,
        periodos_conflictivos=detectar_periodos_conflictivos(periodos),
        lecturas_near_duplicate=detectar_lecturas_near_duplicate(lecturas),
        drift_lotes=detectar_drift_lotes(conteos_lotes),
    )
