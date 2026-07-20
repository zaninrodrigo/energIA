"""Unit tests for `motor.presentation.routes` -- exercised at the route-handler level (no real
DB, no ASGI transport): the route's own port constructors (`SqlLoteProcesamientoPort`,
`SqlValidacionDataSource`) are monkeypatched away with in-memory fakes, so `session` is never
actually touched.

Covers the WARN fix: when the optimistic `Pendiente`/`Error` -> `Procesando` transition loses a
concurrent race (`transicionar_estado` returns `False`), the route used to always report "ya
está en procesamiento (Procesando)" even when the lote had, by the time of the re-read, already
finished in `Procesado` or `Error` -- see `AI_ENGINE_SPEC.md` §2.1-§2.3.
"""

from dataclasses import dataclass, field
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.motor.domain.ire import NIVELES_IRE
from energia.contexts.motor.domain.lote_estado import EstadoLote
from energia.contexts.motor.domain.ports import EstadoLoteActual
from energia.contexts.motor.domain.resultados_ranking import ResultadoRankingRow, ResumenRanking
from energia.contexts.motor.infrastructure.resultados_ranking_repository import CLASIFICACIONES
from energia.contexts.motor.presentation import routes


@dataclass
class _FakeLoteProcesamientoPortPerdioCarrera:
    """Simulates a lost race on the optimistic `Procesando` transition: `transicionar_estado`
    always returns `False`. The FIRST `obtener_estado_actual` call (inside `ProcesarLote.execute`)
    reports a retry-eligible `Pendiente` lote; the SECOND call (the route's post-catch re-read,
    the fix under test) reports `estado_tras_carrera` -- whatever a concurrent execution left the
    lote in by the time this request lost the race.
    """

    estado_tras_carrera: EstadoLote
    lote_id: UUID = field(default_factory=uuid4)
    llamadas_obtener_estado: int = 0

    async def obtener_estado_actual(self, codigo_lote: str) -> EstadoLoteActual:
        self.llamadas_obtener_estado += 1
        estado = (
            EstadoLote.PENDIENTE if self.llamadas_obtener_estado == 1 else self.estado_tras_carrera
        )
        return EstadoLoteActual(
            id=self.lote_id, codigo_lote=codigo_lote, estado=estado, cantidad_registros=1
        )

    async def contar_consumos_activos(self, lote_id: UUID) -> int:
        return 1

    async def transicionar_estado(
        self, lote_id: UUID, *, desde: frozenset[EstadoLote], hacia: EstadoLote
    ) -> bool:
        return False


@pytest.mark.parametrize(
    ("estado_tras_carrera", "fragmento_esperado"),
    [
        (EstadoLote.PROCESANDO, "en procesamiento"),
        (EstadoLote.PROCESADO, "RD-010"),
        (EstadoLote.ERROR, "finalizó en Error"),
    ],
)
async def test_lost_race_branches_409_message_by_current_estado(
    monkeypatch: pytest.MonkeyPatch,
    estado_tras_carrera: EstadoLote,
    fragmento_esperado: str,
) -> None:
    fake_port = _FakeLoteProcesamientoPortPerdioCarrera(estado_tras_carrera=estado_tras_carrera)
    monkeypatch.setattr(routes, "SqlLoteProcesamientoPort", lambda session: fake_port)

    with pytest.raises(HTTPException) as excinfo:
        await routes.procesar_lote(codigo_lote="LOTE-1", session=cast(AsyncSession, None))

    assert excinfo.value.status_code == status.HTTP_409_CONFLICT
    assert fragmento_esperado in cast(str, excinfo.value.detail)


# -------------------------------------------------------------------------------------------
# GET /api/v1/motor/lotes/{codigo_lote}/resultados -- 404 vs. an empty-but-real-lote 200 (the
# distinction the mission calls out explicitly): both exercised here at the route-handler level,
# with `SqlLoteProcesamientoPort`/`SqlResultadosRankingRepository` monkeypatched away, so neither
# ever touches a real session. The IRE-desc ordering, filters, and parsed-`observaciones` shape
# are proven against a real database by the integration suite instead (a fake repository would
# only prove the route calls its port correctly, not that the SQL itself is right).
# -------------------------------------------------------------------------------------------


@dataclass
class _FakeLoteProcesamientoPortInexistente:
    async def obtener_estado_actual(self, codigo_lote: str) -> EstadoLoteActual | None:
        return None


async def test_obtener_resultados_lote_returns_404_when_lote_does_not_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        routes,
        "SqlLoteProcesamientoPort",
        lambda session: _FakeLoteProcesamientoPortInexistente(),
    )

    with pytest.raises(HTTPException) as excinfo:
        await routes.obtener_resultados_lote(
            codigo_lote="NO-EXISTE",
            limit=50,
            offset=0,
            nivel=None,
            clasificacion=None,
            session=cast(AsyncSession, None),
        )

    assert excinfo.value.status_code == status.HTTP_404_NOT_FOUND
    assert "NO-EXISTE" in cast(str, excinfo.value.detail)


@dataclass
class _FakeLoteProcesamientoPortExistente:
    """A real, non-soft-deleted lote that has not been processed yet (still `Pendiente`) -- the
    "not found" vs. "not analyzed yet" distinction `obtener_resultados_lote`'s own docstring
    documents."""

    lote_id: UUID = field(default_factory=uuid4)

    async def obtener_estado_actual(self, codigo_lote: str) -> EstadoLoteActual:
        return EstadoLoteActual(
            id=self.lote_id,
            codigo_lote=codigo_lote,
            estado=EstadoLote.PENDIENTE,
            cantidad_registros=0,
        )


@dataclass
class _FakeResultadosRankingRepositoryVacio:
    """No `resultados_ia` rows at all for this lote -- mirrors what `SqlResultadosRankingRepository`
    would report for a genuinely unanalyzed (but real) lote."""

    async def listar(
        self,
        lote_id: UUID,
        *,
        limit: int,
        offset: int,
        nivel: str | None,
        clasificacion: str | None,
    ) -> list[ResultadoRankingRow]:
        return []

    async def contar(self, lote_id: UUID, *, nivel: str | None, clasificacion: str | None) -> int:
        return 0

    async def resumen(self, lote_id: UUID) -> ResumenRanking:
        return ResumenRanking(
            total_resultados=0,
            conteo_por_nivel=dict.fromkeys(NIVELES_IRE, 0),
            conteo_por_clasificacion=dict.fromkeys(CLASIFICACIONES, 0),
            con_anomalias=0,
            suma_iee_kwh=0.0,
        )


async def test_obtener_resultados_lote_returns_200_empty_page_for_an_unanalyzed_lote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        routes, "SqlLoteProcesamientoPort", lambda session: _FakeLoteProcesamientoPortExistente()
    )
    monkeypatch.setattr(
        routes,
        "SqlResultadosRankingRepository",
        lambda session: _FakeResultadosRankingRepositoryVacio(),
    )

    response = await routes.obtener_resultados_lote(
        codigo_lote="LOTE-PENDIENTE",
        limit=50,
        offset=0,
        nivel=None,
        clasificacion=None,
        session=cast(AsyncSession, None),
    )

    assert response.items == []
    assert response.total == 0
    assert response.resumen.total_resultados == 0
    assert response.resumen.conteo_por_nivel == dict.fromkeys(NIVELES_IRE, 0)
