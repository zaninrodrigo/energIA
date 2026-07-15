"""SqlFeaturesDataSource: `FeaturesDataSource`'s implementation (ADR-006 cross-context read
shortcut) -- Etapa 3's set-based reads (US-008, AI_ENGINE_SPEC.md §6).

Same discipline as `validacion_data_source.py`/`duplicidades_data_source.py`: one set-based SQL
query per method, raw SQL (`sqlalchemy.text`) never an ORM model, since `consumos`/`suministros`
belong to OTHER bounded contexts (DOMAIN_MODEL.md §4.2/§4.3), not `motor` (§4.4). The prior
anomaly count query (`fetch_prior_anomaly_counts`) is the one exception in spirit, not in style:
`anomalias`/`resultados_ia` DO belong to `motor`'s own bounded context (§8.1/§8.2) -- still raw
SQL here, for consistency with every other read in this file (no ORM model exists for ANY of
`motor`'s own tables yet; introducing one for a single query would be inconsistent ceremony, see
`feature_vector_repository.py`'s docstring for the same reasoning applied to the write side).
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.motor.domain.ports import (
    FeatureConsumoRow,
    PriorAnomalyCountRow,
    SuministroMetadataRow,
)

# Same CTE shape `duplicidades_data_source.py` repeats inline per query (not imported/shared) --
# see that module's docstring for why: each query embeds it right where it is used.
_SUMINISTROS_DEL_LOTE_CTE = """
    suministros_del_lote AS (
        SELECT DISTINCT suministro_id
        FROM consumos
        WHERE lote_id = :lote_id AND deleted_at IS NULL
    )
"""

_FETCH_HISTORIAL_KWH_SQL = text(
    f"""
    WITH {_SUMINISTROS_DEL_LOTE_CTE}
    SELECT
        c.id AS consumo_id,
        c.suministro_id,
        c.lote_id,
        c.fecha_inicio,
        c.fecha_fin,
        c.dias_facturados,
        c.kwh
    FROM consumos c
    WHERE c.deleted_at IS NULL
      AND c.suministro_id IN (SELECT suministro_id FROM suministros_del_lote)
    ORDER BY c.suministro_id, c.fecha_inicio
    """
)

_FETCH_METADATA_SUMINISTROS_SQL = text(
    f"""
    WITH {_SUMINISTROS_DEL_LOTE_CTE}
    SELECT
        s.id AS suministro_id,
        s.categoria_tarifaria_id,
        s.localidad,
        s.fecha_alta,
        s.numero_suministro,
        s.estado
    FROM suministros s
    WHERE s.id IN (SELECT suministro_id FROM suministros_del_lote)
    -- Deterministic order: this row order propagates into InformeReglas.suministros,
    -- which is surfaced verbatim in the API response.
    ORDER BY s.numero_suministro
    """
)

_FETCH_PRIOR_ANOMALY_COUNTS_SQL = text(
    f"""
    WITH {_SUMINISTROS_DEL_LOTE_CTE}
    SELECT
        r.suministro_id,
        count(a.id) AS cantidad
    FROM resultados_ia r
    JOIN anomalias a ON a.resultado_ia_id = r.id AND a.deleted_at IS NULL
    WHERE r.deleted_at IS NULL
      AND r.suministro_id IN (SELECT suministro_id FROM suministros_del_lote)
    GROUP BY r.suministro_id
    """
)


class SqlFeaturesDataSource:
    """`FeaturesDataSource` (domain/ports.py) backed by direct SQL queries, same session/
    transaction as the rest of the request."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def fetch_historial_kwh(self, lote_id: UUID) -> Sequence[FeatureConsumoRow]:
        result = await self._session.execute(_FETCH_HISTORIAL_KWH_SQL, {"lote_id": lote_id})
        return [
            FeatureConsumoRow(
                consumo_id=row.consumo_id,
                suministro_id=row.suministro_id,
                lote_id=row.lote_id,
                fecha_inicio=row.fecha_inicio,
                fecha_fin=row.fecha_fin,
                dias_facturados=row.dias_facturados,
                kwh=row.kwh,
            )
            for row in result.all()
        ]

    async def fetch_metadata_suministros(self, lote_id: UUID) -> Sequence[SuministroMetadataRow]:
        result = await self._session.execute(_FETCH_METADATA_SUMINISTROS_SQL, {"lote_id": lote_id})
        return [
            SuministroMetadataRow(
                suministro_id=row.suministro_id,
                categoria_tarifaria_id=row.categoria_tarifaria_id,
                localidad=row.localidad,
                fecha_alta=row.fecha_alta,
                numero_suministro=row.numero_suministro,
                estado=row.estado,
            )
            for row in result.all()
        ]

    async def fetch_prior_anomaly_counts(self, lote_id: UUID) -> Sequence[PriorAnomalyCountRow]:
        result = await self._session.execute(_FETCH_PRIOR_ANOMALY_COUNTS_SQL, {"lote_id": lote_id})
        return [
            PriorAnomalyCountRow(suministro_id=row.suministro_id, cantidad=row.cantidad)
            for row in result.all()
        ]
