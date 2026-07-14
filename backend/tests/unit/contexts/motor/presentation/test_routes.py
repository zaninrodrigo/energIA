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

from energia.contexts.motor.domain.lote_estado import EstadoLote
from energia.contexts.motor.domain.ports import EstadoLoteActual
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
