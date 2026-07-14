"""SqlDuplicidadesDataSource: `DuplicidadesDataSource`'s implementation (ADR-006 cross-context
read shortcut) -- Etapa 2's set-based reads (US-007, AI_ENGINE_SPEC.md §5).

Same discipline as `validacion_data_source.py`: one set-based SQL query per method, raw SQL
(`sqlalchemy.text`) never an ORM model, since `consumos`/`lecturas`/`lotes` belong to OTHER
bounded contexts (DOMAIN_MODEL.md §4.2/§4.3), not `motor` (§4.4).
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.motor.domain.ports import (
    LecturaHistorialRow,
    LoteConteoRow,
    PeriodoHistorialRow,
)

# Both queries below start from the same CTE `validacion_data_source.py`'s `_FETCH_CHAIN_SQL`
# uses to find "every suministro with an active consumo in this lote" -- repeated here (not
# imported/shared) because each query embeds it inline right where it is used, the same way SQL
# text constants are never composed piecewise elsewhere in this codebase.
_SUMINISTROS_DEL_LOTE_CTE = """
    suministros_del_lote AS (
        SELECT DISTINCT suministro_id
        FROM consumos
        WHERE lote_id = :lote_id AND deleted_at IS NULL
    )
"""

# FIX 1 (reviewer finding): PLAIN per-suministro rows -- no `LAG`/`prev_*` here. A LAG-adjacency
# shape can only ever compare a row to its immediate predecessor, which misses overlaps between
# NON-adjacent rows (e.g. one long consumo period overlapping two shorter, mutually disjoint ones
# is only ever paired with the first neighbor under LAG; the second overlap is silently missed --
# reproduced at both the raw-SQL and pure-function level). Complete pairwise overlap enumeration
# now happens in the DOMAIN (`domain/duplicidades.py`'s sorted sweep), not here -- this query only
# fetches the plain, ordered rows that sweep needs.
_FETCH_HISTORIAL_PERIODOS_SQL = text(
    f"""
    WITH {_SUMINISTROS_DEL_LOTE_CTE}
    SELECT
        c.id AS consumo_id,
        c.suministro_id,
        c.lote_id,
        c.fecha_inicio,
        c.fecha_fin
    FROM consumos c
    WHERE c.deleted_at IS NULL
      AND c.suministro_id IN (SELECT suministro_id FROM suministros_del_lote)
    ORDER BY c.suministro_id, c.fecha_inicio
    """
)

_FETCH_HISTORIAL_LECTURAS_SQL = text(
    f"""
    WITH {_SUMINISTROS_DEL_LOTE_CTE}
    SELECT
        lec.id AS lectura_id,
        lec.suministro_id,
        lec.fecha_lectura,
        lec.lectura_actual
    FROM lecturas lec
    WHERE lec.deleted_at IS NULL
      AND lec.suministro_id IN (SELECT suministro_id FROM suministros_del_lote)
    ORDER BY lec.suministro_id, lec.fecha_lectura
    """
)

_FETCH_CONTEOS_LOTES_RELACIONADOS_SQL = text(
    f"""
    WITH {_SUMINISTROS_DEL_LOTE_CTE},
    lotes_relacionados AS (
        SELECT DISTINCT c.lote_id
        FROM consumos c
        WHERE c.deleted_at IS NULL
          AND c.suministro_id IN (SELECT suministro_id FROM suministros_del_lote)
          AND c.lote_id != :lote_id
    )
    SELECT
        l.id AS lote_id,
        l.codigo_lote,
        l.cantidad_registros,
        COUNT(c.id) AS consumos_activos
    FROM lotes_relacionados lr
    JOIN lotes l ON l.id = lr.lote_id
    LEFT JOIN consumos c ON c.lote_id = l.id AND c.deleted_at IS NULL
    GROUP BY l.id, l.codigo_lote, l.cantidad_registros
    """
)


class SqlDuplicidadesDataSource:
    """`DuplicidadesDataSource` (domain/ports.py) backed by direct SQL queries, same session/
    transaction as the rest of the request."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def fetch_historial_periodos(self, lote_id: UUID) -> Sequence[PeriodoHistorialRow]:
        """Full active consumo-period history (every lote) of every suministro with at least one
        active consumo in `lote_id` -- NEVER filtered back down to `lote_id` (unlike
        `SqlValidacionDataSource.fetch_chain`), since Etapa 2's marks must survive across lotes
        (`domain/duplicidades.py`'s module docstring). Plain rows, no `LAG` (FIX 1) -- the domain
        sweep does the pairwise overlap enumeration."""
        result = await self._session.execute(_FETCH_HISTORIAL_PERIODOS_SQL, {"lote_id": lote_id})
        return [
            PeriodoHistorialRow(
                consumo_id=row.consumo_id,
                suministro_id=row.suministro_id,
                lote_id=row.lote_id,
                fecha_inicio=row.fecha_inicio,
                fecha_fin=row.fecha_fin,
            )
            for row in result.all()
        ]

    async def fetch_historial_lecturas(self, lote_id: UUID) -> Sequence[LecturaHistorialRow]:
        """Full active `lecturas` history of every suministro with at least one active consumo in
        `lote_id`, ordered by `fecha_lectura` per suministro. Plain rows, no `LAG` (FIX 1) -- the
        domain sweep does the pairwise near-duplicate enumeration."""
        result = await self._session.execute(_FETCH_HISTORIAL_LECTURAS_SQL, {"lote_id": lote_id})
        return [
            LecturaHistorialRow(
                lectura_id=row.lectura_id,
                suministro_id=row.suministro_id,
                fecha_lectura=row.fecha_lectura,
                lectura_actual=row.lectura_actual,
            )
            for row in result.all()
        ]

    async def fetch_conteos_lotes_relacionados(self, lote_id: UUID) -> Sequence[LoteConteoRow]:
        """Every OTHER lote (`lote_id` excluded) that CURRENTLY has at least one active consumo
        for a suministro also present in `lote_id`, with its `cantidad_registros` vs. its current
        active `consumos` count.

        Best-effort, not exhaustive: a lote that loses its ENTIRE shared link to `lote_id`'s
        suministros (every one of its periods for those suministros migrated elsewhere -- the
        natural-key upsert updates the SAME row's `lote_id` in place, `domain/duplicidades.py`'s
        module docstring, it never leaves a soft-deleted trace behind) becomes undiscoverable
        through this suministro-based join -- there is nothing left in `consumos` connecting it
        to `lote_id` anymore. This still catches the common, useful case: a lote that RETAINS at
        least one other period for a shared suministro is found, and its own drift (if any)
        surfaces even when the specific migrated row cannot be pinpointed.
        """
        result = await self._session.execute(
            _FETCH_CONTEOS_LOTES_RELACIONADOS_SQL, {"lote_id": lote_id}
        )
        return [
            LoteConteoRow(
                lote_id=row.lote_id,
                codigo_lote=row.codigo_lote,
                cantidad_registros=row.cantidad_registros,
                consumos_activos=row.consumos_activos,
            )
            for row in result.all()
        ]
