"""SqlResultadosRankingRepository: `ResultadosRankingRepository`'s implementation -- the READ
side of Etapas 7-8's convergence (`resultados_ia` + `ire` + `anomalias` + `impacto_economico`,
joined to `suministros`/`categorias_tarifarias` for display fields). Feeds `GET /api/v1/motor/
lotes/{codigo_lote}/resultados` (the dashboard's data source, RN-009).

**Set-based, never N+1.** `listar` issues ONE query: joins `resultados_ia` -> `suministros` ->
`categorias_tarifarias` -> `ire` (1:1) -> `impacto_economico` (0:1, `LEFT JOIN`), plus a
`LEFT JOIN LATERAL ... json_agg(...)` correlated subquery for `anomalias` (0:N per
`resultado_ia_id`) -- aggregating there, rather than joining `anomalias` directly, avoids the row
fan-out a plain join would cause (a suministro with 3 anomalias would otherwise duplicate its
`ire`/`impacto_economico` columns 3x). `contar` mirrors the same filters without the `anomalias`
aggregation (not needed for a count). `resumen` is a SEPARATE single-row aggregation query
(conditional `COUNT(*) FILTER (WHERE ...)` per band), deliberately UNFILTERED by `nivel`/
`clasificacion` -- see `domain/resultados_ranking.py`'s `ResumenRanking` docstring.

**Soft-delete discipline**: every joined table's `deleted_at IS NULL` is checked explicitly
(`resultados_ia`, `suministros`, `categorias_tarifarias`, `ire`, `impacto_economico`, `anomalias`)
-- DATABASE_DESIGN.md §10, "ningún DELETE físico".
"""

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.motor.domain.ire import NIVELES_IRE, FactorContribucion
from energia.contexts.motor.domain.isolation_forest import (
    CLASIFICACION_ALTO_RIESGO,
    CLASIFICACION_ATENCION,
    CLASIFICACION_CRITICO,
    CLASIFICACION_NORMAL,
)
from energia.contexts.motor.domain.resultados_ranking import (
    AnomaliaRankingRow,
    BarrioRiesgoRow,
    ResultadoRankingRow,
    ResumenRanking,
)


def _nivel_desde_valor(valor: int) -> str:
    """Band an IRE value (0-100) into its nivel, mirroring the DB's generated `ire.nivel` column
    (`docker/postgres/init/01_schema.sql`): 0-20 Muy Bajo / 21-40 Bajo / 41-60 Medio / 61-80 Alto /
    81-100 Crítico. Used for a BARRIO's mean IRE, which has no stored `nivel` of its own."""
    if valor <= 20:
        indice = 0
    elif valor <= 40:
        indice = 1
    elif valor <= 60:
        indice = 2
    elif valor <= 80:
        indice = 3
    else:
        indice = 4
    return NIVELES_IRE[indice]


_BARRIOS_SQL = text(
    """
    SELECT
        s.localidad,
        s.barrio,
        count(*) AS total_medidores,
        round(avg(i.valor))::int AS ire_promedio,
        max(i.valor)::int AS ire_maximo,
        count(*) FILTER (WHERE anom.n > 0) AS con_anomalias,
        avg(s.latitud) AS latitud,
        avg(s.longitud) AS longitud
    FROM resultados_ia r
    JOIN suministros s ON s.id = r.suministro_id AND s.deleted_at IS NULL
    JOIN ire i ON i.resultado_ia_id = r.id AND i.deleted_at IS NULL
    LEFT JOIN LATERAL (
        SELECT count(*) AS n
        FROM anomalias a
        WHERE a.resultado_ia_id = r.id AND a.deleted_at IS NULL
    ) anom ON true
    WHERE r.lote_id = :lote_id AND r.deleted_at IS NULL
    GROUP BY s.localidad, s.barrio
    ORDER BY max(i.valor) DESC
    """
)

# Mirrors `ck_resultados_ia_clasificacion` (`docker/postgres/init/01_schema.sql`) -- same 4
# literals `domain.isolation_forest` already names as constants, reused here (not re-typed) so a
# future rename of one of them cannot silently drift the two lists apart.
CLASIFICACIONES: tuple[str, ...] = (
    CLASIFICACION_NORMAL,
    CLASIFICACION_ATENCION,
    CLASIFICACION_ALTO_RIESGO,
    CLASIFICACION_CRITICO,
)

_LISTAR_SQL_BASE = """
    SELECT
        s.id AS suministro_id,
        s.numero_suministro,
        s.medidor,
        s.latitud,
        s.longitud,
        s.localidad,
        c.nombre AS categoria_tarifaria,
        r.score_anomalia,
        r.probabilidad,
        r.clasificacion,
        r.observaciones,
        i.valor AS ire_valor,
        i.nivel AS ire_nivel,
        ie.monto_estimado AS iee_kwh,
        COALESCE(anomalias_agg.anomalias, '[]') AS anomalias
    FROM resultados_ia r
    JOIN suministros s ON s.id = r.suministro_id AND s.deleted_at IS NULL
    JOIN categorias_tarifarias c ON c.id = s.categoria_tarifaria_id AND c.deleted_at IS NULL
    JOIN ire i ON i.resultado_ia_id = r.id AND i.deleted_at IS NULL
    LEFT JOIN impacto_economico ie
        ON ie.resultado_ia_id = r.id AND ie.deleted_at IS NULL AND ie.moneda = 'kWh'
    LEFT JOIN LATERAL (
        SELECT json_agg(
            json_build_object(
                'tipo', a.tipo, 'severidad', a.severidad, 'descripcion', a.descripcion
            )
            ORDER BY a.fecha_deteccion
        ) AS anomalias
        FROM anomalias a
        WHERE a.resultado_ia_id = r.id AND a.deleted_at IS NULL
    ) anomalias_agg ON true
    WHERE r.lote_id = :lote_id AND r.deleted_at IS NULL
"""

_CONTAR_SQL_BASE = """
    SELECT count(*)
    FROM resultados_ia r
    JOIN suministros s ON s.id = r.suministro_id AND s.deleted_at IS NULL
    JOIN ire i ON i.resultado_ia_id = r.id AND i.deleted_at IS NULL
    WHERE r.lote_id = :lote_id AND r.deleted_at IS NULL
"""

_RESUMEN_SQL = text(
    """
    SELECT
        count(*) AS total,
        count(*) FILTER (WHERE i.nivel = 'Muy Bajo') AS nivel_muy_bajo,
        count(*) FILTER (WHERE i.nivel = 'Bajo') AS nivel_bajo,
        count(*) FILTER (WHERE i.nivel = 'Medio') AS nivel_medio,
        count(*) FILTER (WHERE i.nivel = 'Alto') AS nivel_alto,
        count(*) FILTER (WHERE i.nivel = 'Crítico') AS nivel_critico,
        count(*) FILTER (WHERE r.clasificacion = 'Normal') AS clasificacion_normal,
        count(*) FILTER (WHERE r.clasificacion = 'Atención') AS clasificacion_atencion,
        count(*) FILTER (WHERE r.clasificacion = 'Alto Riesgo') AS clasificacion_alto_riesgo,
        count(*) FILTER (WHERE r.clasificacion = 'Crítico') AS clasificacion_critico,
        count(*) FILTER (WHERE anomalias_agg.cnt > 0) AS con_anomalias,
        COALESCE(sum(ie.monto_estimado), 0) AS suma_iee_kwh
    FROM resultados_ia r
    JOIN suministros s ON s.id = r.suministro_id AND s.deleted_at IS NULL
    JOIN ire i ON i.resultado_ia_id = r.id AND i.deleted_at IS NULL
    LEFT JOIN impacto_economico ie
        ON ie.resultado_ia_id = r.id AND ie.deleted_at IS NULL AND ie.moneda = 'kWh'
    LEFT JOIN LATERAL (
        SELECT count(*) AS cnt FROM anomalias a
        WHERE a.resultado_ia_id = r.id AND a.deleted_at IS NULL
    ) anomalias_agg ON true
    WHERE r.lote_id = :lote_id AND r.deleted_at IS NULL
    """
)


def _parse_observaciones(raw: str | None) -> tuple[FactorContribucion, ...]:
    """DEC-016's explicability breakdown, parsed defensively (see `domain/resultados_ranking.py`'s
    `ResultadoRankingRow` docstring): `None`/empty/malformed JSON/non-list JSON all yield `()`;
    inside an otherwise-valid list, one entry missing a required key is skipped individually
    rather than failing the whole row."""
    if not raw:
        return ()
    try:
        entradas = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ()
    if not isinstance(entradas, list):
        return ()

    factores = []
    for entrada in entradas:
        try:
            factores.append(
                FactorContribucion(
                    factor=entrada["factor"],
                    contribution=entrada["contribution"],
                    reason=entrada["reason"],
                )
            )
        except (KeyError, TypeError):
            continue
    return tuple(factores)


def _as_anomalias(raw: Any) -> list[dict[str, Any]]:
    """`json_agg(...)`'s output: normally already decoded to a Python `list[dict]` by asyncpg's
    own `json`/`jsonb` codecs (registered by SQLAlchemy's asyncpg dialect at connect time, ahead
    of any per-column typing) -- confirmed empirically by this repository's own integration
    tests. Parsed defensively here too (never hit under this driver/dialect combination, hence
    `pragma: no cover` on both branches) in case a future driver/dialect change stops doing that;
    `COALESCE(anomalias_agg.anomalias, '[]')` in the SQL also means `raw` is never SQL `NULL`."""
    if raw is None:
        return []  # pragma: no cover — defensive; COALESCE in SQL already prevents NULL
    if isinstance(raw, str):
        return list(json.loads(raw))  # pragma: no cover — defensive; asyncpg already decodes json
    return list(raw)


def _filtros_sql(nivel: str | None, clasificacion: str | None) -> tuple[str, dict[str, str]]:
    condiciones = []
    parametros: dict[str, str] = {}
    if nivel is not None:
        condiciones.append("i.nivel = :nivel")
        parametros["nivel"] = nivel
    if clasificacion is not None:
        condiciones.append("r.clasificacion = :clasificacion")
        parametros["clasificacion"] = clasificacion
    sql = "".join(f" AND {condicion}" for condicion in condiciones)
    return sql, parametros


class SqlResultadosRankingRepository:
    """`ResultadosRankingRepository` (domain/resultados_ranking.py) backed by direct SQL queries,
    same session as the rest of the request -- read-only, never writes, never commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def listar(
        self,
        lote_id: UUID,
        *,
        limit: int,
        offset: int,
        nivel: str | None,
        clasificacion: str | None,
    ) -> list[ResultadoRankingRow]:
        filtros_sql, filtros_parametros = _filtros_sql(nivel, clasificacion)
        stmt = text(
            _LISTAR_SQL_BASE
            + filtros_sql
            + " ORDER BY i.valor DESC, s.numero_suministro ASC LIMIT :limit OFFSET :offset"
        )
        result = await self._session.execute(
            stmt,
            {"lote_id": lote_id, "limit": limit, "offset": offset, **filtros_parametros},
        )
        return [
            ResultadoRankingRow(
                suministro_id=row.suministro_id,
                numero_suministro=row.numero_suministro,
                medidor=row.medidor,
                latitud=(float(row.latitud) if row.latitud is not None else None),
                longitud=(float(row.longitud) if row.longitud is not None else None),
                ire_valor=int(row.ire_valor),
                ire_nivel=row.ire_nivel,
                clasificacion=row.clasificacion,
                score_anomalia=(
                    float(row.score_anomalia) if row.score_anomalia is not None else None
                ),
                probabilidad=(float(row.probabilidad) if row.probabilidad is not None else None),
                localidad=row.localidad,
                categoria_tarifaria=row.categoria_tarifaria,
                anomalias=tuple(
                    AnomaliaRankingRow(
                        tipo=anomalia["tipo"],
                        severidad=anomalia["severidad"],
                        descripcion=anomalia.get("descripcion"),
                    )
                    for anomalia in _as_anomalias(row.anomalias)
                ),
                observaciones=_parse_observaciones(row.observaciones),
                iee_kwh=float(row.iee_kwh) if row.iee_kwh is not None else None,
            )
            for row in result
        ]

    async def contar(self, lote_id: UUID, *, nivel: str | None, clasificacion: str | None) -> int:
        filtros_sql, filtros_parametros = _filtros_sql(nivel, clasificacion)
        stmt = text(_CONTAR_SQL_BASE + filtros_sql)
        result = await self._session.execute(stmt, {"lote_id": lote_id, **filtros_parametros})
        return result.scalar_one()  # type: ignore[no-any-return]

    async def resumen(self, lote_id: UUID) -> ResumenRanking:
        result = await self._session.execute(_RESUMEN_SQL, {"lote_id": lote_id})
        row = result.one()
        conteo_por_nivel = dict(
            zip(
                NIVELES_IRE,
                (
                    row.nivel_muy_bajo,
                    row.nivel_bajo,
                    row.nivel_medio,
                    row.nivel_alto,
                    row.nivel_critico,
                ),
                strict=True,
            )
        )
        conteo_por_clasificacion = dict(
            zip(
                CLASIFICACIONES,
                (
                    row.clasificacion_normal,
                    row.clasificacion_atencion,
                    row.clasificacion_alto_riesgo,
                    row.clasificacion_critico,
                ),
                strict=True,
            )
        )
        return ResumenRanking(
            total_resultados=row.total,
            conteo_por_nivel=conteo_por_nivel,
            conteo_por_clasificacion=conteo_por_clasificacion,
            con_anomalias=row.con_anomalias,
            suma_iee_kwh=float(row.suma_iee_kwh),
        )

    async def barrios(self, lote_id: UUID) -> list[BarrioRiesgoRow]:
        result = await self._session.execute(_BARRIOS_SQL, {"lote_id": lote_id})
        return [
            BarrioRiesgoRow(
                localidad=row.localidad,
                barrio=row.barrio,
                total_medidores=row.total_medidores,
                ire_promedio=row.ire_promedio,
                ire_maximo=row.ire_maximo,
                nivel=_nivel_desde_valor(row.ire_maximo),
                con_anomalias=row.con_anomalias,
                latitud=(float(row.latitud) if row.latitud is not None else None),
                longitud=(float(row.longitud) if row.longitud is not None else None),
            )
            for row in result
        ]
