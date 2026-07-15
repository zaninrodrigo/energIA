"""ProcesarLote: Etapa 1's orchestrating use case (US-006 + US-010 trigger, AI_ENGINE_SPEC.md
§2-§4), extended with Etapa 2's duplicidades detection (US-007, §5), Etapa 3's feature
generation (US-008, §6) and Etapa 4's statistical indicators (US-009, §7).

Flow (AI_ENGINE_SPEC.md §2.1, STEP 0 correction):
  (a) the lote must exist and be `Pendiente` or `Error` (retry) -- `Procesando` is a 409
      in-progress conflict, `Procesado` a 409 idempotent-terminal conflict (RD-010).
  (b) completeness gate (`domain/completitud.py`) -- not ready raises `LoteNoListoError` (422),
      with NO state change.
  (c) optimistic transition to `Procesando` (`LoteProcesamientoPort.transicionar_estado`) -- a
      lost race (a concurrent trigger already moved the lote) raises `LoteEnProgresoError` (409).
  (d) fetch the lote's chain (`ValidacionDataSource`).
  (e) RE-COUNT active consumos for the lote, in the SAME transaction, and compare against the
      count the gate certified in (b). A mismatch -- a concurrent consumo insert for this SAME
      lote landed in the window between (b) and this recount, so the chain just fetched in (d)
      is no longer the cohort the gate certified -- raises `LoteModificadoError` (409). This
      does NOT close that window entirely: see `LoteModificadoError`'s docstring and
      AI_ENGINE_SPEC.md §2.5 for the residual micro-race between this recount and the final
      `commit()` (`presentation/routes.py`).
  (f) run Etapa 1's checks (`domain.informe_validacion.construir_informe`) over the fetched chain.
  (g) run Etapa 2's duplicidades detection (`domain.duplicidades.construir_informe_duplicidades`)
      over EVERY suministro of THIS lote -- UNCONDITIONALLY, including suministros step (f) just
      excluded (`domain/duplicidades.py`'s module docstring explains why) -- ANNOTATION ONLY
      (DEC-005, §5): this never changes `estado_final`, which step (h) below decides from
      `informe` alone, exactly as it did before Etapa 2 existed. FIX 3 (reviewer finding,
      documented not changed): an exception raised HERE (e.g. `_construir_duplicidades`'s fetches
      failing) rolls back the WHOLE transaction, same as a `fetch_chain` failure in step (d) --
      step (c)'s `Procesando` transition is undone too, and the lote is left exactly where it was
      (`Pendiente`/`Error`); this is the SAME single-transaction, all-or-nothing design as the
      rest of this flow (see the docstring paragraph below), intentional consistency-over-partials
      rather than an accidental side effect of Etapa 2 sharing this transaction -- see
      AI_ENGINE_SPEC.md §2.5's table.
  (h) run Etapa 3 (feature generation, `domain.features.construir_feature_vector`) + Etapa 4
      (statistical indicators, `domain.features.enriquecer_con_indicadores_cohorte`) over every
      suministro of THIS lote NOT excluded by step (f) (`_generar_features`'s docstring) --
      UNCONDITIONALLY, same "runs regardless of the 95% outcome" policy step (g) already
      established for Etapa 2 (see that method's own docstring for the honest v1 design note).
      Persists via `FeatureVectorRepository.guardar_batch` (upsert, same transaction).
  (i) transition `Procesando` -> `Procesado` (>= 95% valid) or `Procesando` -> `Error` (< 95%).
      `assert`ed to succeed: under the row lock held since step (c), a `False` return here would
      mean a broken invariant, not a normal race (protects a future refactor that might
      accidentally release that lock early, e.g. an intermediate commit).
  (j) return the full `InformeValidacion` + `InformeDuplicidades` + `ResumenFeatures` plus the
      final `EstadoLote`.

Nothing here calls `session.commit()` -- see `presentation/routes.py`'s docstring for why a
SINGLE commit at the very end (after step (h), not after step (c) too) is what keeps this whole
sequence atomic without extra locking: Postgres's own row lock on the `UPDATE ... WHERE id =
lote_id` from step (c) blocks any concurrent `procesar` call against the SAME lote until this
transaction commits or rolls back, so a crash between (c) and (h) rolls the WHOLE sequence back
(the lote never gets stuck at `Procesando` with nothing to show for it) instead of leaving a
stuck row the way a two-commit design would. That SAME rollback is what reverts (c)'s transition
when (e) raises `LoteModificadoError`.
"""

from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from energia.contexts.motor.domain.completitud import ResultadoCompletitud, evaluar_completitud
from energia.contexts.motor.domain.duplicidades import (
    InformeDuplicidades,
    construir_informe_duplicidades,
)
from energia.contexts.motor.domain.features import (
    FeatureConsumoRow,
    ResumenFeatures,
    construir_feature_vector,
    enriquecer_con_indicadores_cohorte,
    resumir_features,
)
from energia.contexts.motor.domain.informe_validacion import InformeValidacion, construir_informe
from energia.contexts.motor.domain.lote_estado import EstadoLote
from energia.contexts.motor.domain.ports import (
    DuplicidadesDataSource,
    FeaturesDataSource,
    FeatureVectorParaGuardar,
    FeatureVectorRepository,
    LoteProcesamientoPort,
    ValidacionDataSource,
)


class LoteNoEncontradoError(Exception):
    """No (non-soft-deleted) lote exists with the given `codigo_lote` -- 404."""


class LoteEnProgresoError(Exception):
    """The lote is currently `Procesando` -- either it already was when `execute()` started, or
    a concurrent call won the race to transition it there -- 409."""


class LoteYaProcesadoError(Exception):
    """The lote is already `Procesado` -- RD-010, terminal -- 409."""


class LoteModificadoError(Exception):
    """A concurrent consumo insert for this SAME lote landed between the completeness gate
    (step b) and the recount right after `fetch_chain` (step e) -- the analyzed cohort no longer
    matches the gate-certified count -- 409. The caller should retry: the single-transaction
    rollback (see module docstring) reverts step (c)'s `Procesando` transition automatically, so
    the lote is left exactly where it was (`Pendiente`/`Error`), never stuck mid-analysis.

    This does NOT close the race window entirely -- see AI_ENGINE_SPEC.md §2.5 for the residual
    micro-race between this recount and the final `commit()`, and the operational mitigation
    (do not import consumos into a lote while triggering its processing)."""


class LoteNoListoError(Exception):
    """The lote's data load is not complete yet (AI_ENGINE_SPEC.md §2.1) -- 422. No state change
    is made when this is raised."""

    def __init__(self, completitud: ResultadoCompletitud) -> None:
        self.completitud = completitud
        super().__init__(completitud.motivo)


@dataclass(frozen=True, slots=True)
class ResultadoProcesamiento:
    """`ProcesarLote.execute()`'s return value: the final estado plus Etapas 1-4's full reports.
    `duplicidades` never influences `estado_final` (DEC-005: annotate only, §5); `features`
    (Etapas 3-4, §6-§7) runs unconditionally too, over every NON-EXCLUDED suministro of the lote,
    the same "always runs after Etapa 2, regardless of the 95% outcome" policy `duplicidades`
    already established -- see `_generar_features`'s docstring for why this v1 choice is
    reasonable and where it is flagged as a deliberate, honest design note."""

    estado_final: EstadoLote
    informe: InformeValidacion
    duplicidades: InformeDuplicidades
    features: ResumenFeatures


class ProcesarLote:
    """US-006 + US-010 + US-007 + US-008 + US-009: valida la integridad de un lote completo
    (Etapa 1), detecta duplicidades (Etapa 2, anotación únicamente), genera los feature vectors
    (Etapa 3) y calcula los indicadores estadísticos (Etapa 4) para cada suministro no excluido,
    y decide el estado final del lote."""

    def __init__(
        self,
        *,
        lote_port: LoteProcesamientoPort,
        validacion_source: ValidacionDataSource,
        duplicidades_source: DuplicidadesDataSource,
        features_source: FeaturesDataSource,
        feature_vector_repository: FeatureVectorRepository,
    ) -> None:
        self._lote_port = lote_port
        self._validacion_source = validacion_source
        self._duplicidades_source = duplicidades_source
        self._features_source = features_source
        self._feature_vector_repository = feature_vector_repository

    async def execute(self, codigo_lote: str) -> ResultadoProcesamiento:
        actual = await self._lote_port.obtener_estado_actual(codigo_lote)
        if actual is None:
            raise LoteNoEncontradoError(codigo_lote)

        if actual.estado is EstadoLote.PROCESANDO:
            raise LoteEnProgresoError(codigo_lote)
        if actual.estado is EstadoLote.PROCESADO:
            raise LoteYaProcesadoError(codigo_lote)
        # actual.estado is PENDIENTE or ERROR here -- both are retry-eligible entry points.

        consumos_activos = await self._lote_port.contar_consumos_activos(actual.id)
        completitud = evaluar_completitud(
            cantidad_registros=actual.cantidad_registros, consumos_activos=consumos_activos
        )
        if not completitud.completo:
            raise LoteNoListoError(completitud)

        movido = await self._lote_port.transicionar_estado(
            actual.id, desde=frozenset({actual.estado}), hacia=EstadoLote.PROCESANDO
        )
        if not movido:
            raise LoteEnProgresoError(codigo_lote)

        filas = await self._validacion_source.fetch_chain(actual.id)

        # Re-count, in the SAME transaction, right after fetch_chain: a concurrent consumo
        # insert for this SAME lote landing between the gate (above) and this recount would
        # otherwise silently change the analyzed cohort vs. the certified count (see module
        # docstring's step (e) and `LoteModificadoError`'s docstring).
        consumos_activos_tras_fetch = await self._lote_port.contar_consumos_activos(actual.id)
        if consumos_activos_tras_fetch != completitud.consumos_activos:
            raise LoteModificadoError(codigo_lote)

        informe = construir_informe(actual.id, list(filas))
        duplicidades = await self._construir_duplicidades(actual.id)
        features = await self._generar_features(actual.id, informe, duplicidades)

        estado_final = EstadoLote.PROCESADO if informe.umbral_cumplido else EstadoLote.ERROR
        movido_final = await self._lote_port.transicionar_estado(
            actual.id, desde=frozenset({EstadoLote.PROCESANDO}), hacia=estado_final
        )
        assert movido_final, (
            f"transición final Procesando -> {estado_final.value} devolvió False para el lote "
            f"{codigo_lote}: imposible hoy bajo el row lock sostenido desde el paso (c) hasta "
            "el commit final -- ver módulo docstring"
        )

        return ResultadoProcesamiento(
            estado_final=estado_final, informe=informe, duplicidades=duplicidades, features=features
        )

    async def _construir_duplicidades(self, lote_id: UUID) -> InformeDuplicidades:
        """Etapa 2 (module docstring's step (g)): fetch the relevant cross-lote history and run
        every `domain.duplicidades` detector over it -- UNCONDITIONALLY over every suministro
        with an active consumo in `lote_id`, deliberately NOT filtered by Etapa 1's exclusions
        (`domain/duplicidades.py`'s module docstring explains why: a suministro V5 just excluded
        from THIS lote's scoring is exactly the case whose overlap Etapa 2 must still mark, so a
        FUTURE lote's feature windows know to skip it)."""
        periodos = await self._duplicidades_source.fetch_historial_periodos(lote_id)
        lecturas = await self._duplicidades_source.fetch_historial_lecturas(lote_id)
        conteos_lotes = await self._duplicidades_source.fetch_conteos_lotes_relacionados(lote_id)

        return construir_informe_duplicidades(
            lote_id,
            periodos=list(periodos),
            lecturas=list(lecturas),
            conteos_lotes=list(conteos_lotes),
        )

    async def _generar_features(
        self, lote_id: UUID, informe: InformeValidacion, duplicidades: InformeDuplicidades
    ) -> ResumenFeatures:
        """Etapa 3 (US-008, §6) + Etapa 4 (US-009, §7): build and persist one `FeatureVector` per
        NON-EXCLUDED suministro of `lote_id` (mission directive #1 -- Etapa 1's `informe.
        exclusiones` are skipped, exactly like the scoring branches downstream would skip them),
        after Etapa 2.

        **Deliberate v1 design note: this runs UNCONDITIONALLY, regardless of `informe.
        umbral_cumplido`.** AI_ENGINE_SPEC.md §3's pipeline draws stages 1->2->3->4->... as one
        sequential flow with no "abort after stage 1" gate drawn between stages 2 and 3, and
        Etapa 2 (`_construir_duplicidades`) already established the precedent of running
        unconditionally, never gated by Etapa 1's 95% threshold (DEC-005). Mirroring that here
        keeps `feature_vectors` populated even for a lote that lands in `Error` (DEC-004) -- which
        matters for a retry: `Error -> Procesando` re-runs Etapa 3-4 over the SAME rows and
        upserts (never duplicates) the SAME vectors, so a corrected re-import naturally overwrites
        stale ones instead of leaving the previous attempt's vectors orphaned. Not explicit in
        AI_ENGINE_SPEC.md §6/§7 either way -- flagged here as an honest, reasoned deviation rather
        than a silent assumption.

        **Conflicted-period exclusion (DEC-005, §6 note):** every `consumo_id` appearing in
        `duplicidades.periodos_conflictivos` (both sides of each pair) is dropped from the kwh
        history BEFORE it ever reaches `construir_feature_vector` -- see `domain/features.py`'s
        module docstring for why the domain layer itself stays unaware of "conflicted" entirely,
        **except for F10** (FIX 1, reviewer finding, CRITICAL): the UNFILTERED history (conflicted
        rows included) is ALSO built here and passed as `historial_suministro_completo`, used by
        `construir_feature_vector` only to compute `zero_consumption_streak` -- a conflicted period
        that had real, nonzero consumption must still break a zero-streak, or two zero periods
        flanking it would look artificially adjacent (see that function's docstring).
        """
        excluidos = {exclusion.suministro_id for exclusion in informe.exclusiones}
        suministros_con_conflicto = {
            entrada.suministro_id for entrada in duplicidades.periodos_conflictivos
        }
        consumos_conflictivos = {
            periodo.consumo_id
            for entrada in duplicidades.periodos_conflictivos
            for periodo in entrada.periodos
        }

        historial_filas = await self._features_source.fetch_historial_kwh(lote_id)
        metadata_filas = await self._features_source.fetch_metadata_suministros(lote_id)
        conteo_filas = await self._features_source.fetch_prior_anomaly_counts(lote_id)

        historial_por_suministro: dict[UUID, list[FeatureConsumoRow]] = defaultdict(list)
        # FIX 1 (reviewer finding, CRITICAL): the UNFILTERED history -- conflicted rows included
        # -- built alongside the filtered one above, for F10 `zero_consumption_streak` only (see
        # `_generar_features`'s docstring and `domain/features.py`'s module docstring).
        historial_completo_por_suministro: dict[UUID, list[FeatureConsumoRow]] = defaultdict(list)
        for fila in historial_filas:
            historial_completo_por_suministro[fila.suministro_id].append(fila)
            if fila.consumo_id in consumos_conflictivos:
                continue
            historial_por_suministro[fila.suministro_id].append(fila)

        conteos_por_suministro = {fila.suministro_id: fila.cantidad for fila in conteo_filas}

        vectores = []
        for metadata in metadata_filas:
            if metadata.suministro_id in excluidos:
                continue
            vector = construir_feature_vector(
                suministro_id=metadata.suministro_id,
                lote_id=lote_id,
                historial_suministro=historial_por_suministro.get(metadata.suministro_id, []),
                historial_suministro_completo=historial_completo_por_suministro.get(
                    metadata.suministro_id, []
                ),
                categoria_tarifaria_id=metadata.categoria_tarifaria_id,
                localidad=metadata.localidad,
                fecha_alta=metadata.fecha_alta,
                prior_anomaly_count=conteos_por_suministro.get(metadata.suministro_id, 0),
                tiene_periodos_conflictivos=metadata.suministro_id in suministros_con_conflicto,
            )
            if vector is not None:
                vectores.append(vector)

        vectores = enriquecer_con_indicadores_cohorte(vectores)

        await self._feature_vector_repository.guardar_batch(
            [
                FeatureVectorParaGuardar(
                    suministro_id=vector.suministro_id,
                    lote_id=vector.lote_id,
                    features=vector.features,
                )
                for vector in vectores
            ]
        )

        return resumir_features(vectores)
