"""Unit tests for `ProcesarLote` (US-006 + US-010 trigger, AI_ENGINE_SPEC.md §2-§4) against
plain in-memory fakes of `LoteProcesamientoPort`/`ValidacionDataSource` -- no database."""

import functools
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from energia.contexts.motor.application import procesar_lote as procesar_lote_module
from energia.contexts.motor.application.procesar_lote import (
    LoteEnProgresoError,
    LoteModificadoError,
    LoteNoEncontradoError,
    LoteNoListoError,
    LoteYaProcesadoError,
    ProcesarLote,
)
from energia.contexts.motor.domain.isolation_forest import (
    ALGORITMO_ISOLATION_FOREST,
    CLASIFICACION_CRITICO,
    SCOPE_GLOBAL,
    agrupar_para_entrenamiento,
    bandear_clasificacion,
    construir_nombre_modelo,
)
from energia.contexts.motor.domain.lote_estado import EstadoLote
from energia.contexts.motor.domain.ports import (
    ConsumoValidacionRow,
    EstadoLoteActual,
    FeatureConsumoRow,
    FeatureVectorParaGuardar,
    LecturaHistorialRow,
    LoteConteoRow,
    PeriodoHistorialRow,
    PrediccionParaGuardar,
    PriorAnomalyCountRow,
    ResultadoIAParaGuardar,
    SuministroMetadataRow,
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


@dataclass
class FakeFeaturesDataSource:
    """In-memory fake of `FeaturesDataSource` (Etapa 3, US-008) -- empty dicts by default, so
    every existing Etapa 1/2 test can pass one without caring about Etapa 3-4's own fixtures."""

    historial_por_lote: dict[UUID, list[FeatureConsumoRow]] = field(default_factory=dict)
    metadata_por_lote: dict[UUID, list[SuministroMetadataRow]] = field(default_factory=dict)
    conteos_por_lote: dict[UUID, list[PriorAnomalyCountRow]] = field(default_factory=dict)

    async def fetch_historial_kwh(self, lote_id: UUID) -> list[FeatureConsumoRow]:
        return self.historial_por_lote.get(lote_id, [])

    async def fetch_metadata_suministros(self, lote_id: UUID) -> list[SuministroMetadataRow]:
        return self.metadata_por_lote.get(lote_id, [])

    async def fetch_prior_anomaly_counts(self, lote_id: UUID) -> list[PriorAnomalyCountRow]:
        return self.conteos_por_lote.get(lote_id, [])


@dataclass
class FakeFeatureVectorRepository:
    """In-memory fake of `FeatureVectorRepository` -- records every batch passed to
    `guardar_batch` (never actually persists anything) so a test can assert what `ProcesarLote`
    would have written to `feature_vectors`."""

    guardados: list[FeatureVectorParaGuardar] = field(default_factory=list)

    async def guardar_batch(self, vectores: Sequence[FeatureVectorParaGuardar]) -> None:
        self.guardados.extend(vectores)


@dataclass
class FakeIsolationForestScorer:
    """In-memory fake of `IsolationForestScorer` (Etapa 6, AI_ENGINE_SPEC.md §9) -- deterministic,
    no sklearn: returns `-sum(fila)` per row (a row with LARGER feature values gets a MORE
    NEGATIVE fake score, standing in for "more anomalous" without needing real fitting in these
    WIRING tests -- the real scorer's own smoke tests live in `tests/unit/contexts/motor/
    infrastructure/test_isolation_forest_scorer.py`). Records every matrix it was called with."""

    llamadas: list[list[tuple[float, ...]]] = field(default_factory=list)

    async def ajustar_y_puntuar(self, matriz: Sequence[Sequence[float]]) -> Sequence[float]:
        self.llamadas.append([tuple(fila) for fila in matriz])
        return [-sum(fila) for fila in matriz]


@dataclass
class FakeModelosIaRepository:
    """In-memory fake of `ModelosIaRepository` -- records every `registrar_fit` call (never
    touches a database); returns a fresh `UUID` per call (this file tests WIRING, not the real
    upsert/single-Activo-per-nombre semantics -- those are `tests/integration/contexts/motor/
    test_modelos_ia_repository_integration.py`'s job, against the real DB)."""

    llamadas: list[dict[str, str]] = field(default_factory=list)

    async def registrar_fit(self, *, nombre: str, version: str, algoritmo: str) -> UUID:
        self.llamadas.append({"nombre": nombre, "version": version, "algoritmo": algoritmo})
        return uuid4()


@dataclass
class FakePrediccionesRepository:
    """In-memory fake of `PrediccionesRepository` -- records every `reemplazar_lote` call (never
    touches a database)."""

    llamadas: list[tuple[UUID, list[PrediccionParaGuardar]]] = field(default_factory=list)

    async def reemplazar_lote(self, lote_id: UUID, filas: Sequence[PrediccionParaGuardar]) -> None:
        self.llamadas.append((lote_id, list(filas)))


@dataclass
class FakeResultadosIaRepository:
    """In-memory fake of `ResultadosIaRepository` (Etapas 7-8, AI_ENGINE_SPEC.md §10/§11/§14) --
    records every `persistir` call (never touches a database); returns a fresh `UUID` per
    suministro (this file tests WIRING, not the real upsert/backfill/soft-delete-replace
    semantics -- those are `tests/integration/contexts/motor/
    test_resultados_ia_repository_integration.py`'s job, against the real DB)."""

    llamadas: list[tuple[UUID, list[ResultadoIAParaGuardar]]] = field(default_factory=list)

    async def persistir(
        self, lote_id: UUID, entradas: Sequence[ResultadoIAParaGuardar]
    ) -> dict[UUID, UUID]:
        self.llamadas.append((lote_id, list(entradas)))
        return {entrada.suministro_id: uuid4() for entrada in entradas}


def _construir_use_case(
    *,
    lote_port: FakeLoteProcesamientoPort,
    validacion_source: FakeValidacionDataSource,
    duplicidades_source: FakeDuplicidadesDataSource,
    features_source: FakeFeaturesDataSource,
    feature_vector_repository: FakeFeatureVectorRepository,
    isolation_forest_scorer: FakeIsolationForestScorer | None = None,
    modelos_ia_repository: FakeModelosIaRepository | None = None,
    predicciones_repository: FakePrediccionesRepository | None = None,
    resultados_ia_repository: FakeResultadosIaRepository | None = None,
) -> ProcesarLote:
    """Builds a `ProcesarLote` with sensible Etapa-6/Etapa-7-8 fakes by default, so every earlier
    test keeps working unchanged (only this ONE factory function needs updating when a new stage's
    constructor dependency lands, not every call site) -- stage-focused tests pass their OWN fakes
    explicitly to inspect what was recorded."""
    return ProcesarLote(
        lote_port=lote_port,
        validacion_source=validacion_source,
        duplicidades_source=duplicidades_source,
        features_source=features_source,
        feature_vector_repository=feature_vector_repository,
        isolation_forest_scorer=isolation_forest_scorer or FakeIsolationForestScorer(),
        modelos_ia_repository=modelos_ia_repository or FakeModelosIaRepository(),
        predicciones_repository=predicciones_repository or FakePrediccionesRepository(),
        resultados_ia_repository=resultados_ia_repository or FakeResultadosIaRepository(),
    )


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
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({}),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=FakeFeaturesDataSource(),
        feature_vector_repository=FakeFeatureVectorRepository(),
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
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({}),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=FakeFeaturesDataSource(),
        feature_vector_repository=FakeFeatureVectorRepository(),
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
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({}),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=FakeFeaturesDataSource(),
        feature_vector_repository=FakeFeatureVectorRepository(),
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
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({}),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=FakeFeaturesDataSource(),
        feature_vector_repository=FakeFeatureVectorRepository(),
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
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: filas}),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=FakeFeaturesDataSource(),
        feature_vector_repository=FakeFeatureVectorRepository(),
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
    # Etapas 3-4 (US-008/US-009): an empty `FakeFeaturesDataSource` (no metadata rows) means no
    # suministro is analyzable -- `resultado.features` is still a well-formed `ResumenFeatures`,
    # every count 0, not `None`/absent.
    assert resultado.features.suministros_con_vector == 0
    assert resultado.features.cold_starts == 0
    assert resultado.features.con_periodos_conflictivos == 0


async def test_complete_lote_below_threshold_transitions_to_error() -> None:
    lote_id = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-1", estado=EstadoLote.PENDIENTE, cantidad_registros=1
        ),
        consumos_activos=1,
    )
    fila_invalida = _fila_excluida()
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: [fila_invalida]}),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=FakeFeaturesDataSource(),
        feature_vector_repository=FakeFeatureVectorRepository(),
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
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: [_fila_valida()]}),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=FakeFeaturesDataSource(),
        feature_vector_repository=FakeFeatureVectorRepository(),
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
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: [_fila_valida()]}),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=FakeFeaturesDataSource(),
        feature_vector_repository=FakeFeatureVectorRepository(),
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

    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource(
            {lote_id: [_fila_valida()]}, on_fetch=_simular_insert_concurrente
        ),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=FakeFeaturesDataSource(),
        feature_vector_repository=FakeFeatureVectorRepository(),
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
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: [_fila_valida()]}),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=FakeFeaturesDataSource(),
        feature_vector_repository=FakeFeatureVectorRepository(),
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
    """A `ConsumoValidacionRow` Etapa 1 EXCLUDES (V7: dias_facturados <= 0). Not V1 -- V1 is
    informative-only now (CHECKS_INFORMATIVOS), it annotates but never excludes."""
    base = _fila_valida(suministro_id)
    return ConsumoValidacionRow(
        consumo_id=base.consumo_id,
        suministro_id=base.suministro_id,
        fecha_inicio=base.fecha_inicio,
        fecha_fin=base.fecha_fin,
        dias_facturados=0,
        kwh=base.kwh,
        lectura_id=base.lectura_id,
        lectura_anterior=base.lectura_anterior,
        lectura_actual=base.lectura_actual,
        lectura_dias_facturados=0,
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
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: [_fila_valida()]}),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=FakeFeaturesDataSource(),
        feature_vector_repository=FakeFeatureVectorRepository(),
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
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource(
            {lote_id: [_fila_valida(suministro_ok), _fila_excluida(suministro_excluido)]}
        ),
        duplicidades_source=duplicidades_source,
        features_source=FakeFeaturesDataSource(),
        feature_vector_repository=FakeFeatureVectorRepository(),
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
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: [_fila_excluida()]}),
        duplicidades_source=FakeDuplicidadesDataSource(conteos_por_lote={lote_id: [conteo_drift]}),
        features_source=FakeFeaturesDataSource(),
        feature_vector_repository=FakeFeatureVectorRepository(),
    )

    resultado = await use_case.execute("LOTE-1")

    assert resultado.estado_final is EstadoLote.ERROR  # Etapa 1's threshold decides this alone
    assert len(resultado.duplicidades.drift_lotes) == 1
    drift = resultado.duplicidades.drift_lotes[0]
    assert drift.codigo_lote == "LOTE-OTRO"
    assert drift.diferencia == -1


# ---------------------------------------------------------------------------------------------
# Etapas 3-4 -- feature generation + statistical indicators (US-008/US-009, AI_ENGINE_SPEC.md
# §6/§7). These are WIRING tests: the feature/indicator MATH itself is unit-tested exhaustively
# in `tests/unit/contexts/motor/domain/test_features.py` against hand-computed values -- what
# matters here is that `_generar_features` correctly (a) skips Etapa 1's excluded suministros,
# (b) filters Etapa 2's conflicted consumo_ids out of the kwh history before scoring, and (c)
# hands the result to `FeatureVectorRepository.guardar_batch`.
# ---------------------------------------------------------------------------------------------


def _metadata(
    suministro_id: UUID,
    *,
    categoria_id: UUID | None = None,
    numero_suministro: str | None = None,
    estado: str = "Activo",
    categoria_tarifaria_nombre: str = "Residencial",
) -> SuministroMetadataRow:
    return SuministroMetadataRow(
        suministro_id=suministro_id,
        categoria_tarifaria_id=categoria_id or uuid4(),
        localidad="Formosa",
        fecha_alta=date(2020, 1, 1),
        numero_suministro=numero_suministro or f"SUM-{suministro_id}",
        estado=estado,
        categoria_tarifaria_nombre=categoria_tarifaria_nombre,
    )


def _historial(
    suministro_id: UUID,
    lote_id: UUID,
    *,
    fecha_inicio: date,
    kwh: str,
    consumo_id: UUID | None = None,
) -> FeatureConsumoRow:
    return FeatureConsumoRow(
        consumo_id=consumo_id or uuid4(),
        suministro_id=suministro_id,
        lote_id=lote_id,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_inicio,
        dias_facturados=30,
        kwh=Decimal(kwh),
    )


async def test_features_generated_only_for_non_excluded_suministros() -> None:
    """A suministro Etapa 1 excludes (V1, no lectura) never gets a `FeatureVector`, even though
    Etapa 3's own data source has metadata/history for it -- mission directive #1."""
    lote_id = uuid4()
    suministro_ok = uuid4()
    suministro_excluido = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-1", estado=EstadoLote.PENDIENTE, cantidad_registros=2
        ),
        consumos_activos=2,
    )
    features_source = FakeFeaturesDataSource(
        historial_por_lote={
            lote_id: [
                _historial(suministro_ok, lote_id, fecha_inicio=date(2024, 1, 1), kwh="100.000"),
                _historial(
                    suministro_excluido, lote_id, fecha_inicio=date(2024, 1, 1), kwh="999.000"
                ),
            ]
        },
        metadata_por_lote={lote_id: [_metadata(suministro_ok), _metadata(suministro_excluido)]},
        conteos_por_lote={lote_id: [PriorAnomalyCountRow(suministro_id=suministro_ok, cantidad=5)]},
    )
    repository = FakeFeatureVectorRepository()
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource(
            {lote_id: [_fila_valida(suministro_ok), _fila_excluida(suministro_excluido)]}
        ),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=features_source,
        feature_vector_repository=repository,
    )

    resultado = await use_case.execute("LOTE-1")

    assert resultado.features.suministros_con_vector == 1
    assert len(repository.guardados) == 1
    guardado = repository.guardados[0]
    assert guardado.suministro_id == suministro_ok
    assert guardado.lote_id == lote_id
    assert guardado.features["avg_consumption"] == 100.0
    assert guardado.features["prior_anomaly_count"] == 5
    assert guardado.features["is_cold_start"] is True  # only 1 period of history


async def test_conflicted_consumo_is_excluded_from_the_feature_window() -> None:
    """A past period marked conflictive by Etapa 2 (DEC-005, both sides of the pair) must be
    dropped from Etapa 3's kwh history BEFORE `construir_feature_vector` ever sees it -- proven
    here by contrast: `avg_consumption` reflects ONLY the current period (100.0), not the mean of
    100 and the excluded 1000."""
    lote_id = uuid4()
    suministro_id = uuid4()
    c_conflictiva = uuid4()
    c_otro_lado = uuid4()
    c_actual = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-1", estado=EstadoLote.PENDIENTE, cantidad_registros=1
        ),
        consumos_activos=1,
    )
    # Two overlapping periods of the SAME suministro (the real-world V5 shape) -- neither
    # belongs to `lote_id`, both end up marked conflictive.
    periodos_conflictivos = [
        PeriodoHistorialRow(
            consumo_id=c_conflictiva,
            suministro_id=suministro_id,
            lote_id=uuid4(),
            fecha_inicio=date(2023, 12, 1),
            fecha_fin=date(2023, 12, 31),
        ),
        PeriodoHistorialRow(
            consumo_id=c_otro_lado,
            suministro_id=suministro_id,
            lote_id=uuid4(),
            fecha_inicio=date(2023, 12, 15),
            fecha_fin=date(2024, 1, 15),
        ),
    ]
    features_source = FakeFeaturesDataSource(
        historial_por_lote={
            lote_id: [
                _historial(
                    suministro_id,
                    uuid4(),
                    fecha_inicio=date(2023, 12, 1),
                    kwh="1000.000",
                    consumo_id=c_conflictiva,
                ),
                _historial(
                    suministro_id,
                    lote_id,
                    fecha_inicio=date(2024, 1, 1),
                    kwh="100.000",
                    consumo_id=c_actual,
                ),
            ]
        },
        metadata_por_lote={lote_id: [_metadata(suministro_id)]},
    )
    repository = FakeFeatureVectorRepository()
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: [_fila_valida(suministro_id)]}),
        duplicidades_source=FakeDuplicidadesDataSource(
            periodos_por_lote={lote_id: periodos_conflictivos}
        ),
        features_source=features_source,
        feature_vector_repository=repository,
    )

    resultado = await use_case.execute("LOTE-1")

    assert resultado.features.suministros_con_vector == 1
    assert resultado.features.con_periodos_conflictivos == 1
    guardado = repository.guardados[0]
    assert guardado.features["avg_consumption"] == 100.0
    assert guardado.features["max_consumption"] == 100.0
    assert guardado.features["has_conflicted_periods"] is True


async def test_conflicted_nonzero_period_still_breaks_the_zero_streak() -> None:
    """FIX 1 (reviewer finding, CRITICAL) wiring test: `_generar_features` must build BOTH the
    filtered history (every other window feature) AND the unfiltered one (F10 only) and pass both
    to `construir_feature_vector` -- proven here by contrast with the pure-domain unit tests in
    `tests/unit/contexts/motor/domain/test_features.py`: Jan=0, Feb=500 (conflicted, excluded from
    the filtered history), Mar=0, Apr=0 (current) -> the persisted `zero_consumption_streak` must
    be `2` (Mar, Apr), NOT `3` (the bug: bridging Jan+Mar+Apr as if Feb's real 500 never
    happened)."""
    lote_id = uuid4()
    suministro_id = uuid4()
    c_enero = uuid4()
    c_febrero_a = uuid4()
    c_febrero_b = uuid4()
    c_marzo = uuid4()
    c_abril_actual = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-1", estado=EstadoLote.PENDIENTE, cantidad_registros=1
        ),
        consumos_activos=1,
    )
    # Two overlapping Feb periods (double-billed, the real-world shape) -- both end up marked
    # conflictive (DEC-005, both sides), both excluded from the filtered history.
    periodos_conflictivos = [
        PeriodoHistorialRow(
            consumo_id=c_febrero_a,
            suministro_id=suministro_id,
            lote_id=uuid4(),
            fecha_inicio=date(2024, 2, 1),
            fecha_fin=date(2024, 2, 15),
        ),
        PeriodoHistorialRow(
            consumo_id=c_febrero_b,
            suministro_id=suministro_id,
            lote_id=uuid4(),
            fecha_inicio=date(2024, 2, 10),
            fecha_fin=date(2024, 2, 29),
        ),
    ]
    features_source = FakeFeaturesDataSource(
        historial_por_lote={
            lote_id: [
                _historial(
                    suministro_id,
                    uuid4(),
                    fecha_inicio=date(2024, 1, 1),
                    kwh="0.000",
                    consumo_id=c_enero,
                ),
                _historial(
                    suministro_id,
                    uuid4(),
                    fecha_inicio=date(2024, 2, 1),
                    kwh="500.000",
                    consumo_id=c_febrero_a,
                ),
                _historial(
                    suministro_id,
                    uuid4(),
                    fecha_inicio=date(2024, 2, 10),
                    kwh="500.000",
                    consumo_id=c_febrero_b,
                ),
                _historial(
                    suministro_id,
                    uuid4(),
                    fecha_inicio=date(2024, 3, 1),
                    kwh="0.000",
                    consumo_id=c_marzo,
                ),
                _historial(
                    suministro_id,
                    lote_id,
                    fecha_inicio=date(2024, 4, 1),
                    kwh="0.000",
                    consumo_id=c_abril_actual,
                ),
            ]
        },
        metadata_por_lote={lote_id: [_metadata(suministro_id)]},
    )
    repository = FakeFeatureVectorRepository()
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: [_fila_valida(suministro_id)]}),
        duplicidades_source=FakeDuplicidadesDataSource(
            periodos_por_lote={lote_id: periodos_conflictivos}
        ),
        features_source=features_source,
        feature_vector_repository=repository,
    )

    resultado = await use_case.execute("LOTE-1")

    assert resultado.features.suministros_con_vector == 1
    assert resultado.features.con_periodos_conflictivos == 1
    guardado = repository.guardados[0]
    assert guardado.features["zero_consumption_streak"] == 2


# ---------------------------------------------------------------------------------------------
# Etapa 5 -- reglas de negocio (AI_ENGINE_SPEC.md §8). These are WIRING tests, same discipline as
# Etapas 3-4's own section above: the rule MATH itself is unit-tested exhaustively in
# `tests/unit/contexts/motor/domain/test_reglas.py` against hand-computed values -- what matters
# here is that `ProcesarLote` (a) evaluates every NON-EXCLUDED suministro that received a
# `FeatureVector` this run, (b) reuses the ALREADY-BUILT vector (never recomputes features), and
# (c) surfaces the result on `ResultadoProcesamiento.reglas`.
# ---------------------------------------------------------------------------------------------


async def test_reglas_reports_zero_hits_for_a_single_period_clean_suministro() -> None:
    """A single-period (cold-start), nonzero suministro breaches no rule at all -- every rule's
    own null-guard suppresses it (no `pct_change`, no `percentile_peer`, no `deviation`)."""
    lote_id = uuid4()
    suministro_id = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-1", estado=EstadoLote.PENDIENTE, cantidad_registros=1
        ),
        consumos_activos=1,
    )
    features_source = FakeFeaturesDataSource(
        historial_por_lote={
            lote_id: [
                _historial(suministro_id, lote_id, fecha_inicio=date(2024, 1, 1), kwh="100.000")
            ]
        },
        metadata_por_lote={lote_id: [_metadata(suministro_id)]},
    )
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: [_fila_valida(suministro_id)]}),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=features_source,
        feature_vector_repository=FakeFeatureVectorRepository(),
    )

    resultado = await use_case.execute("LOTE-1")

    assert resultado.reglas.lote_id == lote_id
    assert resultado.reglas.resumen.suministros_evaluados == 1
    assert resultado.reglas.resumen.suministros_con_hits == 0
    assert resultado.reglas.suministros == ()
    assert resultado.reglas.resumen.hits_por_regla == {
        "R1": 0,
        "R2": 0,
        "R3": 0,
        "R4": 0,
        "R5": 0,
        "R6": 0,
    }


async def test_reglas_fires_r1_end_to_end_reusing_the_built_vector() -> None:
    """Three consecutive zero-kwh periods (racha = 3, R1's threshold), suministro `Activo` (the
    `_metadata` default) -- R1 must fire off the vector Etapa 3-4 already built, with no separate
    feature recomputation. `numero_suministro` (not just the UUID) must surface in the report."""
    lote_id = uuid4()
    suministro_id = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-1", estado=EstadoLote.PENDIENTE, cantidad_registros=1
        ),
        consumos_activos=1,
    )
    features_source = FakeFeaturesDataSource(
        historial_por_lote={
            lote_id: [
                _historial(suministro_id, uuid4(), fecha_inicio=date(2024, 1, 1), kwh="0.000"),
                _historial(suministro_id, uuid4(), fecha_inicio=date(2024, 2, 1), kwh="0.000"),
                _historial(suministro_id, lote_id, fecha_inicio=date(2024, 3, 1), kwh="0.000"),
            ]
        },
        metadata_por_lote={
            lote_id: [_metadata(suministro_id, numero_suministro="SUM-R1", estado="Activo")]
        },
    )
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: [_fila_valida(suministro_id)]}),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=features_source,
        feature_vector_repository=FakeFeatureVectorRepository(),
    )

    resultado = await use_case.execute("LOTE-1")

    assert resultado.reglas.resumen.suministros_evaluados == 1
    assert resultado.reglas.resumen.suministros_con_hits == 1
    assert resultado.reglas.resumen.hits_por_regla["R1"] == 1
    assert len(resultado.reglas.suministros) == 1
    entrada = resultado.reglas.suministros[0]
    assert entrada.suministro_id == suministro_id
    assert entrada.numero_suministro == "SUM-R1"
    assert [hit.regla for hit in entrada.hits] == ["R1"]
    assert entrada.hits[0].tipo == "Persistencia Anómala"
    assert entrada.hits[0].severidad == "Alta"


async def test_reglas_never_evaluates_a_suministro_excluded_by_etapa_1() -> None:
    """A suministro Etapa 1 excludes never receives a `FeatureVector` (Etapas 3-4), so Etapa 5
    never evaluates it either -- it must not count toward `suministros_evaluados` nor appear in
    `reglas.suministros`, mirroring the Etapas 3-4 exclusion test above."""
    lote_id = uuid4()
    suministro_ok = uuid4()
    suministro_excluido = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-1", estado=EstadoLote.PENDIENTE, cantidad_registros=2
        ),
        consumos_activos=2,
    )
    features_source = FakeFeaturesDataSource(
        historial_por_lote={
            lote_id: [
                _historial(suministro_ok, lote_id, fecha_inicio=date(2024, 1, 1), kwh="100.000"),
                _historial(
                    suministro_excluido, lote_id, fecha_inicio=date(2024, 1, 1), kwh="999.000"
                ),
            ]
        },
        metadata_por_lote={lote_id: [_metadata(suministro_ok), _metadata(suministro_excluido)]},
    )
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource(
            {lote_id: [_fila_valida(suministro_ok), _fila_excluida(suministro_excluido)]}
        ),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=features_source,
        feature_vector_repository=FakeFeatureVectorRepository(),
    )

    resultado = await use_case.execute("LOTE-1")

    assert resultado.reglas.resumen.suministros_evaluados == 1
    assert all(
        entrada.suministro_id != suministro_excluido for entrada in resultado.reglas.suministros
    )


# ---------------------------------------------------------------------------------------------
# Etapa 6 -- Isolation Forest (US-011/RF-006, AI_ENGINE_SPEC.md §9). These are WIRING tests, same
# discipline as Etapas 3-5's own sections above: the matrix/grouping/normalization/banding MATH
# itself is unit-tested exhaustively in `tests/unit/contexts/motor/domain/test_isolation_forest.
# py` against hand-computed values (including the per-categoria training-strategy split, DEC-010
# -- this file's own synthetic scale never reaches `MODELO_POR_CATEGORIA_MINIMO`, so every
# scenario below lands in the single "global" scope) -- what matters here is that `ProcesarLote`
# (a) scores exactly the non-excluded population Etapa 5 already evaluated, reusing the SAME
# vectors, (b) never lets Etapa 6 influence `estado_final`, (c) registers one `modelos_ia` fit per
# scope and threads its id into every `predicciones` row for that scope, and (d) surfaces the
# result on `ResultadoProcesamiento.ml`.
# ---------------------------------------------------------------------------------------------


async def test_ml_scores_every_non_excluded_suministro_and_never_touches_estado() -> None:
    lote_id = uuid4()
    suministro_a = uuid4()
    suministro_b = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-ML-1", estado=EstadoLote.PENDIENTE, cantidad_registros=2
        ),
        consumos_activos=2,
    )
    features_source = FakeFeaturesDataSource(
        historial_por_lote={
            lote_id: [
                _historial(suministro_a, lote_id, fecha_inicio=date(2024, 1, 1), kwh="100.000"),
                _historial(suministro_b, lote_id, fecha_inicio=date(2024, 1, 1), kwh="500.000"),
            ]
        },
        metadata_por_lote={
            lote_id: [
                _metadata(suministro_a, numero_suministro="SUM-ML-A"),
                _metadata(suministro_b, numero_suministro="SUM-ML-B"),
            ]
        },
    )
    modelos_ia_repository = FakeModelosIaRepository()
    predicciones_repository = FakePrediccionesRepository()
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource(
            {lote_id: [_fila_valida(suministro_a), _fila_valida(suministro_b)]}
        ),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=features_source,
        feature_vector_repository=FakeFeatureVectorRepository(),
        modelos_ia_repository=modelos_ia_repository,
        predicciones_repository=predicciones_repository,
    )

    resultado = await use_case.execute("LOTE-ML-1")

    # Etapa 6 never influences the estado/95% verdict (mission directive #8).
    assert resultado.estado_final is EstadoLote.PROCESADO

    assert resultado.ml.suministros_scored == 2
    assert resultado.ml.lote_id == lote_id
    assert len(resultado.ml.modelos) == 1
    assert resultado.ml.modelos[0].scope == SCOPE_GLOBAL
    assert resultado.ml.modelos[0].suministros_entrenados == 2

    # One `registrar_fit` call for the single "global" scope (small population, DEC-010).
    assert len(modelos_ia_repository.llamadas) == 1
    assert modelos_ia_repository.llamadas[0]["nombre"] == construir_nombre_modelo(SCOPE_GLOBAL)
    assert modelos_ia_repository.llamadas[0]["algoritmo"] == ALGORITMO_ISOLATION_FOREST

    # One `reemplazar_lote` call, with exactly 2 predicciones rows, correct modelo_ia_id threaded.
    assert len(predicciones_repository.llamadas) == 1
    lote_llamado, filas = predicciones_repository.llamadas[0]
    assert lote_llamado == lote_id
    assert len(filas) == 2
    assert {fila.suministro_id for fila in filas} == {suministro_a, suministro_b}
    modelo_id_esperado = resultado.ml.modelos[0].modelo_ia_id
    assert all(fila.modelo_ia_id == modelo_id_esperado for fila in filas)
    for fila in filas:
        assert 0.0 <= fila.score <= 1.0
        assert fila.clasificacion in {"Normal", "Atención", "Alto Riesgo", "Crítico"}


async def test_ml_top_10_reflects_the_more_anomalous_suministro_first() -> None:
    """`FakeIsolationForestScorer` returns `-sum(fila)` per row -- a suministro whose features
    sum to a LARGER value gets a MORE NEGATIVE fake raw score, i.e. MORE anomalous under DEC-013's
    inverted min-max (module docstring, `domain/isolation_forest.py`). `suministro_b`'s much
    higher `avg_consumption`/`max_consumption`/etc. (kwh=900 vs. kwh=100) must rank #1."""
    lote_id = uuid4()
    suministro_a = uuid4()
    suministro_b = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-ML-2", estado=EstadoLote.PENDIENTE, cantidad_registros=2
        ),
        consumos_activos=2,
    )
    features_source = FakeFeaturesDataSource(
        historial_por_lote={
            lote_id: [
                _historial(suministro_a, lote_id, fecha_inicio=date(2024, 1, 1), kwh="100.000"),
                _historial(suministro_b, lote_id, fecha_inicio=date(2024, 1, 1), kwh="900.000"),
            ]
        },
        metadata_por_lote={
            lote_id: [
                _metadata(suministro_a, numero_suministro="SUM-ML-A"),
                _metadata(suministro_b, numero_suministro="SUM-ML-B"),
            ]
        },
    )
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource(
            {lote_id: [_fila_valida(suministro_a), _fila_valida(suministro_b)]}
        ),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=features_source,
        feature_vector_repository=FakeFeatureVectorRepository(),
    )

    resultado = await use_case.execute("LOTE-ML-2")

    assert resultado.ml.top_10[0].numero_suministro == "SUM-ML-B"
    assert resultado.ml.top_10[0].ml_score_0_100 == pytest.approx(100.0)
    assert resultado.ml.top_10[1].ml_score_0_100 == pytest.approx(0.0)
    assert resultado.ml.top_10[0].clasificacion == bandear_clasificacion(100.0)


async def test_ml_below_threshold_lote_still_gets_scored() -> None:
    """Etapa 6 runs UNCONDITIONALLY, same "runs regardless of the 95% outcome" policy Etapas 2-5
    already established (mission directive #8: never gates on/influences `estado_final`)."""
    lote_id = uuid4()
    suministro_id = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-ML-3", estado=EstadoLote.PENDIENTE, cantidad_registros=2
        ),
        consumos_activos=2,
    )
    features_source = FakeFeaturesDataSource(
        historial_por_lote={
            lote_id: [
                _historial(suministro_id, lote_id, fecha_inicio=date(2024, 1, 1), kwh="100.000")
            ]
        },
        metadata_por_lote={lote_id: [_metadata(suministro_id)]},
    )
    use_case = _construir_use_case(
        lote_port=lote_port,
        # Only ONE of the two declared consumos is valid -- Etapa 1's threshold (< 95%) lands
        # this lote in Error, exactly like the existing Etapa 1 threshold tests above.
        validacion_source=FakeValidacionDataSource(
            {lote_id: [_fila_valida(suministro_id), _fila_excluida()]}
        ),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=features_source,
        feature_vector_repository=FakeFeatureVectorRepository(),
    )

    resultado = await use_case.execute("LOTE-ML-3")

    assert resultado.estado_final is EstadoLote.ERROR
    assert resultado.ml.suministros_scored == 1  # scored anyway, per mission directive #8


async def test_ml_empty_vectores_still_clears_stale_predicciones_and_registers_no_model() -> None:
    """A lote with zero non-excluded suministros (e.g. every one excluded by Etapa 1) has nothing
    to fit -- no `modelos_ia` row is registered, but `predicciones_repository.reemplazar_lote` is
    STILL called (with an empty list) so a retry correctly clears any stale rows from a previous
    attempt (mirrors `SqlPrediccionesRepository`'s own empty-`filas`-still-clears contract)."""
    lote_id = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-ML-4", estado=EstadoLote.PENDIENTE, cantidad_registros=1
        ),
        consumos_activos=1,
    )
    modelos_ia_repository = FakeModelosIaRepository()
    predicciones_repository = FakePrediccionesRepository()
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: [_fila_excluida()]}),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=FakeFeaturesDataSource(),
        feature_vector_repository=FakeFeatureVectorRepository(),
        modelos_ia_repository=modelos_ia_repository,
        predicciones_repository=predicciones_repository,
    )

    resultado = await use_case.execute("LOTE-ML-4")

    assert resultado.ml.suministros_scored == 0
    assert resultado.ml.modelos == ()
    assert modelos_ia_repository.llamadas == []
    assert predicciones_repository.llamadas == [(lote_id, [])]


async def test_ml_multi_group_wiring_scores_every_suministro_once_with_correct_flat_reindex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WARNING (reviewer finding) -- multi-group wiring was untested: every Etapa 6 test above
    stays under `MODELO_POR_CATEGORIA_MINIMO` (1.000 suministros), so `agrupar_para_entrenamiento`
    always returns a SINGLE `"global"` group in practice, never exercising
    `_ejecutar_isolation_forest`'s flat-reindexing loop (`resultados_por_grupo`/`indice_global`,
    `application/procesar_lote.py`) across MORE THAN ONE group. The per-categoria SPLIT math
    itself is already unit-tested at full 1.000+ scale in `tests/unit/contexts/motor/domain/
    test_isolation_forest.py`; what is NEW here is proving `ProcesarLote` threads TWO groups'
    fits/scores back onto the RIGHT suministros end to end.

    `MODELO_POR_CATEGORIA_MINIMO` is impractical to reach in an in-memory wiring test, so this
    test monkeypatches the `agrupar_para_entrenamiento` NAME `procesar_lote` calls down to
    `minimo_categoria=5` -- documented here, nowhere else. Patching the MODULE CONSTANT itself
    would NOT work: `agrupar_para_entrenamiento`'s `minimo_categoria` keyword-only parameter
    default is bound to `MODELO_POR_CATEGORIA_MINIMO`'s value at function-definition (import)
    time, so reassigning the module attribute afterwards has no effect on calls that rely on the
    default. Rebinding the imported name in `procesar_lote`'s own namespace to a `functools.
    partial` with `minimo_categoria=5` is the actual seam this test needs.

    Two groups: a `"Industrial"` categoria with 5 suministros (>= the patched threshold, gets its
    OWN model) plus 2 more suministros in a different, small categoria that pools into the
    `"global"` fallback (< the patched threshold). Every suministro's `kwh` is DISTINCT and
    strictly increasing across the WHOLE population, smallest in the pooled group, biggest in
    `"Industrial"` -- `FakeIsolationForestScorer` (`-sum(fila)` per row) turns that into a
    strictly increasing `ml_score_0_100` by `kwh` (DEC-013: more negative raw score -> higher
    normalized score), a distinguishable-per-group score PATTERN that would catch a flat-
    reindexing bug (a suministro receiving another suministro's score or `modelo_ia_id`)."""
    lote_id = uuid4()
    categoria_industrial = uuid4()
    categoria_chica = uuid4()

    industrial_ids = [uuid4() for _ in range(5)]
    chica_ids = [uuid4() for _ in range(2)]
    # Strictly increasing kwh across the WHOLE population -- the pooled "global" suministros get
    # the SMALLEST values, "Industrial" suministros the LARGEST.
    kwh_chica = ["10.000", "20.000"]
    kwh_industrial = ["1000.000", "2000.000", "3000.000", "4000.000", "5000.000"]

    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id,
            codigo_lote="LOTE-ML-MULTI",
            estado=EstadoLote.PENDIENTE,
            cantidad_registros=7,
        ),
        consumos_activos=7,
    )
    historial = [
        _historial(sid, lote_id, fecha_inicio=date(2024, 1, 1), kwh=kwh)
        for sid, kwh in zip(chica_ids, kwh_chica, strict=True)
    ] + [
        _historial(sid, lote_id, fecha_inicio=date(2024, 1, 1), kwh=kwh)
        for sid, kwh in zip(industrial_ids, kwh_industrial, strict=True)
    ]
    metadata = [
        _metadata(
            sid,
            categoria_id=categoria_chica,
            numero_suministro=f"SUM-CHICA-{i}",
            categoria_tarifaria_nombre="Residencial",
        )
        for i, sid in enumerate(chica_ids)
    ] + [
        _metadata(
            sid,
            categoria_id=categoria_industrial,
            numero_suministro=f"SUM-IND-{i}",
            categoria_tarifaria_nombre="Industrial",
        )
        for i, sid in enumerate(industrial_ids)
    ]
    features_source = FakeFeaturesDataSource(
        historial_por_lote={lote_id: historial}, metadata_por_lote={lote_id: metadata}
    )
    validacion_source = FakeValidacionDataSource(
        {lote_id: [_fila_valida(sid) for sid in chica_ids + industrial_ids]}
    )
    modelos_ia_repository = FakeModelosIaRepository()
    predicciones_repository = FakePrediccionesRepository()

    monkeypatch.setattr(
        procesar_lote_module,
        "agrupar_para_entrenamiento",
        functools.partial(agrupar_para_entrenamiento, minimo_categoria=5),
    )

    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=validacion_source,
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=features_source,
        feature_vector_repository=FakeFeatureVectorRepository(),
        modelos_ia_repository=modelos_ia_repository,
        predicciones_repository=predicciones_repository,
    )

    resultado = await use_case.execute("LOTE-ML-MULTI")

    assert resultado.ml.suministros_scored == 7

    # Two separate `modelos_ia` fits -- one per scope, groups sorted by scope ("Industrial" <
    # "global" lexicographically).
    assert len(modelos_ia_repository.llamadas) == 2
    assert len(resultado.ml.modelos) == 2
    assert resultado.ml.modelos[0].scope == "Industrial"
    assert resultado.ml.modelos[0].suministros_entrenados == 5
    assert resultado.ml.modelos[1].scope == SCOPE_GLOBAL
    assert resultado.ml.modelos[1].suministros_entrenados == 2
    modelo_industrial_id = resultado.ml.modelos[0].modelo_ia_id
    modelo_global_id = resultado.ml.modelos[1].modelo_ia_id
    assert modelo_industrial_id != modelo_global_id

    # A SINGLE `reemplazar_lote` call with exactly 7 rows -- every suministro exactly once.
    assert len(predicciones_repository.llamadas) == 1
    lote_llamado, filas = predicciones_repository.llamadas[0]
    assert lote_llamado == lote_id
    assert len(filas) == 7
    por_suministro = {fila.suministro_id: fila for fila in filas}
    assert set(por_suministro) == set(chica_ids) | set(industrial_ids)

    # Correct group -> modelo threading, un-swapped across groups.
    for sid in industrial_ids:
        assert por_suministro[sid].modelo_ia_id == modelo_industrial_id
    for sid in chica_ids:
        assert por_suministro[sid].modelo_ia_id == modelo_global_id

    # Distinguishable score pattern: strictly increasing with kwh across the WHOLE population --
    # proves the flat re-indexing did not swap any suministro's score with another's.
    ids_por_kwh_creciente = chica_ids + industrial_ids
    scores_por_kwh_creciente = [por_suministro[sid].score for sid in ids_por_kwh_creciente]
    assert scores_por_kwh_creciente == sorted(scores_por_kwh_creciente)
    assert len(set(scores_por_kwh_creciente)) == 7  # every suministro gets a DISTINCT score
    assert max(por_suministro[sid].score for sid in chica_ids) < min(
        por_suministro[sid].score for sid in industrial_ids
    )


# ---------------------------------------------------------------------------------------------
# Etapas 7-8 -- Composición del IRE + Impacto Económico Estimado (AI_ENGINE_SPEC.md §10/§11/§14).
# THE convergence. These are WIRING tests, same discipline as Etapas 3-6's own sections above: the
# IRE/IEE MATH itself is unit-tested exhaustively in `tests/unit/contexts/motor/domain/test_ire.py`
# against hand-computed values -- what matters here is that `ProcesarLote` (a) composes an IRE/IEE
# for exactly the population Etapa 6 scored, reusing `vectores`/`reglas`/`predicciones` (never
# recomputing), (b) calls `ResultadosIaRepository.persistir` once for the whole lote with the
# correct per-suministro payload (score_anomalia/probabilidad/prediccion_id/observaciones/
# anomalias), (c) applies the ML-only anomaly dedup policy correctly, and (d) never lets Etapas 7-8
# influence `estado_final`.
# ---------------------------------------------------------------------------------------------


async def test_ire_iee_populates_resultado_and_persists_for_every_scored_suministro() -> None:
    lote_id = uuid4()
    suministro_id = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-IRE-1", estado=EstadoLote.PENDIENTE, cantidad_registros=1
        ),
        consumos_activos=1,
    )
    features_source = FakeFeaturesDataSource(
        historial_por_lote={
            lote_id: [
                _historial(suministro_id, lote_id, fecha_inicio=date(2024, 1, 1), kwh="100.000")
            ]
        },
        metadata_por_lote={lote_id: [_metadata(suministro_id, numero_suministro="SUM-IRE-1")]},
    )
    resultados_ia_repository = FakeResultadosIaRepository()
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: [_fila_valida(suministro_id)]}),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=features_source,
        feature_vector_repository=FakeFeatureVectorRepository(),
        resultados_ia_repository=resultados_ia_repository,
    )

    resultado = await use_case.execute("LOTE-IRE-1")

    assert resultado.ire.lote_id == lote_id
    assert resultado.ire.suministros_evaluados == 1
    assert resultado.iee.lote_id == lote_id
    assert len(resultado.ire.top_10) == 1
    entrada_informe = resultado.ire.top_10[0]
    assert entrada_informe.suministro_id == suministro_id
    assert entrada_informe.numero_suministro == "SUM-IRE-1"
    assert 0 <= entrada_informe.ire <= 100
    assert entrada_informe.clasificacion in {"Normal", "Atención", "Alto Riesgo", "Crítico"}

    assert len(resultados_ia_repository.llamadas) == 1
    lote_llamado, entradas = resultados_ia_repository.llamadas[0]
    assert lote_llamado == lote_id
    assert len(entradas) == 1
    entrada = entradas[0]
    assert entrada.suministro_id == suministro_id
    assert entrada.lote_id == lote_id
    assert 0.0 <= entrada.probabilidad <= 1.0
    assert entrada.clasificacion == entrada_informe.clasificacion
    assert entrada.ire_valor == entrada_informe.ire
    # Single-period (cold-start) suministro -- no IEE (`domain/ire.py`'s `calcular_iee_kwh`), so
    # the persisted `impacto_economico` factor must be absent (DEC-016: nonzero contributions
    # only) even though it is always COMPUTED (never null-guarded like historial/variación).
    assert entrada.iee_kwh is None
    observaciones = json.loads(entrada.observaciones)
    assert isinstance(observaciones, list)
    factores_persistidos = {item["factor"] for item in observaciones}
    # Single period, zero prior anomalies -- `historial_consumos` (F9 null), `variacion_
    # porcentual` (F5/F6 both null, no previous period), `impacto_economico` (cold-start, no
    # IEE) and `persistencia_anomalias` (F15=0, F10=0 -- genuinely zero, not null, but DEC-016
    # only keeps NONZERO contributions either way) all contribute exactly `0` and are excluded.
    assert "historial_consumos" not in factores_persistidos
    assert "variacion_porcentual" not in factores_persistidos
    assert "impacto_economico" not in factores_persistidos
    assert "persistencia_anomalias" not in factores_persistidos
    assert "inspecciones_anteriores" not in factores_persistidos  # v1: always-zero, never emitted
    # `score_ia` (degenerate single-score ML normalization, `domain/isolation_forest.py`, ->
    # 50.0) / `consumo_promedio` (degenerate single-vector normalization, `domain/ire.py`, ->
    # 50.0) / `categoria_tarifaria` (fixed criticidad lookup, always nonzero) DO resolve to a
    # nonzero value for this exact single-suministro scenario.
    assert factores_persistidos == {"score_ia", "consumo_promedio", "categoria_tarifaria"}
    for item in observaciones:
        assert set(item) == {"factor", "contribution", "reason"}
        assert isinstance(item["contribution"], int | float)
        assert item["contribution"] > 0
        assert isinstance(item["reason"], str) and item["reason"]


async def test_ire_never_influences_estado_final() -> None:
    """Mission directive #8: Etapas 7-8 NEVER affect estado/95% verdict -- a lote landing in
    Error via Etapa 1's own threshold still gets a fully composed `ire`/`iee`."""
    lote_id = uuid4()
    suministro_id = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-IRE-2", estado=EstadoLote.PENDIENTE, cantidad_registros=2
        ),
        consumos_activos=2,
    )
    features_source = FakeFeaturesDataSource(
        historial_por_lote={
            lote_id: [
                _historial(suministro_id, lote_id, fecha_inicio=date(2024, 1, 1), kwh="100.000")
            ]
        },
        metadata_por_lote={lote_id: [_metadata(suministro_id)]},
    )
    use_case = _construir_use_case(
        lote_port=lote_port,
        # Only ONE of the two declared consumos is valid -- Etapa 1's threshold (< 95%) lands
        # this lote in Error, exactly like the existing Etapa 1/6 threshold tests above.
        validacion_source=FakeValidacionDataSource(
            {lote_id: [_fila_valida(suministro_id), _fila_excluida()]}
        ),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=features_source,
        feature_vector_repository=FakeFeatureVectorRepository(),
    )

    resultado = await use_case.execute("LOTE-IRE-2")

    assert resultado.estado_final is EstadoLote.ERROR
    assert resultado.ire.suministros_evaluados == 1  # composed anyway, per mission directive #8


async def test_ire_empty_vectores_still_calls_persistir_with_empty_list() -> None:
    """Mirrors Etapa 6's own `test_ml_empty_vectores_still_clears_stale_predicciones_and_registers_
    no_model` contract: an Error-retry that ends up analyzing NOTHING still calls `persistir` (with
    an empty list) so `SqlResultadosIaRepository` clears stale `anomalias`/`impacto_economico` rows
    from a previous attempt."""
    lote_id = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-IRE-3", estado=EstadoLote.PENDIENTE, cantidad_registros=1
        ),
        consumos_activos=1,
    )
    resultados_ia_repository = FakeResultadosIaRepository()
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: [_fila_excluida()]}),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=FakeFeaturesDataSource(),
        feature_vector_repository=FakeFeatureVectorRepository(),
        resultados_ia_repository=resultados_ia_repository,
    )

    resultado = await use_case.execute("LOTE-IRE-3")

    assert resultado.ire.suministros_evaluados == 0
    assert resultado.iee.suministros_con_iee == 0
    assert resultados_ia_repository.llamadas == [(lote_id, [])]


async def test_ml_only_anomaly_generated_when_critico_and_no_rule_fired() -> None:
    """§8/§10.3's v1 ML-only anomaly policy: a suministro whose ML score alone lands `"Crítico"`
    AND breaches no R1-R6 rule gets exactly one 'Patrón Irregular' anomaly persisted."""
    lote_id = uuid4()
    suministro_normal = uuid4()
    suministro_critico = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-IRE-4", estado=EstadoLote.PENDIENTE, cantidad_registros=2
        ),
        consumos_activos=2,
    )
    # Single period each (cold-start) -- no R1-R6 rule can fire (every rule needs history), so any
    # anomalia persisted here is unambiguously the ML-only 'Patrón Irregular' policy at work.
    features_source = FakeFeaturesDataSource(
        historial_por_lote={
            lote_id: [
                _historial(
                    suministro_normal, lote_id, fecha_inicio=date(2024, 1, 1), kwh="100.000"
                ),
                _historial(
                    suministro_critico, lote_id, fecha_inicio=date(2024, 1, 1), kwh="900.000"
                ),
            ]
        },
        metadata_por_lote={
            lote_id: [
                _metadata(suministro_normal, numero_suministro="SUM-NORMAL"),
                _metadata(suministro_critico, numero_suministro="SUM-CRITICO"),
            ]
        },
    )
    resultados_ia_repository = FakeResultadosIaRepository()
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource(
            {lote_id: [_fila_valida(suministro_normal), _fila_valida(suministro_critico)]}
        ),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=features_source,
        feature_vector_repository=FakeFeatureVectorRepository(),
        resultados_ia_repository=resultados_ia_repository,
    )

    await use_case.execute("LOTE-IRE-4")

    _, entradas = resultados_ia_repository.llamadas[0]
    por_suministro = {entrada.suministro_id: entrada for entrada in entradas}
    assert por_suministro[suministro_normal].anomalias == ()
    anomalias_critico = por_suministro[suministro_critico].anomalias
    assert len(anomalias_critico) == 1
    assert anomalias_critico[0].tipo == "Patrón Irregular"
    assert anomalias_critico[0].severidad == "Crítica"
    assert "aproximación" in (anomalias_critico[0].descripcion or "")


async def test_ml_only_anomaly_not_generated_when_a_rule_already_fired() -> None:
    """Dedup policy: rule hits are canonical -- a suministro whose R1 already fired must NOT ALSO
    get a 'Patrón Irregular' ML-only anomaly, even if its ML score independently lands Crítico."""
    lote_id = uuid4()
    suministro_id = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-IRE-5", estado=EstadoLote.PENDIENTE, cantidad_registros=1
        ),
        consumos_activos=1,
    )
    # Three consecutive zero-kwh periods -- fires R1 (racha = 3, `test_reglas_fires_r1_end_to_
    # end_reusing_the_built_vector`'s own fixture shape). A single suministro's `FakeIsolationForest
    # Scorer` degenerate case (`normalizar_scores_min_max_invertido`'s all-equal-scores branch,
    # `domain/isolation_forest.py`) yields `ml_score_0_100 = 50.0`, banded "Alto Riesgo" -- NOT
    # "Crítico" -- so this scenario alone would not trigger the ML-only policy either way; the
    # assertion below locks in that R1's OWN anomaly is the only one persisted, regardless.
    features_source = FakeFeaturesDataSource(
        historial_por_lote={
            lote_id: [
                _historial(suministro_id, uuid4(), fecha_inicio=date(2024, 1, 1), kwh="0.000"),
                _historial(suministro_id, uuid4(), fecha_inicio=date(2024, 2, 1), kwh="0.000"),
                _historial(suministro_id, lote_id, fecha_inicio=date(2024, 3, 1), kwh="0.000"),
            ]
        },
        metadata_por_lote={
            lote_id: [_metadata(suministro_id, numero_suministro="SUM-R1", estado="Activo")]
        },
    )
    resultados_ia_repository = FakeResultadosIaRepository()
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource({lote_id: [_fila_valida(suministro_id)]}),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=features_source,
        feature_vector_repository=FakeFeatureVectorRepository(),
        resultados_ia_repository=resultados_ia_repository,
    )

    await use_case.execute("LOTE-IRE-5")

    _, entradas = resultados_ia_repository.llamadas[0]
    anomalias = entradas[0].anomalias
    assert [a.tipo for a in anomalias] == ["Persistencia Anómala"]  # R1 only, no ML duplicate


async def test_ml_critico_and_rule_hit_on_the_same_suministro_gets_no_ml_only_duplicate() -> None:
    """WARNING 5 (reviewer finding): `test_ml_only_anomaly_not_generated_when_a_rule_already_
    fired` above proves the dedup policy with a SINGLE-suministro lote, where `FakeIsolationForest
    Scorer`'s degenerate all-equal-scores case (`domain/isolation_forest.py`) always yields
    `ml_score_0_100 = 50.0` ("Alto Riesgo", never "Crítico") -- so that test never actually
    exercised a suministro that is BOTH rule-hit AND INDEPENDENTLY ML-Crítico at the same time.
    This test closes that gap: a multi-suministro lote (a REAL min-max spread, not the degenerate
    50) where one suministro fires R3 (`Incremento Brusco`, an 800% jump, well over the 200%
    threshold) AND independently lands `Crítico` on the ML branch alone (asserted directly off
    the persisted `predicciones` row, not assumed) -- the wiring-level dedup policy
    (`_componer_ire_e_iee`'s docstring) must still persist EXACTLY the rule's own anomalia, no
    ADDITIONAL 'Patrón Irregular' ML-only row."""
    lote_id = uuid4()
    suministro_estable = uuid4()
    suministro_pico = uuid4()
    lote_port = FakeLoteProcesamientoPort(
        lote=EstadoLoteActual(
            id=lote_id, codigo_lote="LOTE-IRE-6", estado=EstadoLote.PENDIENTE, cantidad_registros=2
        ),
        consumos_activos=2,
    )
    # `suministro_pico`: 100 kWh baseline (prior lote) -> 900 kWh this lote -- fires R3 AND, given
    # `FakeIsolationForestScorer`'s `-sum(fila)` fake (dominated here by the consumption-magnitude
    # features), lands at the extreme of this lote's min-max spread against the stable peer --
    # independently `Crítico` on the ML branch alone, asserted below off the real `predicciones`
    # row.
    features_source = FakeFeaturesDataSource(
        historial_por_lote={
            lote_id: [
                _historial(
                    suministro_estable, lote_id, fecha_inicio=date(2024, 3, 1), kwh="100.000"
                ),
                _historial(suministro_pico, uuid4(), fecha_inicio=date(2024, 2, 1), kwh="100.000"),
                _historial(suministro_pico, lote_id, fecha_inicio=date(2024, 3, 1), kwh="900.000"),
            ]
        },
        metadata_por_lote={
            lote_id: [
                _metadata(suministro_estable, numero_suministro="SUM-ESTABLE"),
                _metadata(suministro_pico, numero_suministro="SUM-PICO"),
            ]
        },
    )
    resultados_ia_repository = FakeResultadosIaRepository()
    predicciones_repository = FakePrediccionesRepository()
    use_case = _construir_use_case(
        lote_port=lote_port,
        validacion_source=FakeValidacionDataSource(
            {lote_id: [_fila_valida(suministro_estable), _fila_valida(suministro_pico)]}
        ),
        duplicidades_source=FakeDuplicidadesDataSource(),
        features_source=features_source,
        feature_vector_repository=FakeFeatureVectorRepository(),
        resultados_ia_repository=resultados_ia_repository,
        predicciones_repository=predicciones_repository,
    )

    await use_case.execute("LOTE-IRE-6")

    # Independently ML-Crítico -- proven off the ACTUAL persisted prediction, never assumed.
    _, predicciones = predicciones_repository.llamadas[0]
    prediccion_pico = next(p for p in predicciones if p.suministro_id == suministro_pico)
    assert prediccion_pico.clasificacion == CLASIFICACION_CRITICO
    assert prediccion_pico.score * 100 >= 71

    _, entradas = resultados_ia_repository.llamadas[0]
    anomalias_pico = next(e for e in entradas if e.suministro_id == suministro_pico).anomalias
    tipos = [a.tipo for a in anomalias_pico]
    assert "Incremento Brusco" in tipos
    assert "Patrón Irregular" not in tipos  # the dedup policy: rule hit wins, no ML duplicate
