"""Read side of Etapas 7-8's convergence (`resultados_ia` + `ire` + `anomalias` +
`impacto_economico`) -- feeds `GET /api/v1/motor/lotes/{codigo_lote}/resultados`, the IRE ranking
of a processed lote (the dashboard's data source, RN-009: "las inspecciones deberán priorizarse
según el IRE y el Impacto Económico Estimado", `BUSINESS_ANALYSIS.md` §15).

Deliberately its own module, NOT `domain/ports.py` (where every other port in this context
lives): `ports.py` is imported by `domain/duplicidades.py` -> `domain/features.py` ->
`domain/ire.py` -- a `ResultadoRankingRow` needs `FactorContribucion` (`domain/ire.py`), so adding
it to `ports.py` would close that chain into a circular import (`ports` -> `ire` -> `features` ->
`ports`). This module sits one level away from that cycle: it imports FROM `domain/ire.py`, and
nothing in `domain/ire.py`'s own import chain ever imports this module back.

`Suministro`/`CategoriaTarifaria` display fields (`numero_suministro`, `localidad`, `categoria_
tarifaria`) are joined in here purely for display -- the same "motor never imports another
context's domain/repository, its infrastructure runs direct SQL instead" shortcut `domain/
ports.py`'s module docstring documents for the write side and `LoteProcesamientoPort`.
"""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from energia.contexts.motor.domain.ire import FactorContribucion

__all__ = [
    "AnomaliaRankingRow",
    "ResultadoRankingRow",
    "BarrioRiesgoRow",
    "ResumenRanking",
    "ResultadosRankingRepository",
]


@dataclass(frozen=True, slots=True)
class AnomaliaRankingRow:
    """One `anomalias` entry attached to a `ResultadoRankingRow` -- same three display fields as
    `domain.ports.AnomaliaParaGuardar` (the write side of this same table), read back instead of
    written."""

    tipo: str
    severidad: str
    descripcion: str | None


@dataclass(frozen=True, slots=True)
class ResultadoRankingRow:
    """One suministro's row in a processed lote's IRE ranking. `observaciones` is DEC-016's
    explicability breakdown, already PARSED from `resultados_ia.observaciones`'s stored JSON text
    into `FactorContribucion` entries (`domain/ire.py`, the exact shape `ComposicionIRE.factores`
    serializes) -- never the raw text; a malformed/unparseable value defensively yields `()`
    rather than raising (this is a READ path over already-persisted data: the malformed case
    should never happen in practice, but a corrupt row must not 500 the whole ranking).
    `score_anomalia`/`probabilidad` are `None` only if `resultados_ia`'s own columns are (never
    written NULL by `ResultadosIaRepository.persistir` today, but the DDL allows it -- see
    `docker/postgres/init/01_schema.sql`). `iee_kwh` is `None` when the suministro has no
    `impacto_economico` row (cold-start, `domain/ire.py`'s `calcular_iee_kwh` returning `None` at
    Etapa 7-8 persistence time), never a fabricated `0.0`."""

    suministro_id: UUID
    numero_suministro: str
    # rutafolio: 11-digit meter identifier ("el DNI del medidor", docker/postgres/init/
    # 01_schema.sql); latitud/longitud: geo-reference for plotting anomalies on a map. All
    # nullable: legacy/historical meters may not carry them yet.
    rutafolio: str | None
    latitud: float | None
    longitud: float | None
    ire_valor: int
    ire_nivel: str
    clasificacion: str
    score_anomalia: float | None
    probabilidad: float | None
    localidad: str | None
    categoria_tarifaria: str
    anomalias: tuple[AnomaliaRankingRow, ...]
    observaciones: tuple[FactorContribucion, ...]
    iee_kwh: float | None


@dataclass(frozen=True, slots=True)
class BarrioRiesgoRow:
    """A processed lote's risk aggregated to one (localidad, barrio) -- feeds the barrio-level heat
    view (`GET .../barrios`): "which neighbourhoods concentrate the anomalous-consumption risk".
    `ire_promedio` is the mean IRE of the barrio's analyzed meters; `ire_maximo` is its highest
    single IRE -- and `nivel` bands `ire_maximo` (NOT the mean), because "does this barrio have
    potential for anomalous consumption" is about its WORST meter, not its average: one Crítico
    meter among many calm ones must not be diluted to green. Both are on the 0-100 `ire.valor`
    scale; `nivel` uses the SAME 5 bands as `ire.nivel`.
    `latitud`/`longitud` are the barrio's centroid (mean of its meters' coordinates), for placing it
    on the map -- `None` when none of the barrio's meters are georeferenced. `barrio` is `None` for
    meters whose barrio was never relevado (grouped together as one "sin barrio" bucket)."""

    localidad: str | None
    barrio: str | None
    total_medidores: int
    ire_promedio: int
    ire_maximo: int
    nivel: str
    con_anomalias: int
    latitud: float | None
    longitud: float | None


@dataclass(frozen=True, slots=True)
class ResumenRanking:
    """Whole-lote summary counts (the dashboard's summary cards) -- deliberately UNFILTERED by
    `?nivel=`/`?clasificacion=`, the same "KPI cards don't move when you filter the table"
    convention most dashboards use: a caller filtering the ranking to `?nivel=Alto` still sees the
    lote's TOTAL counts here, not the filtered subset's. `total_resultados` is named differently
    from the ranking response's own (filtered) `total` precisely to avoid that ambiguity in the
    same JSON payload."""

    total_resultados: int
    conteo_por_nivel: dict[str, int]
    conteo_por_clasificacion: dict[str, int]
    con_anomalias: int
    suma_iee_kwh: float


class ResultadosRankingRepository(Protocol):
    """Same-context port: the READ side of Etapas 7-8's convergence (`resultados_ia` + `ire` +
    `anomalias` + `impacto_economico`, joined to `suministros`/`categorias_tarifarias` for display
    fields) -- feeds `GET /api/v1/motor/lotes/{codigo_lote}/resultados`. See `infrastructure/
    resultados_ranking_repository.py` for the set-based SQL (one query with joins + JSON
    aggregation for `anomalias`, never N+1)."""

    async def listar(
        self,
        lote_id: UUID,
        *,
        limit: int,
        offset: int,
        nivel: str | None,
        clasificacion: str | None,
    ) -> list[ResultadoRankingRow]:
        """Return one page of this lote's ranking, ordered by `ire.valor` DESCENDING (RN-009:
        highest risk first), `numero_suministro` ascending as a deterministic tie-break.
        Optionally filtered to a single `nivel`/`clasificacion`. Excludes every soft-deleted row
        across all joined tables (`resultados_ia`, `suministros`, `categorias_tarifarias`, `ire`,
        `impacto_economico`, `anomalias`)."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def contar(self, lote_id: UUID, *, nivel: str | None, clasificacion: str | None) -> int:
        """Count this lote's ranking rows matching the same filters `listar` accepts -- feeds the
        response's (filtered) pagination `total`."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def resumen(self, lote_id: UUID) -> ResumenRanking:
        """Whole-lote summary counts, UNFILTERED (see `ResumenRanking`'s docstring). Returns a
        `ResumenRanking` with every count at `0` (`conteo_por_nivel`/`conteo_por_clasificacion`
        still keyed by all 5/4 bands) -- never raises -- when the lote has no `resultados_ia` rows
        at all (imported but not yet processed, or processed before Etapa 7 existed)."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def barrios(self, lote_id: UUID) -> list[BarrioRiesgoRow]:
        """This lote's risk aggregated per (localidad, barrio), ordered by `ire_promedio`
        descending (highest-risk barrios first). Empty list when the lote has no `resultados_ia`
        rows yet. Excludes every soft-deleted row across the joined tables."""
        ...  # pragma: no cover — Protocol stub, never executed directly
