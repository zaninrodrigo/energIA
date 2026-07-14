"""SqlValidacionDataSource: `ValidacionDataSource`'s implementation (ADR-006 cross-context read
shortcut).

One set-based SQL query per lote, not a per-row loop of individual queries (RNF-001 -- Etapa 1
must stay SQL-bound and fast, AI_ENGINE_SPEC.md §2.4/§12). The window function
(`LAG(...) OVER (PARTITION BY suministro_id ORDER BY fecha_inicio, fecha_fin)`) spans EVERY
active consumo of each suministro referenced by this lote -- not just this lote's own rows --
because RD-017 ("el período no puede superponerse con otro") is a suministro-level invariant
that must be checked across a suministro's WHOLE history, including periods imported by a
DIFFERENT lote (that is exactly how two overlapping imports for the same suministro, split
across two lotes, would ever be detected at all). The outer query then filters back down to
just this lote's own rows before returning them -- the window's wider scope only exists to
compute each row's `prev_fecha_inicio`/`prev_fecha_fin` correctly.

Deliberately raw SQL (`sqlalchemy.text`), never an ORM model of `consumos`/`lecturas`/
`suministros`/`categorias_tarifarias`: none of those tables belong to `motor` (DOMAIN_MODEL.md
§4.4) -- mirrors `contexts.consumos.infrastructure.suministro_directory.
SqlDirectSuministroDirectory`'s rationale, see `domain/ports.py`'s module docstring.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.motor.domain.ports import ConsumoValidacionRow

_FETCH_CHAIN_SQL = text(
    """
    WITH suministros_del_lote AS (
        SELECT DISTINCT suministro_id
        FROM consumos
        WHERE lote_id = :lote_id AND deleted_at IS NULL
    ),
    historial AS (
        SELECT
            c.id AS consumo_id,
            c.suministro_id,
            c.lote_id,
            c.fecha_inicio,
            c.fecha_fin,
            c.dias_facturados,
            c.kwh,
            c.lectura_id,
            LAG(c.fecha_inicio) OVER (
                PARTITION BY c.suministro_id ORDER BY c.fecha_inicio, c.fecha_fin
            ) AS prev_fecha_inicio,
            LAG(c.fecha_fin) OVER (
                PARTITION BY c.suministro_id ORDER BY c.fecha_inicio, c.fecha_fin
            ) AS prev_fecha_fin
        FROM consumos c
        WHERE c.deleted_at IS NULL
          AND c.suministro_id IN (SELECT suministro_id FROM suministros_del_lote)
    )
    SELECT
        historial.consumo_id,
        historial.suministro_id,
        historial.fecha_inicio,
        historial.fecha_fin,
        historial.dias_facturados,
        historial.kwh,
        historial.lectura_id,
        historial.prev_fecha_inicio,
        historial.prev_fecha_fin,
        l.lectura_anterior,
        l.lectura_actual,
        l.dias_facturados AS lectura_dias_facturados,
        ct.deleted_at AS categoria_deleted_at
    FROM historial
    JOIN suministros s ON s.id = historial.suministro_id
    LEFT JOIN categorias_tarifarias ct ON ct.id = s.categoria_tarifaria_id
    LEFT JOIN lecturas l ON l.id = historial.lectura_id AND l.deleted_at IS NULL
    WHERE historial.lote_id = :lote_id
    ORDER BY historial.suministro_id, historial.fecha_inicio
    """
)


class SqlValidacionDataSource:
    """`ValidacionDataSource` (domain/ports.py) backed by a direct SQL query, same session/
    transaction as the rest of the request."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def fetch_chain(self, lote_id: UUID) -> Sequence[ConsumoValidacionRow]:
        result = await self._session.execute(_FETCH_CHAIN_SQL, {"lote_id": lote_id})
        return [
            ConsumoValidacionRow(
                consumo_id=row.consumo_id,
                suministro_id=row.suministro_id,
                fecha_inicio=row.fecha_inicio,
                fecha_fin=row.fecha_fin,
                dias_facturados=row.dias_facturados,
                kwh=row.kwh,
                lectura_id=row.lectura_id,
                lectura_anterior=row.lectura_anterior,
                lectura_actual=row.lectura_actual,
                lectura_dias_facturados=row.lectura_dias_facturados,
                prev_fecha_inicio=row.prev_fecha_inicio,
                prev_fecha_fin=row.prev_fecha_fin,
                categoria_deleted_at=row.categoria_deleted_at,
            )
            for row in result.all()
        ]
