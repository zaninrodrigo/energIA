"""Unit tests for `ProcesarLote` (US-006 + US-010 trigger, AI_ENGINE_SPEC.md §2-§4) against
plain in-memory fakes of `LoteProcesamientoPort`/`ValidacionDataSource` -- no database."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from energia.contexts.motor.application.procesar_lote import (
    LoteEnProgresoError,
    LoteModificadoError,
    LoteNoEncontradoError,
    LoteNoListoError,
    LoteYaProcesadoError,
    ProcesarLote,
)
from energia.contexts.motor.domain.lote_estado import EstadoLote
from energia.contexts.motor.domain.ports import (
    ConsumoValidacionRow,
    EstadoLoteActual,
    LecturaHistorialRow,
    LoteConteoRow,
    PeriodoHistorialRow,
)


@dataclass
class FakeLoteProcesamientoPort:
    """In-memory fake of `LoteProcesamientoPort` -- a single lote, plus hooks to simulate a
    concurrent race stealing the transition out from under `ProcesarLote`, or the final
    transition itself (impossible today under the real row lock, but exercised here to protect
    a future refactor -- see `test_final_transition_returning_false_raises_assertion_error`)."""

    lote: EstadoLoteActual | None
    consumos_activos: int = 0
    roba_transicion_concurrente: bool = False
    falla_transicion_final: bool = False
    transiciones_solicitadas: list[tuple[frozenset[EstadoLote], EstadoLote]] = field(
        default_factory=list
    )

    async def obtener_estado_actual(self, codigo_lote: str) -> EstadoLoteActual | None:
        if self.lote is None or self.lote.codigo_lote != codigo_lote:
            return None
        return self.lote

    async def contar_consumos_activos(self, lote_id: UUID) -> int:
        return self.consumos_activos

    async def transicionar_estado(
        self, lote_id: UUID, *, desde: frozenset[EstadoLote], hacia: EstadoLote
    ) -> bool:
        self.transiciones_solicitadas.append((desde, hacia))
        if self.roba_transicion_concurrente:
            return False
        if self.falla_transicion_final and desde == frozenset({EstadoLote.PROCESANDO}):
            return False
        assert self.lote is not None
        if self.lote.estado not in desde:
            return False
        self.lote = EstadoLoteActual(
            id=self.lote.id,
            codigo_lote=self.lote.codigo_lote,
            estado=hacia,
            cantidad_registros=self.lote.cantidad_registros,
        )
        return True


@dataclass
class FakeValidacionDataSource:
    """`on_fetch`, when set, runs as a side effect of `fetch_chain` -- used to simulate a
    concurrent consumo insert for the SAME lote landing in the window between the completeness
    gate and `fetch_chain` (the reviewer's mid-flight-import race, `LoteModificadoError`)."""

    filas_por_lote: dict[UUID, list[ConsumoValidacionRow]]
    on_fetch: Callable[[], None] | None = None

    async def fetch_chain(self, lote_id: UUID) -> list[ConsumoValidacionRow]:
        if self.on_fetch is not None:
            self.on_fetch()
        return self.filas_por_lote.get(lote_id, [])


@dataclass
class FakeDuplicidadesDataSource:
    """In-memory fake of `DuplicidadesDataSource` (Etapa 2, US-007) -- empty dicts by default, so
    every existing Etapa 1 test can pass one without caring about Etapa 2's own fixtures."""

    periodos_por_lote: dict[UUID, list[PeriodoHistorialRow]] = field(default_factory=dict)
    lecturas_por_lote: dict[UUID, list[LecturaHistorialRow]] = field(default_factory=dict)
    conteos_por_lote: dict[UUID, list[LoteConteoRow]] = field(default_factory=dict)

    async def fetch_historial_periodos(self, lote_id: UUID) -> list[PeriodoHistorialRow]:
        return self.periodos_por_lote.get(lote_id, [])

    async def fetch_historial_lecturas(self, lote_id: UUID) -> list[LecturaHistorialRow]:
        return self.lecturas_por_lote.get(lote_id, [])

    async def fetch_conteos_lotes_relacionados(self, lote_id: UUID) -> list[LoteConteoRow]:
        return self.conteos_por_lote.get(lote_id, [])


def _fila_valida(suministro_id: UUID | None = None) -> ConsumoValidacionRow:
    return ConsumoValidacionRow(
        consumo_id=uuid4(),
        suministro_id=suministro_id or uuid4(),
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
        dias_facturados=31,
        kwh=Decimal("100.000"),
        lectura_id=uuid4(),
        lectura_anterior=Decimal("100.000"),
        lectura_actual=Decimal("200.000"),
        lectura_dias_facturados=31,
        prev_fecha_inicio=None,
        prev_fecha_fin=None,
        categoria_deleted_at=None,
    )


async def test_lote_not_found_raises() -> None:
    lote_port = FakeLoteProcesamientoPort(lote=None)
    use_case = ProcesarLote(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({}),
        duplicidades_source=FakeDuplicidadesDataSource(),
    )

    with pytest.raises(LoteNoEncontradoError):
        await use_case.execute("LOTE-DESCONOCIDO")


async def test_lote_procesando_raises_conflict() -> None:
    lote_id = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-1", estado=EstadoLote.PROCESANDO, cantidad_registros=3
        )
    )
    use_case = ProcesarLote(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({}),
        duplicidades_source=FakeDuplicidadesDataSource(),
    )

    with pytest.raises(LoteEnProgresoError):
        await use_case.execute("LOTE-1")


async def test_lote_procesado_raises_terminal_conflict() -> None:
    lote_id = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-1", estado=EstadoLote.PROCESADO, cantidad_registros=3
        )
    )
    use_case = ProcesarLote(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({}),
        duplicidades_source=FakeDuplicidadesDataSource(),
    )

    with pytest.raises(LoteYaProcesadoError):
        await use_case.execute("LOTE-1")


async def test_incomplete_lote_raises_not_ready_and_makes_no_transition() -> None:
    lote_id = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-1", estado=EstadoLote.PENDIENTE, cantidad_registros=5
        ),
        consumos_activos=2,
    )
    use_case = ProcesarLote(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({}),
        duplicidades_source=FakeDuplicidadesDataSource(),
    )

    with pytest.raises(LoteNoListoError) as excinfo:
        await use_case.execute("LOTE-1")

    assert excinfo.value.completitud.cantidad_registros == 5
    assert excinfo.value.completitud.consumos_activos == 2
    assert lote_port.transiciones_solicitadas == []
    assert lote_port.lote is not None
    assert lote_port.lote.estado is EstadoLote.PENDIENTE  # untouched


async def test_complete_lote_with_valid_checks_transitions_to_procesado() -> None:
    lote_id = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-1", estado=EstadoLote.PENDIENTE, cantidad_registros=2
        ),
        consumos_activos=2,
    )
    filas = [_fila_valida(), _fila_valida()]
    use_case = ProcesarLote(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: filas}),
        duplicidades_source=FakeDuplicidadesDataSource(),
    )

    resultado = await use_case.execute("LOTE-1")

    assert resultado.estado_final is EstadoLote.PROCESADO
    assert resultado.informe.umbral_cumplido is True
    assert lote_port.lote is not None
    assert lote_port.lote.estado is EstadoLote.PROCESADO
    assert lote_port.transiciones_solicitadas == [
        (frozenset({EstadoLote.PENDIENTE}), EstadoLote.PROCESANDO),
        (frozenset({EstadoLote.PROCESANDO}), EstadoLote.PROCESADO),
    ]


async def test_complete_lote_below_threshold_transitions_to_error() -> None:
    lote_id = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-1", estado=EstadoLote.PENDIENTE, cantidad_registros=1
        ),
        consumos_activos=1,
    )
    fila_invalida = _fila_valida()
    fila_invalida = ConsumoValidacionRow(
        consumo_id=fila_invalida.consumo_id,
        suministro_id=fila_invalida.suministro_id,
        fecha_inicio=fila_invalida.fecha_inicio,
        fecha_fin=fila_invalida.fecha_fin,
        dias_facturados=fila_invalida.dias_facturados,
        kwh=fila_invalida.kwh,
        lectura_id=None,
        lectura_anterior=None,
        lectura_actual=None,
        lectura_dias_facturados=None,
        prev_fecha_inicio=None,
        prev_fecha_fin=None,
        categoria_deleted_at=None,
    )
    use_case = ProcesarLote(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: [fila_invalida]}),
        duplicidades_source=FakeDuplicidadesDataSource(),
    )

    resultado = await use_case.execute("LOTE-1")

    assert resultado.estado_final is EstadoLote.ERROR
    assert resultado.informe.umbral_cumplido is False
    assert lote_port.lote is not None
    assert lote_port.lote.estado is EstadoLote.ERROR


async def test_error_lote_can_retry() -> None:
    lote_id = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-1", estado=EstadoLote.ERROR, cantidad_registros=1
        ),
        consumos_activos=1,
    )
    use_case = ProcesarLote(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: [_fila_valida()]}),
        duplicidades_source=FakeDuplicidadesDataSource(),
    )

    resultado = await use_case.execute("LOTE-1")

    assert resultado.estado_final is EstadoLote.PROCESADO
    assert lote_port.transiciones_solicitadas[0] == (
        frozenset({EstadoLote.ERROR}),
        EstadoLote.PROCESANDO,
    )


async def test_concurrent_trigger_race_degrades_to_conflict() -> None:
    lote_id = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-1", estado=EstadoLote.PENDIENTE, cantidad_registros=1
        ),
        consumos_activos=1,
        roba_transicion_concurrente=True,
    )
    use_case = ProcesarLote(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: [_fila_valida()]}),
        duplicidades_source=FakeDuplicidadesDataSource(),
    )

    with pytest.raises(LoteEnProgresoError):
        await use_case.execute("LOTE-1")


async def test_consumo_inserted_mid_flight_raises_lote_modificado_with_no_final_transition() -> (
    None
):
    """Reproduces the reviewer's race: the completeness gate certifies `consumos_activos=1`,
    but a concurrent consumo insert for the SAME lote lands right as `fetch_chain` runs -- the
    recount right after `fetch_chain` (same transaction) must catch the mismatch and raise
    `LoteModificadoError`, WITHOUT ever requesting the final `Procesando -> Procesado/Error`
    transition (only the transition INTO `Procesando`, step (c), was requested)."""
    lote_id = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-1", estado=EstadoLote.PENDIENTE, cantidad_registros=1
        ),
        consumos_activos=1,
    )

    def _simular_insert_concurrente() -> None:
        # A second consumo for the SAME lote landed after the gate counted 1 -- mutating the
        # fake's own `consumos_activos` models the DB row a real concurrent session would have
        # inserted, read back by the recount `contar_consumos_activos` call right after this.
        lote_port.consumos_activos = 2

    use_case = ProcesarLote(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource(
            {lote_id: [_fila_valida()]}, on_fetch=_simular_insert_concurrente
        ),
        duplicidades_source=FakeDuplicidadesDataSource(),
    )

    with pytest.raises(LoteModificadoError):
        await use_case.execute("LOTE-1")

    assert lote_port.transiciones_solicitadas == [
        (frozenset({EstadoLote.PENDIENTE}), EstadoLote.PROCESANDO)
    ]
    assert lote_port.lote is not None
    assert lote_port.lote.estado is EstadoLote.PROCESANDO  # never reached the final transition


async def test_final_transition_returning_false_raises_assertion_error() -> None:
    """FIX 5(c): the final `Procesando -> Procesado/Error` transition is guarded by an `assert`
    that protects a future refactor -- today it is impossible for it to return `False` (the row
    lock held since step (c) rules out a concurrent writer), but if a future change ever broke
    that invariant, this must fail loudly instead of silently returning a wrong `estado_final`.
    """
    lote_id = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-1", estado=EstadoLote.PENDIENTE, cantidad_registros=1
        ),
        consumos_activos=1,
        falla_transicion_final=True,
    )
    use_case = ProcesarLote(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: [_fila_valida()]}),
        duplicidades_source=FakeDuplicidadesDataSource(),
    )

    with pytest.raises(AssertionError):
        await use_case.execute("LOTE-1")


# ---------------------------------------------------------------------------------------------
# Etapa 2 -- duplicidades (US-007, AI_ENGINE_SPEC.md §5)
# ---------------------------------------------------------------------------------------------


def _periodos_conflictivos_pair(suministro_id: UUID, *, lote_id: UUID) -> list[PeriodoHistorialRow]:
    """Two overlapping `PeriodoHistorialRow`s for the same suministro -- PLAIN rows (FIX 1, no
    `LAG`/`prev_*`): `detectar_periodos_conflictivos`'s sorted sweep needs both rows of a pair
    present in the list to find the overlap between them."""
    return [
        PeriodoHistorialRow(
            consumo_id=uuid4(),
            suministro_id=suministro_id,
            lote_id=uuid4(),
            fecha_inicio=date(2024, 1, 1),
            fecha_fin=date(2024, 1, 31),
        ),
        PeriodoHistorialRow(
            consumo_id=uuid4(),
            suministro_id=suministro_id,
            lote_id=lote_id,
            fecha_inicio=date(2024, 1, 15),
            fecha_fin=date(2024, 2, 15),
        ),
    ]


def _fila_excluida(suministro_id: UUID | None = None) -> ConsumoValidacionRow:
    """A `ConsumoValidacionRow` Etapa 1's V1 excludes (no lectura associated)."""
    base = _fila_valida(suministro_id)
    return ConsumoValidacionRow(
        consumo_id=base.consumo_id,
        suministro_id=base.suministro_id,
        fecha_inicio=base.fecha_inicio,
        fecha_fin=base.fecha_fin,
        dias_facturados=base.dias_facturados,
        kwh=base.kwh,
        lectura_id=None,
        lectura_anterior=None,
        lectura_actual=None,
        lectura_dias_facturados=None,
        prev_fecha_inicio=None,
        prev_fecha_fin=None,
        categoria_deleted_at=None,
    )


async def test_duplicidades_defaults_to_empty_when_source_has_nothing() -> None:
    lote_id = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-1", estado=EstadoLote.PENDIENTE, cantidad_registros=1
        ),
        consumos_activos=1,
    )
    use_case = ProcesarLote(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: [_fila_valida()]}),
        duplicidades_source=FakeDuplicidadesDataSource(),
    )

    resultado = await use_case.execute("LOTE-1")

    assert resultado.duplicidades.lote_id == lote_id
    assert resultado.duplicidades.periodos_conflictivos == ()
    assert resultado.duplicidades.lecturas_near_duplicate == ()
    assert resultado.duplicidades.drift_lotes == ()


async def test_duplicidades_marks_suministro_even_when_etapa1_excluded_it() -> None:
    """Etapa 2's periodo marks are computed for EVERY suministro of the lote, deliberately NOT
    filtered by Etapa 1's own exclusions (`domain/duplicidades.py`'s module docstring) -- a
    suministro V5 excludes from THIS lote's scoring is exactly the case whose overlap must still
    be marked, so a FUTURE lote's feature windows know to skip it."""
    lote_id = uuid4()
    suministro_ok = uuid4()
    suministro_excluido = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-1", estado=EstadoLote.PENDIENTE, cantidad_registros=2
        ),
        consumos_activos=2,
    )
    duplicidades_source = FakeDuplicidadesDataSource(
        periodos_por_lote={
            lote_id: [
                *_periodos_conflictivos_pair(suministro_ok, lote_id=lote_id),
                *_periodos_conflictivos_pair(suministro_excluido, lote_id=lote_id),
            ]
        }
    )
    use_case = ProcesarLote(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource(
            {lote_id: [_fila_valida(suministro_ok), _fila_excluida(suministro_excluido)]}
        ),
        duplicidades_source=duplicidades_source,
    )

    resultado = await use_case.execute("LOTE-1")

    suministros_marcados = {p.suministro_id for p in resultado.duplicidades.periodos_conflictivos}
    assert suministros_marcados == {suministro_ok, suministro_excluido}


async def test_duplicidades_present_regardless_of_estado_final() -> None:
    """Etapa 2 never changes the estado outcome (DEC-005: annotate only, §5) -- it still runs and
    populates `duplicidades` even when the lote lands in `Error` via Etapa 1's own threshold."""
    lote_id = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-1", estado=EstadoLote.PENDIENTE, cantidad_registros=1
        ),
        consumos_activos=1,
    )
    conteo_drift = LoteConteoRow(
        lote_id=uuid4(), codigo_lote="LOTE-OTRO", cantidad_registros=3, consumos_activos=2
    )
    use_case = ProcesarLote(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: [_fila_excluida()]}),
        duplicidades_source=FakeDuplicidadesDataSource(conteos_por_lote={lote_id: [conteo_drift]}),
    )

    resultado = await use_case.execute("LOTE-1")

    assert resultado.estado_final is EstadoLote.ERROR  # Etapa 1's threshold decides this alone
    assert len(resultado.duplicidades.drift_lotes) == 1
    drift = resultado.duplicidades.drift_lotes[0]
    assert drift.codigo_lote == "LOTE-OTRO"
    assert drift.diferencia == -1
