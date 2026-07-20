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
  (h.1) run Etapa 5 (business rules, `domain.reglas.evaluar_reglas`, AI_ENGINE_SPEC.md §8) over
      every vector step (h) just built -- REUSING it, never recomputing F1-F17/the Etapa 4
      indicators (`_evaluar_reglas`'s docstring). COMPUTE + REPORT only: no `anomalias` row is
      written this stage (that FK requires a `ResultadoIA` row, which requires Etapas 6-7's
      `modelo_ia_id`/`clasificacion` -- `domain/reglas.py`'s module docstring). Hits live only in
      the response's `reglas` field.
  (i) transition `Procesando` -> `Procesado` (>= 95% valid) or `Procesando` -> `Error` (< 95%).
      `assert`ed to succeed: under the row lock held since step (c), a `False` return here would
      mean a broken invariant, not a normal race (protects a future refactor that might
      accidentally release that lock early, e.g. an intermediate commit).
  (j) return the full `InformeValidacion` + `InformeDuplicidades` + `ResumenFeatures` +
      `InformeReglas` plus the final `EstadoLote`.

Nothing here calls `session.commit()` -- see `presentation/routes.py`'s docstring for why a
SINGLE commit at the very end (after step (h), not after step (c) too) is what keeps this whole
sequence atomic without extra locking: Postgres's own row lock on the `UPDATE ... WHERE id =
lote_id` from step (c) blocks any concurrent `procesar` call against the SAME lote until this
transaction commits or rolls back, so a crash between (c) and (h) rolls the WHOLE sequence back
(the lote never gets stuck at `Procesando` with nothing to show for it) instead of leaving a
stuck row the way a two-commit design would. That SAME rollback is what reverts (c)'s transition
when (e) raises `LoteModificadoError`.
"""

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

from energia.contexts.motor.domain.completitud import ResultadoCompletitud, evaluar_completitud
from energia.contexts.motor.domain.duplicidades import (
    InformeDuplicidades,
    construir_informe_duplicidades,
)
from energia.contexts.motor.domain.features import (
    FeatureConsumoRow,
    FeatureVector,
    ResumenFeatures,
    construir_feature_vector,
    enriquecer_con_indicadores_cohorte,
    resumir_features,
)
from energia.contexts.motor.domain.informe_validacion import InformeValidacion, construir_informe
from energia.contexts.motor.domain.ire import (
    InformeIEE,
    InformeIRE,
    ResultadoSuministroParaInforme,
    calcular_iee_kwh,
    calcular_percentil_real_consumo_promedio_lote,
    componer_ire,
    construir_informe_iee,
    construir_informe_ire,
    debe_generar_anomalia_ml,
    normalizar_consumo_promedio_lote,
    normalizar_iee_lote,
)
from energia.contexts.motor.domain.isolation_forest import (
    ALGORITMO_ISOLATION_FOREST,
    GrupoEntrenamiento,
    InformeML,
    ModeloEntrenadoInfo,
    PrediccionSuministro,
    agrupar_para_entrenamiento,
    bandear_clasificacion,
    construir_informe_ml,
    construir_matriz,
    construir_nombre_modelo,
    construir_version_modelo,
    normalizar_scores_min_max_invertido,
)
from energia.contexts.motor.domain.lote_estado import EstadoLote
from energia.contexts.motor.domain.ports import (
    AnomaliaParaGuardar,
    DuplicidadesDataSource,
    FeaturesDataSource,
    FeatureVectorParaGuardar,
    FeatureVectorRepository,
    IsolationForestScorer,
    LoteProcesamientoPort,
    ModelosIaRepository,
    PrediccionesRepository,
    PrediccionParaGuardar,
    ResultadoIAParaGuardar,
    ResultadosIaRepository,
    SuministroMetadataRow,
    ValidacionDataSource,
)
from energia.contexts.motor.domain.reglas import (
    InformeReglas,
    ReglasSuministro,
    construir_informe_reglas,
    evaluar_reglas,
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
    """`ProcesarLote.execute()`'s return value: the final estado plus Etapas 1-6's full reports.
    `duplicidades` never influences `estado_final` (DEC-005: annotate only, §5); `features`
    (Etapas 3-4, §6-§7) runs unconditionally too, over every NON-EXCLUDED suministro of the lote,
    the same "always runs after Etapa 2, regardless of the 95% outcome" policy `duplicidades`
    already established -- see `_generar_features`'s docstring for why this v1 choice is
    reasonable and where it is flagged as a deliberate, honest design note. `reglas` (Etapa 5, §8)
    runs over the SAME non-excluded population that received a `FeatureVector`, reusing it
    (`_evaluar_reglas`'s docstring) -- COMPUTE + REPORT only, no `anomalias` persisted this stage
    (`domain/reglas.py`'s module docstring). `ml` (Etapa 6, US-011/RF-006, §9) ALSO runs over that
    SAME population, reusing the same vectors (`_ejecutar_isolation_forest`'s docstring) -- unlike
    `reglas`, this stage DOES persist (`modelos_ia`/`predicciones`, mission directive #6), but
    still NEVER influences `estado_final` (mission directive #8, same "annotate/compute only"
    policy every prior stage after Etapa 1 already established). `ire`/`iee` (Etapas 7-8, §10/§11,
    THE convergence) compose the 8-factor IRE and the IEE over that SAME population one final
    time, reusing `vectores`/`reglas`/`ml`'s already-computed outputs (`_componer_ire_e_iee`'s
    docstring) -- persists `resultados_ia`/`anomalias`/`ire`/`impacto_economico` atomically
    (`infrastructure/resultados_ia_repository.py`), backfills `feature_vectors.resultado_ia_id`,
    and -- like every stage after Etapa 1 -- NEVER influences `estado_final` (mission directive
    #8)."""

    estado_final: EstadoLote
    informe: InformeValidacion
    duplicidades: InformeDuplicidades
    features: ResumenFeatures
    reglas: InformeReglas
    ml: InformeML
    ire: InformeIRE
    iee: InformeIEE


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
        isolation_forest_scorer: IsolationForestScorer,
        modelos_ia_repository: ModelosIaRepository,
        predicciones_repository: PrediccionesRepository,
        resultados_ia_repository: ResultadosIaRepository,
    ) -> None:
        self._lote_port = lote_port
        self._validacion_source = validacion_source
        self._duplicidades_source = duplicidades_source
        self._features_source = features_source
        self._feature_vector_repository = feature_vector_repository
        self._isolation_forest_scorer = isolation_forest_scorer
        self._modelos_ia_repository = modelos_ia_repository
        self._predicciones_repository = predicciones_repository
        self._resultados_ia_repository = resultados_ia_repository

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
        (
            resumen_features,
            vectores,
            historial_por_suministro,
            metadata_por_suministro,
        ) = await self._generar_features(actual.id, informe, duplicidades)
        reglas = self._evaluar_reglas(
            actual.id, vectores, historial_por_suministro, metadata_por_suministro
        )
        ml, predicciones = await self._ejecutar_isolation_forest(
            actual.id, actual.codigo_lote, vectores, metadata_por_suministro
        )
        ire_informe, iee_informe = await self._componer_ire_e_iee(
            actual.id, vectores, metadata_por_suministro, reglas, predicciones
        )

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
            estado_final=estado_final,
            informe=informe,
            duplicidades=duplicidades,
            features=resumen_features,
            reglas=reglas,
            ml=ml,
            ire=ire_informe,
            iee=iee_informe,
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
    ) -> tuple[
        ResumenFeatures,
        list[FeatureVector],
        dict[UUID, list[FeatureConsumoRow]],
        dict[UUID, SuministroMetadataRow],
    ]:
        """Etapa 3 (US-008, §6) + Etapa 4 (US-009, §7): build and persist one `FeatureVector` per
        NON-EXCLUDED suministro of `lote_id` (mission directive #1 -- Etapa 1's `informe.
        exclusiones` are skipped, exactly like the scoring branches downstream would skip them),
        after Etapa 2.

        Besides the `ResumenFeatures` summary, this also returns the cohort-enriched vectors, the
        FILTERED per-suministro kwh history, and the metadata keyed by suministro_id -- Etapa 5
        (`_evaluar_reglas`) needs all three to evaluate its rules WITHOUT recomputing anything
        Etapa 3-4 already computed (mission directive: "do not recompute features").

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

        metadata_por_suministro = {fila.suministro_id: fila for fila in metadata_filas}
        return (
            resumir_features(vectores),
            vectores,
            historial_por_suministro,
            metadata_por_suministro,
        )

    def _evaluar_reglas(
        self,
        lote_id: UUID,
        vectores: list[FeatureVector],
        historial_por_suministro: dict[UUID, list[FeatureConsumoRow]],
        metadata_por_suministro: dict[UUID, SuministroMetadataRow],
    ) -> InformeReglas:
        """Etapa 5 (business rules, AI_ENGINE_SPEC.md §8): pure, synchronous rule evaluation over
        every vector Etapa 3-4 (`_generar_features`) just built -- REUSING it, never recomputing
        F1-F17/the Etapa 4 indicators (mission directive: "do not recompute features"). Runs over
        exactly the same non-excluded population that received a `FeatureVector` this run --
        nothing more, nothing less (a suministro Etapa 1 excluded never reaches this method at
        all, since it never made it into `vectores` to begin with).

        `historial_por_suministro` is the SAME filtered kwh history used to build those vectors
        (consistent with F5's own source, `domain/reglas.py`'s module docstring) -- passed
        through unchanged, never re-fetched.

        COMPUTE + REPORT only: no `anomalias` row is written here (`domain/reglas.py`'s module
        docstring explains why -- the FK to `resultados_ia` requires Etapas 6-7's
        `modelo_ia_id`/`clasificacion`, which do not exist yet). The hits live only in the
        `InformeReglas` this method returns.
        """
        entradas = [
            ReglasSuministro(
                suministro_id=vector.suministro_id,
                numero_suministro=metadata_por_suministro[vector.suministro_id].numero_suministro,
                hits=evaluar_reglas(
                    vector,
                    historial_por_suministro.get(vector.suministro_id, []),
                    metadata_por_suministro[vector.suministro_id],
                ),
            )
            for vector in vectores
        ]
        return construir_informe_reglas(lote_id, entradas, suministros_evaluados=len(vectores))

    async def _ejecutar_isolation_forest(
        self,
        lote_id: UUID,
        codigo_lote: str,
        vectores: list[FeatureVector],
        metadata_por_suministro: dict[UUID, SuministroMetadataRow],
    ) -> tuple[InformeML, tuple[PrediccionSuministro, ...]]:
        """Etapa 6 (US-011/RF-006, AI_ENGINE_SPEC.md §9): fit + score Isolation Forest (DEC-011/
        DEC-012) over the SAME non-excluded population Etapa 5 (`_evaluar_reglas`) just
        evaluated -- REUSING `vectores`, never recomputing F1-F17/Etapa 4's indicators. NEVER
        influences `estado_final` (mission directive #8) -- like Etapas 2-5, this is a
        compute-and-persist stage the 95%-threshold decision (step (i) of the module docstring)
        never looks at.

        **Training strategy (DEC-010).** `domain.isolation_forest.agrupar_para_entrenamiento`
        splits `vectores` by categoria tarifaria (>= `MODELO_POR_CATEGORIA_MINIMO` suministros in
        THIS lote gets its own model; the rest pool into one `"global"` model) -- one
        `IsolationForestScorer.ajustar_y_puntuar` call, one `modelos_ia` fit
        (`ModelosIaRepository.registrar_fit`), per group. RAW scores from EVERY group are
        collected together and normalized ONCE, per lote (DEC-013, not per group) -- see
        `domain.isolation_forest`'s module docstring for why the normalization scope is the whole
        lote even though FITTING happens per group.

        **Idempotent Error-retry reprocess (mission directive #6).**
        `modelos_ia_repository.registrar_fit` upserts by `(nombre, version)` --
        `construir_version_modelo` is a deterministic function of `codigo_lote` alone, so a retry
        of the SAME lote reuses the SAME `modelos_ia` row instead of colliding with `uq_modelos_
        ia_nombre_version`. `predicciones_repository.reemplazar_lote` deletes this lote's
        previous `predicciones` rows before inserting the fresh set (`predicciones` carries no
        natural unique key to upsert against, `infrastructure/predicciones_repository.py`'s
        docstring) -- called even when `vectores` is empty, so a retry that ends up with nothing
        to score still clears stale rows from a PREVIOUS attempt. Neither write commits here:
        same single-transaction discipline as every other port this use case calls (§2.5).

        Returns the `InformeML` (response summary) PLUS the full `PrediccionSuministro` tuple --
        Etapa 7 (`_componer_ire_e_iee`) needs the per-suministro `modelo_ia_id`/`score_crudo`/
        `score_normalizado`/`clasificacion`/`id` this method already computed, and must not
        recompute or re-fetch any of it (same "reuse, never recompute" discipline as every prior
        stage). `id` (added for Etapa 7) is generated HERE, client-side, once per suministro --
        see `domain/ports.py`'s `PrediccionParaGuardar` docstring for why.
        """
        if not vectores:
            await self._predicciones_repository.reemplazar_lote(lote_id, [])
            return construir_informe_ml(lote_id, modelos=(), predicciones=()), ()

        categoria_nombres = {
            metadata.categoria_tarifaria_id: metadata.categoria_tarifaria_nombre
            for metadata in metadata_por_suministro.values()
        }
        grupos = agrupar_para_entrenamiento(vectores, categoria_nombres)

        modelos: list[ModeloEntrenadoInfo] = []
        resultados_por_grupo: list[tuple[GrupoEntrenamiento, UUID, Sequence[float]]] = []
        scores_crudos: list[float] = []
        for grupo in grupos:
            matriz = construir_matriz(grupo.vectores)
            scores = await self._isolation_forest_scorer.ajustar_y_puntuar(matriz.filas)
            version = construir_version_modelo(codigo_lote)
            modelo_ia_id = await self._modelos_ia_repository.registrar_fit(
                nombre=construir_nombre_modelo(grupo.scope),
                version=version,
                algoritmo=ALGORITMO_ISOLATION_FOREST,
            )
            modelos.append(
                ModeloEntrenadoInfo(
                    scope=grupo.scope,
                    modelo_ia_id=modelo_ia_id,
                    version=version,
                    suministros_entrenados=len(grupo.vectores),
                )
            )
            resultados_por_grupo.append((grupo, modelo_ia_id, scores))
            scores_crudos.extend(scores)

        # DEC-013: normalized ONCE, across every group's raw scores together -- see this
        # method's own docstring and `domain.isolation_forest`'s module docstring for why the
        # normalization scope is the whole lote, not each group.
        normalizados = normalizar_scores_min_max_invertido(scores_crudos)

        predicciones: list[PrediccionSuministro] = []
        indice_global = 0
        for grupo, modelo_ia_id, scores in resultados_por_grupo:
            for vector, score_crudo in zip(grupo.vectores, scores, strict=True):
                normalizado = normalizados[indice_global]
                indice_global += 1
                ml_score_0_100 = normalizado * 100
                metadata = metadata_por_suministro[vector.suministro_id]
                predicciones.append(
                    PrediccionSuministro(
                        id=uuid4(),
                        suministro_id=vector.suministro_id,
                        numero_suministro=metadata.numero_suministro,
                        modelo_ia_id=modelo_ia_id,
                        scope=grupo.scope,
                        score_crudo=score_crudo,
                        score_normalizado=normalizado,
                        ml_score_0_100=ml_score_0_100,
                        clasificacion=bandear_clasificacion(ml_score_0_100),
                    )
                )

        await self._predicciones_repository.reemplazar_lote(
            lote_id,
            [
                PrediccionParaGuardar(
                    id=prediccion.id,
                    modelo_ia_id=prediccion.modelo_ia_id,
                    suministro_id=prediccion.suministro_id,
                    lote_id=lote_id,
                    score=prediccion.score_normalizado,
                    clasificacion=prediccion.clasificacion,
                )
                for prediccion in predicciones
            ],
        )

        return (
            construir_informe_ml(lote_id, modelos=modelos, predicciones=predicciones),
            tuple(predicciones),
        )

    async def _componer_ire_e_iee(
        self,
        lote_id: UUID,
        vectores: list[FeatureVector],
        metadata_por_suministro: dict[UUID, SuministroMetadataRow],
        reglas: InformeReglas,
        predicciones: tuple[PrediccionSuministro, ...],
    ) -> tuple[InformeIRE, InformeIEE]:
        """Etapas 7-8 (AI_ENGINE_SPEC.md §10/§11) -- THE convergence. Reuses `vectores` (Etapa
        3-4), `reglas` (Etapa 5) and `predicciones` (Etapa 6) -- never recomputes a feature, a
        rule hit, or an ML score.

        **Computation order: 8 -> 7 (`domain/ire.py`'s module docstring).** The IEE is one of the
        IRE's own 8 factors (weight 0.10, §10.1) -- `calcular_iee_kwh`/`normalizar_iee_lote` run
        FIRST, THEN `componer_ire` consumes their normalized output. §3's pipeline diagram still
        numbers the STAGES/tables 7 (`ire`) then 8 (`impacto_economico`) -- only the in-memory
        computation is reordered, persistence stays atomic at the end either way.

        **Anomalias dedup policy (§8/§10.3, `domain/ire.py`'s `debe_generar_anomalia_ml`).** Every
        R1-R6 rule hit (Etapa 5) becomes its own `anomalias` row (canonical, causally explicable,
        RN-012); an ADDITIONAL ML-only `'Patrón Irregular'` anomaly is generated ONLY when the ML
        branch's OWN preliminary verdict (`predicciones.clasificacion`) is `"Crítico"` AND no rule
        already fired for that suministro this lote -- never both for the same signal.

        **Persistence (§14, mission directive #6) is ONE call** to `ResultadosIaRepository.
        persistir` for the WHOLE lote (not per-suministro) -- that repository owns the exact
        per-table write order (upsert `resultados_ia` -> backfill `feature_vectors.resultado_
        ia_id` -> replace `anomalias` -> upsert `ire` -> upsert/soft-replace `impacto_economico`),
        all in the SAME transaction as every other write this use case makes. Called even when
        `vectores` is empty (mirrors `_ejecutar_isolation_forest`'s own always-call-to-clear-stale-
        rows contract) so an Error-retry that ends up analyzing nothing still clears a previous
        attempt's rows for this lote.
        """
        predicciones_por_suministro = {
            prediccion.suministro_id: prediccion for prediccion in predicciones
        }
        suministros_con_hits = {entrada.suministro_id for entrada in reglas.suministros}
        hits_por_suministro = {
            entrada.suministro_id: entrada.hits for entrada in reglas.suministros
        }

        # Etapa 8 first (module docstring): per-suministro IEE, then the two per-lote
        # normalizations `componer_ire` (Etapa 7) needs as precomputed inputs.
        iees_por_suministro = {
            vector.suministro_id: calcular_iee_kwh(vector) for vector in vectores
        }
        iees_normalizados = normalizar_iee_lote(iees_por_suministro)
        consumo_promedio_normalizado = normalizar_consumo_promedio_lote(vectores)
        # RN-012 fix: the TRUE percentile (`consumo_promedio` factor's explicability TEXT only,
        # `domain/ire.py`'s `_factor_consumo_promedio` docstring) -- a SEPARATE per-lote
        # normalization from `consumo_promedio_normalizado` above, which keeps driving the
        # factor's WEIGHT unchanged.
        consumo_promedio_percentil_real = calcular_percentil_real_consumo_promedio_lote(vectores)

        entradas_persistencia: list[ResultadoIAParaGuardar] = []
        entradas_informe: list[ResultadoSuministroParaInforme] = []
        for vector in vectores:
            suministro_id = vector.suministro_id
            prediccion = predicciones_por_suministro[suministro_id]
            metadata = metadata_por_suministro[suministro_id]

            composicion = componer_ire(
                vector,
                ml_score_0_100=prediccion.ml_score_0_100,
                iee_kwh=iees_por_suministro[suministro_id],
                iee_normalizado_0_100=iees_normalizados[suministro_id],
                consumo_promedio_normalizado_0_100=consumo_promedio_normalizado[suministro_id],
                consumo_promedio_percentil_real=consumo_promedio_percentil_real[suministro_id],
                categoria_nombre=metadata.categoria_tarifaria_nombre,
            )

            anomalias = [
                AnomaliaParaGuardar(
                    tipo=hit.tipo, severidad=hit.severidad, descripcion=hit.descripcion
                )
                for hit in hits_por_suministro.get(suministro_id, ())
            ]
            if debe_generar_anomalia_ml(
                prediccion.clasificacion,
                tiene_hits_reglas=suministro_id in suministros_con_hits,
            ):
                anomalias.append(
                    AnomaliaParaGuardar(
                        tipo="Patrón Irregular",
                        severidad="Crítica",
                        descripcion=(
                            f"Score de anomalía del modelo {prediccion.scope}: "
                            f"{prediccion.ml_score_0_100:.0f}/100 "
                            "(aproximación por atribución de features)"
                        ),
                    )
                )

            observaciones = json.dumps(
                [
                    {
                        "factor": factor.factor,
                        "contribution": round(factor.contribution, 4),
                        "reason": factor.reason,
                    }
                    for factor in composicion.factores
                ],
                ensure_ascii=False,
            )

            entradas_persistencia.append(
                ResultadoIAParaGuardar(
                    suministro_id=suministro_id,
                    lote_id=lote_id,
                    modelo_ia_id=prediccion.modelo_ia_id,
                    prediccion_id=prediccion.id,
                    score_anomalia=prediccion.score_crudo,
                    probabilidad=prediccion.score_normalizado,
                    clasificacion=composicion.clasificacion,
                    observaciones=observaciones,
                    ire_valor=composicion.ire_valor,
                    iee_kwh=iees_por_suministro[suministro_id],
                    anomalias=tuple(anomalias),
                )
            )
            entradas_informe.append(
                ResultadoSuministroParaInforme(
                    suministro_id=suministro_id,
                    numero_suministro=metadata.numero_suministro,
                    composicion=composicion,
                    iee_kwh=iees_por_suministro[suministro_id],
                    anomalia_tipos=tuple(anomalia.tipo for anomalia in anomalias),
                )
            )

        await self._resultados_ia_repository.persistir(lote_id, entradas_persistencia)

        return (
            construir_informe_ire(lote_id, entradas_informe),
            construir_informe_iee(lote_id, entradas_informe),
        )
