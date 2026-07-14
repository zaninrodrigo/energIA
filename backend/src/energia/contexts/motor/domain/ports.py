"""Ports the `motor` application layer depends on (ADR-001 Dependency Inversion; ADR-006
modular-monolith cross-context lookups).

Both ports below are cross-context by construction: `Lote`/`Consumo`/`Lectura`/`Suministro`/
`CategoriaTarifaria` all belong to OTHER bounded contexts (`consumos`, `suministros` --
DOMAIN_MODEL.md §4.2/§4.3), not to `motor` (§4.4). Per `contexts/README.md`'s cross-context
directory-port pattern, `motor` never imports anything from `contexts.consumos`/
`contexts.suministros`; its infrastructure implementations run direct SQL (`sqlalchemy.text`)
against those tables instead -- the same sanctioned modular-monolith shortcut
`SqlDirectSuministroDirectory`/`SqlDirectClienteDirectory` already establish, extended here to a
port that also WRITES (`transicionar_estado`), not just resolves a natural key -- see
`contexts/README.md` ("El motor: puerto de escritura cruzada") for why that is a deliberate,
narrow extension of the pattern rather than a departure from it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from energia.contexts.motor.domain.lote_estado import EstadoLote

__all__ = [
    "EstadoLoteActual",
    "LoteProcesamientoPort",
    "ConsumoValidacionRow",
    "ValidacionDataSource",
    "PeriodoHistorialRow",
    "LecturaHistorialRow",
    "LoteConteoRow",
    "DuplicidadesDataSource",
]


@dataclass(frozen=True, slots=True)
class EstadoLoteActual:
    """A snapshot of one lote's processing-relevant fields, as read by `LoteProcesamientoPort`."""

    id: UUID
    codigo_lote: str
    estado: EstadoLote
    cantidad_registros: int


class LoteProcesamientoPort(Protocol):
    """Cross-context port: read + optimistically write `lotes.estado`, without `motor` importing
    `contexts.consumos.domain.lote.Lote` or its repository. `Lote`'s own state machine
    (`ALLOWED_TRANSITIONS`) lives in `consumos/domain/lote.py`; `motor`'s mirror
    (`domain/lote_estado.py`) is the pre-flight check `ProcesarLote` runs before ever calling
    `transicionar_estado` here -- the SQL `WHERE estado IN (...)` in the implementation is the
    actual optimistic-concurrency guarantee, not just a repeat of that pre-flight check.
    """

    async def obtener_estado_actual(self, codigo_lote: str) -> EstadoLoteActual | None:
        """Return the (non-soft-deleted) lote's `id`/`estado`/`cantidad_registros` for this
        `codigo_lote`, or `None` if no such lote exists."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def contar_consumos_activos(self, lote_id: UUID) -> int:
        """Count non-soft-deleted `consumos` rows for this `lote_id` -- the completeness gate's
        second operand (AI_ENGINE_SPEC.md §2.1, `domain/completitud.py`)."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def transicionar_estado(
        self, lote_id: UUID, *, desde: frozenset[EstadoLote], hacia: EstadoLote
    ) -> bool:
        """Optimistically move `lote_id` from any state in `desde` to `hacia`:
        `UPDATE lotes SET estado = hacia WHERE id = lote_id AND estado IN (desde)`. Returns
        whether a row was actually updated -- `False` means a concurrent transition already
        moved this lote out of `desde` (the "concurrent trigger race degrades to 409" case,
        AI_ENGINE_SPEC.md §2.1). Does not commit: the caller (route boundary) controls the
        transaction.
        """
        ...  # pragma: no cover — Protocol stub, never executed directly


@dataclass(frozen=True, slots=True)
class ConsumoValidacionRow:
    """One row of a lote's imported chain (`consumos` joined with `lecturas`/`suministros`/
    `categorias_tarifarias`), already shaped for the Etapa 1 checks (`domain/checks.py`) to
    evaluate directly -- no further query needed per check, per row.

    `lectura_anterior`/`lectura_actual`/`lectura_dias_facturados` are `None` whenever
    `lectura_id` is `None` (V1) OR the referenced lectura is no longer active -- both cases mean
    "no lectura data available for V2/V3", handled identically by those checks.

    `prev_fecha_inicio`/`prev_fecha_fin` are the immediately preceding period (by
    `fecha_inicio`) among ALL active consumos of this SAME suministro -- across every lote, not
    just this one (RD-017 is a suministro-level invariant, not a lote-scoped one; see
    `infrastructure/validacion_data_source.py`'s docstring for why the SQL window spans the
    suministro's full history). `None` when this is the first (or only) period on record for the
    suministro.

    `categoria_deleted_at` is the referenced `categorias_tarifarias.deleted_at` -- not `None`
    means the suministro's category has been soft-deleted, so it is no longer a valid cohort for
    comparison (RD-009, V6). `suministros.categoria_tarifaria_id` is `NOT NULL` at the schema
    level, so this -- not a missing FK -- is the only way V6 can actually fire in practice; see
    that check's docstring.
    """

    consumo_id: UUID
    suministro_id: UUID
    fecha_inicio: date
    fecha_fin: date
    dias_facturados: int
    kwh: Decimal
    lectura_id: UUID | None
    lectura_anterior: Decimal | None
    lectura_actual: Decimal | None
    lectura_dias_facturados: int | None
    prev_fecha_inicio: date | None
    prev_fecha_fin: date | None
    categoria_deleted_at: datetime | None


class ValidacionDataSource(Protocol):
    """Cross-context port: read the full imported chain (`consumos` + `lecturas` + `suministros`
    + `categorias_tarifarias`) a lote's Etapa 1 checks need, as one set-based query -- never a
    per-row loop of individual queries (see `infrastructure/validacion_data_source.py`).
    """

    async def fetch_chain(self, lote_id: UUID) -> Sequence[ConsumoValidacionRow]:
        """Return every active `consumos` row belonging to `lote_id`, already joined/enriched --
        see `ConsumoValidacionRow`'s docstring for exactly what each field means."""
        ...  # pragma: no cover — Protocol stub, never executed directly


@dataclass(frozen=True, slots=True)
class PeriodoHistorialRow:
    """One row of a suministro's FULL active consumo-period history -- every lote that ever
    touched this suministro, not just the lote currently being processed (AI_ENGINE_SPEC.md §5,
    Etapa 2 / US-007). Input to `domain.duplicidades.detectar_periodos_conflictivos`.

    PLAIN per-suministro row -- no `LAG`/`prev_*` here (FIX 1, reviewer finding): a LAG-adjacency
    shape can only ever compare a row to its immediate predecessor by `fecha_inicio`, which misses
    overlaps between NON-adjacent periods (e.g. one long period overlapping two shorter, mutually
    disjoint ones only ever gets paired with the first neighbor under LAG; the second overlap is
    silently missed). `domain.duplicidades.detectar_periodos_conflictivos` instead sorts these by
    `fecha_inicio` per suministro and sweeps forward pairwise, enumerating EVERY overlapping pair
    -- see that function's docstring for the algorithm.
    """

    consumo_id: UUID
    suministro_id: UUID
    lote_id: UUID
    fecha_inicio: date
    fecha_fin: date


@dataclass(frozen=True, slots=True)
class LecturaHistorialRow:
    """One row of a suministro's full active `lecturas` history -- input to
    `domain.duplicidades.detectar_lecturas_near_duplicate` (AI_ENGINE_SPEC.md §5, Etapa 2 /
    US-007).

    PLAIN per-suministro row -- no `LAG`/`prev_*` here, for the same non-adjacency reason
    `PeriodoHistorialRow`'s docstring documents (FIX 1): an interleaved lectura with a DIFFERENT
    `lectura_actual` would otherwise break a LAG chain between two genuine near-duplicates a few
    days apart. `domain.duplicidades.detectar_lecturas_near_duplicate` instead sorts these by
    `fecha_lectura` per suministro and sweeps forward pairwise within the near-duplicate window --
    see that function's docstring for the algorithm.
    """

    lectura_id: UUID
    suministro_id: UUID
    fecha_lectura: date
    lectura_actual: Decimal


@dataclass(frozen=True, slots=True)
class LoteConteoRow:
    """One OTHER lote (not the one being processed) that shares at least one suministro with the
    lote being processed, with its declared `cantidad_registros` vs. its CURRENT active `consumos`
    count -- input to `domain.duplicidades.detectar_drift_lotes` (AI_ENGINE_SPEC.md §5's amended
    "consumo repetido entre lotes" row: the natural-key upsert (`consumos.
    uq_consumos_suministro_periodo`) migrates a re-imported period's `lote_id` to whichever lote
    re-imported it, so the detectable residue of a cross-lote repeat is this count drifting away
    from what the OTHER lote itself declared, not a duplicate row surviving under two lotes at
    once -- see `infrastructure/duplicidades_data_source.py` for the query.
    """

    lote_id: UUID
    codigo_lote: str
    cantidad_registros: int
    consumos_activos: int


class DuplicidadesDataSource(Protocol):
    """Cross-context port: Etapa 2's set-based reads (AI_ENGINE_SPEC.md §5, US-007) -- full
    suministro-level history (periods + lecturas) and other-lote consumo counts, all scoped by
    the lote currently being processed (see `infrastructure/duplicidades_data_source.py`).
    """

    async def fetch_historial_periodos(self, lote_id: UUID) -> Sequence[PeriodoHistorialRow]:
        """Return the FULL active consumo-period history (every lote, ordered by `suministro_id`,
        `fecha_inicio`) of every suministro that has at least one active consumo in `lote_id`."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def fetch_historial_lecturas(self, lote_id: UUID) -> Sequence[LecturaHistorialRow]:
        """Return the full active `lecturas` history (ordered by `suministro_id`,
        `fecha_lectura`) of every suministro that has at least one active consumo in `lote_id`."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def fetch_conteos_lotes_relacionados(self, lote_id: UUID) -> Sequence[LoteConteoRow]:
        """Return every OTHER lote (`lote_id` excluded) that shares at least one suministro with
        `lote_id`, each with its `cantidad_registros` vs. its current active `consumos` count."""
        ...  # pragma: no cover — Protocol stub, never executed directly
