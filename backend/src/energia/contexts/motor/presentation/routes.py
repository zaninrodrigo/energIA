"""FastAPI router for the `motor` context: `POST /api/v1/motor/lotes/{codigo_lote}/procesar`
(US-006 + US-010 trigger, AI_ENGINE_SPEC.md §2-§4).

See docs/03-architecture/API_SPEC.md ("Contexto: Motor de Inteligencia Energética") for the full
contract.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.motor.application.procesar_lote import (
    LoteEnProgresoError,
    LoteModificadoError,
    LoteNoEncontradoError,
    LoteNoListoError,
    LoteYaProcesadoError,
    ProcesarLote,
    ResultadoProcesamiento,
)
from energia.contexts.motor.domain.lote_estado import EstadoLote
from energia.contexts.motor.domain.ports import LoteProcesamientoPort
from energia.contexts.motor.infrastructure.duplicidades_data_source import (
    SqlDuplicidadesDataSource,
)
from energia.contexts.motor.infrastructure.feature_vector_repository import (
    SqlFeatureVectorRepository,
)
from energia.contexts.motor.infrastructure.features_data_source import SqlFeaturesDataSource
from energia.contexts.motor.infrastructure.isolation_forest_scorer import (
    SklearnIsolationForestScorer,
)
from energia.contexts.motor.infrastructure.lote_procesamiento import SqlLoteProcesamientoPort
from energia.contexts.motor.infrastructure.modelos_ia_repository import SqlModelosIaRepository
from energia.contexts.motor.infrastructure.predicciones_repository import (
    SqlPrediccionesRepository,
)
from energia.contexts.motor.infrastructure.resultados_ia_repository import (
    SqlResultadosIaRepository,
)
from energia.contexts.motor.infrastructure.resultados_ranking_repository import (
    SqlResultadosRankingRepository,
)
from energia.contexts.motor.infrastructure.validacion_data_source import SqlValidacionDataSource
from energia.contexts.motor.presentation.schemas import (
    AnomaliaRankingSchema,
    ClasificacionFiltro,
    DistribucionIRESchema,
    DistribucionScoreSchema,
    DriftLoteSchema,
    ExclusionSchema,
    HallazgoSchema,
    IeeSuministroResumenSchema,
    IndicadoresResumenSchema,
    InformeDuplicidadesSchema,
    InformeIEESchema,
    InformeIRESchema,
    InformeMLSchema,
    InformeReglasSchema,
    InformeValidacionSchema,
    IreSuministroResumenSchema,
    LecturaNearDuplicateSchema,
    ModeloEntrenadoSchema,
    NivelFiltro,
    ObservacionSchema,
    PeriodoConflictivoSchema,
    PeriodosConflictivosSuministroSchema,
    PrediccionResumenSchema,
    ProcesarLoteResponseSchema,
    ReglaHitSchema,
    ReglasSuministroSchema,
    ResultadoRankingItemSchema,
    ResultadosRankingPageSchema,
    ResumenFeaturesSchema,
    ResumenRankingSchema,
    ResumenReglasSchema,
    TopFactorSchema,
)
from energia.shared.db import get_db_session

motor_router = APIRouter(prefix="/api/v1/motor", tags=["motor"])

_DEFAULT_PAGE_LIMIT = 50
_MAX_PAGE_LIMIT = 200


def _to_response(resultado: ResultadoProcesamiento) -> ProcesarLoteResponseSchema:
    informe = resultado.informe
    duplicidades = resultado.duplicidades
    return ProcesarLoteResponseSchema(
        estado_final=resultado.estado_final.value,
        informe=InformeValidacionSchema(
            lote_id=str(informe.lote_id),
            total_suministros=informe.total_suministros,
            suministros_excluidos=informe.suministros_excluidos,
            fraccion_valida=informe.fraccion_valida,
            umbral_cumplido=informe.umbral_cumplido,
            hallazgos=[
                HallazgoSchema(
                    check=hallazgo.check,
                    suministro_id=str(hallazgo.suministro_id),
                    consumo_id=str(hallazgo.consumo_id),
                    motivo=hallazgo.motivo,
                )
                for hallazgo in informe.hallazgos
            ],
            exclusiones=[
                ExclusionSchema(
                    suministro_id=str(exclusion.suministro_id),
                    motivos=list(exclusion.motivos),
                )
                for exclusion in informe.exclusiones
            ],
        ),
        duplicidades=InformeDuplicidadesSchema(
            lote_id=str(duplicidades.lote_id),
            periodos_conflictivos=[
                PeriodosConflictivosSuministroSchema(
                    suministro_id=str(entrada.suministro_id),
                    periodos=[
                        PeriodoConflictivoSchema(
                            consumo_id=str(periodo.consumo_id),
                            fecha_inicio=periodo.fecha_inicio,
                            fecha_fin=periodo.fecha_fin,
                            lote_id=str(periodo.lote_id),
                            conflicto_con_consumo_id=str(periodo.conflicto_con_consumo_id),
                            conflicto_con_fecha_inicio=periodo.conflicto_con_fecha_inicio,
                            conflicto_con_fecha_fin=periodo.conflicto_con_fecha_fin,
                            conflicto_con_lote_id=str(periodo.conflicto_con_lote_id),
                        )
                        for periodo in entrada.periodos
                    ],
                )
                for entrada in duplicidades.periodos_conflictivos
            ],
            lecturas_near_duplicate=[
                LecturaNearDuplicateSchema(
                    suministro_id=str(lectura.suministro_id),
                    lectura_id=str(lectura.lectura_id),
                    fecha_lectura=lectura.fecha_lectura,
                    lectura_actual=lectura.lectura_actual,
                    conflicto_con_lectura_id=str(lectura.conflicto_con_lectura_id),
                    conflicto_con_fecha_lectura=lectura.conflicto_con_fecha_lectura,
                )
                for lectura in duplicidades.lecturas_near_duplicate
            ],
            drift_lotes=[
                DriftLoteSchema(
                    lote_id=str(drift.lote_id),
                    codigo_lote=drift.codigo_lote,
                    cantidad_registros=drift.cantidad_registros,
                    consumos_activos=drift.consumos_activos,
                    diferencia=drift.diferencia,
                )
                for drift in duplicidades.drift_lotes
            ],
        ),
        features=ResumenFeaturesSchema(
            suministros_con_vector=resultado.features.suministros_con_vector,
            cold_starts=resultado.features.cold_starts,
            con_periodos_conflictivos=resultado.features.con_periodos_conflictivos,
            indicadores=IndicadoresResumenSchema(
                zscore_extremos=resultado.features.zscore_extremos,
                iqr_outliers=resultado.features.iqr_outliers,
                percentile_extremos=resultado.features.percentile_extremos,
            ),
        ),
        reglas=InformeReglasSchema(
            lote_id=str(resultado.reglas.lote_id),
            resumen=ResumenReglasSchema(
                suministros_evaluados=resultado.reglas.resumen.suministros_evaluados,
                suministros_con_hits=resultado.reglas.resumen.suministros_con_hits,
                hits_por_regla=resultado.reglas.resumen.hits_por_regla,
            ),
            suministros=[
                ReglasSuministroSchema(
                    suministro_id=str(entrada.suministro_id),
                    numero_suministro=entrada.numero_suministro,
                    hits=[
                        ReglaHitSchema(
                            regla=hit.regla,
                            tipo=hit.tipo,
                            severidad=hit.severidad,
                            descripcion=hit.descripcion,
                        )
                        for hit in entrada.hits
                    ],
                )
                for entrada in resultado.reglas.suministros
            ],
        ),
        ml=InformeMLSchema(
            lote_id=str(resultado.ml.lote_id),
            modelos=[
                ModeloEntrenadoSchema(
                    scope=modelo.scope,
                    modelo_ia_id=str(modelo.modelo_ia_id),
                    version=modelo.version,
                    suministros_entrenados=modelo.suministros_entrenados,
                )
                for modelo in resultado.ml.modelos
            ],
            suministros_scored=resultado.ml.suministros_scored,
            distribucion=(
                DistribucionScoreSchema(
                    minimo=resultado.ml.distribucion.minimo,
                    p50=resultado.ml.distribucion.p50,
                    p95=resultado.ml.distribucion.p95,
                    maximo=resultado.ml.distribucion.maximo,
                )
                if resultado.ml.distribucion is not None
                else None
            ),
            top_10=[
                PrediccionResumenSchema(
                    suministro_id=str(prediccion.suministro_id),
                    numero_suministro=prediccion.numero_suministro,
                    ml_score_0_100=prediccion.ml_score_0_100,
                    clasificacion=prediccion.clasificacion,
                )
                for prediccion in resultado.ml.top_10
            ],
        ),
        ire=InformeIRESchema(
            lote_id=str(resultado.ire.lote_id),
            suministros_evaluados=resultado.ire.suministros_evaluados,
            distribucion=(
                DistribucionIRESchema(
                    minimo=resultado.ire.distribucion.minimo,
                    p50=resultado.ire.distribucion.p50,
                    p95=resultado.ire.distribucion.p95,
                    maximo=resultado.ire.distribucion.maximo,
                )
                if resultado.ire.distribucion is not None
                else None
            ),
            conteo_por_nivel=resultado.ire.conteo_por_nivel,
            top_10=[
                IreSuministroResumenSchema(
                    suministro_id=str(entrada.suministro_id),
                    numero_suministro=entrada.numero_suministro,
                    ire=entrada.ire,
                    nivel=entrada.nivel,
                    clasificacion=entrada.clasificacion,
                    top_factores=[
                        TopFactorSchema(factor=factor.factor, contribution=factor.contribution)
                        for factor in entrada.top_factores
                    ],
                )
                for entrada in resultado.ire.top_10
            ],
            anomalias_persistidas_por_tipo=resultado.ire.anomalias_persistidas_por_tipo,
        ),
        iee=InformeIEESchema(
            lote_id=str(resultado.iee.lote_id),
            total_kwh_estimado=resultado.iee.total_kwh_estimado,
            suministros_con_iee=resultado.iee.suministros_con_iee,
            top_5=[
                IeeSuministroResumenSchema(
                    suministro_id=str(entrada.suministro_id),
                    numero_suministro=entrada.numero_suministro,
                    iee_kwh=entrada.iee_kwh,
                )
                for entrada in resultado.iee.top_5
            ],
        ),
    )


async def _mensaje_conflicto_transicion(lote_port: LoteProcesamientoPort, codigo_lote: str) -> str:
    """Build the 409 detail for a lost race on the optimistic `Pendiente`/`Error` ->
    `Procesando` transition (`LoteEnProgresoError`): by the time this is raised, a concurrent
    execution may have already finished the lote (`Procesado`/`Error`) rather than still be
    running it -- an always-"en procesamiento" message would misreport that outcome. Re-reads
    the lote's CURRENT estado to branch the message accordingly.
    """
    estado_actual = await lote_port.obtener_estado_actual(codigo_lote)
    estado = estado_actual.estado if estado_actual is not None else None
    if estado is EstadoLote.PROCESADO:
        return f"el lote {codigo_lote} ya fue procesado (RD-010: estado terminal)"
    if estado is EstadoLote.ERROR:
        return f"el lote {codigo_lote} finalizó en Error; puede reintentarse"
    # EstadoLote.PROCESANDO (the common case) or an unexpected re-read (None/Pendiente, e.g. a
    # concurrent soft-delete) -- the generic in-progress message is the safest default.
    return f"el lote {codigo_lote} está en procesamiento (Procesando)"


@motor_router.post("/lotes/{codigo_lote}/procesar", response_model=ProcesarLoteResponseSchema)
async def procesar_lote(
    codigo_lote: str,
    session: AsyncSession = Depends(get_db_session),
) -> ProcesarLoteResponseSchema:
    """Run Etapa 1 (US-006, integrity validation) + Etapa 2 (US-007, duplicidades) + Etapa 3
    (US-008, feature generation) + Etapa 4 (US-009, statistical indicators) + Etapa 5
    (AI_ENGINE_SPEC.md §8, business rules) + Etapa 6 (US-011/RF-006, AI_ENGINE_SPEC.md §9,
    Isolation Forest) over `codigo_lote` and decide its final `estado` (`Procesado` if >= 95% of
    its suministros pass Etapa 1, `Error` otherwise -- DEC-004; Etapas 2-6 never influence this,
    DEC-005 for Etapa 2, and the same unconditional policy extended to Etapas 3-6 -- see
    `ProcesarLote._generar_features`'s/`_ejecutar_isolation_forest`'s docstrings). `feature_vectors`
    rows are persisted for every NON-EXCLUDED suministro; the response's `features` field is a
    summary only (counts), never the full vectors. Etapa 5's rule hits are NOT persisted anywhere
    (`domain/reglas.py`'s module docstring: `anomalias.resultado_ia_id` is `NOT NULL`, requiring
    Etapa 7's `modelo_ia_id`/`clasificacion` that do not exist yet) -- the `reglas` field of this
    response is the ONLY place a hit is visible in this implementation. Etapa 6, by contrast, DOES
    persist: one `modelos_ia` row per training scope (upsert by `(nombre, version)`, §9.4) and one
    `predicciones` row per scored suministro (full lote replacement); the response's `ml` field
    summarizes that write (model versions/scopes, score distribution, top 10) -- never the full
    per-suministro list.

    A SINGLE commit, after the whole use case returns successfully: `ProcesarLote.execute()`
    itself never commits (see that module's docstring for why a single commit at the very end,
    not two smaller ones, is what keeps the Pendiente/Error -> Procesando -> Procesado/Error
    sequence atomic against a crash in the middle -- the lote is never left stuck at
    `Procesando`; an exception here leaves the session's transaction uncommitted, which
    `energia.shared.db.get_db_session` rolls back on teardown).

    Returns 200 in BOTH outcomes of the threshold check -- landing in `Error` is a legitimate,
    successful execution of the motor (the REQUEST succeeded; the LOTE's own resulting estado
    can still be `Error`), not an HTTP-level failure. Only precondition violations before the
    checks ever run (404/409/422 below) are HTTP errors.
    """
    lote_port = SqlLoteProcesamientoPort(session)
    use_case = ProcesarLote(
        lote_port=lote_port,
        validacion_source=SqlValidacionDataSource(session),
        duplicidades_source=SqlDuplicidadesDataSource(session),
        features_source=SqlFeaturesDataSource(session),
        feature_vector_repository=SqlFeatureVectorRepository(session),
        isolation_forest_scorer=SklearnIsolationForestScorer(),
        modelos_ia_repository=SqlModelosIaRepository(session),
        predicciones_repository=SqlPrediccionesRepository(session),
        resultados_ia_repository=SqlResultadosIaRepository(session),
    )
    try:
        resultado = await use_case.execute(codigo_lote)
    except LoteNoEncontradoError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"lote no encontrado: {codigo_lote}",
        ) from error
    except LoteYaProcesadoError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"el lote {codigo_lote} ya fue procesado (RD-010: estado terminal)",
        ) from error
    except LoteEnProgresoError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=await _mensaje_conflicto_transicion(lote_port, codigo_lote),
        ) from error
    except LoteModificadoError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"el lote {codigo_lote} fue modificado durante el análisis; reintente",
        ) from error
    except LoteNoListoError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "detail": "el lote no está completo",
                "cantidad_registros": error.completitud.cantidad_registros,
                "consumos_activos": error.completitud.consumos_activos,
                "motivo": error.completitud.motivo,
            },
        ) from error

    await session.commit()
    return _to_response(resultado)


@motor_router.get("/lotes/{codigo_lote}/resultados", response_model=ResultadosRankingPageSchema)
async def obtener_resultados_lote(
    codigo_lote: str,
    limit: int = Query(default=_DEFAULT_PAGE_LIMIT, ge=1, le=_MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    nivel: NivelFiltro | None = Query(default=None),
    clasificacion: ClasificacionFiltro | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> ResultadosRankingPageSchema:
    """The IRE ranking of a processed lote (Etapas 7-8, AI_ENGINE_SPEC.md §14) -- the dashboard's
    data source: prioritizes inspections by IRE descending (RN-009, `BUSINESS_ANALYSIS.md` §15:
    "las inspecciones deberán priorizarse según el IRE y el Impacto Económico Estimado").

    **404 vs. an empty 200, the distinction that matters here**: 404 only when `codigo_lote`
    itself does not resolve to a (non-soft-deleted) `lotes` row -- reusing `LoteProcesamientoPort.
    obtener_estado_actual`, the SAME lookup `POST .../procesar` uses for its own 404. A lote that
    EXISTS but has no `resultados_ia` rows yet (imported but not yet processed, still `Pendiente`;
    or processed before Etapa 7 existed) is a REAL lote with an EMPTY ranking -- 200, `items: []`,
    `total: 0`, `resumen.total_resultados: 0` -- never 404: the lote is not "not found", it is
    simply "not analyzed yet".

    `?nivel=`/`?clasificacion=` filter `items`/`total` (422 on a value outside the closed sets
    `NivelFiltro`/`ClasificacionFiltro` declare -- FastAPI/Pydantic's own Enum validation, no
    manual check needed). `resumen` is ALWAYS the whole lote's UNFILTERED summary (dashboard
    cards) -- see `ResumenRankingSchema`'s docstring.
    """
    lote_port = SqlLoteProcesamientoPort(session)
    estado_actual = await lote_port.obtener_estado_actual(codigo_lote)
    if estado_actual is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"lote no encontrado: {codigo_lote}",
        )

    repository = SqlResultadosRankingRepository(session)
    nivel_valor = nivel.value if nivel is not None else None
    clasificacion_valor = clasificacion.value if clasificacion is not None else None

    filas = await repository.listar(
        estado_actual.id,
        limit=limit,
        offset=offset,
        nivel=nivel_valor,
        clasificacion=clasificacion_valor,
    )
    total = await repository.contar(
        estado_actual.id, nivel=nivel_valor, clasificacion=clasificacion_valor
    )
    resumen = await repository.resumen(estado_actual.id)

    return ResultadosRankingPageSchema(
        items=[
            ResultadoRankingItemSchema(
                suministro_id=str(fila.suministro_id),
                numero_suministro=fila.numero_suministro,
                rutafolio=fila.rutafolio,
                latitud=fila.latitud,
                longitud=fila.longitud,
                ire_valor=fila.ire_valor,
                ire_nivel=fila.ire_nivel,
                clasificacion=fila.clasificacion,
                score_anomalia=fila.score_anomalia,
                probabilidad=fila.probabilidad,
                localidad=fila.localidad,
                categoria_tarifaria=fila.categoria_tarifaria,
                anomalias=[
                    AnomaliaRankingSchema(
                        tipo=anomalia.tipo,
                        severidad=anomalia.severidad,
                        descripcion=anomalia.descripcion,
                    )
                    for anomalia in fila.anomalias
                ],
                observaciones=[
                    ObservacionSchema(
                        factor=factor.factor,
                        contribution=factor.contribution,
                        reason=factor.reason,
                    )
                    for factor in fila.observaciones
                ],
                iee_kwh=fila.iee_kwh,
            )
            for fila in filas
        ],
        total=total,
        limit=limit,
        offset=offset,
        resumen=ResumenRankingSchema(
            total_resultados=resumen.total_resultados,
            conteo_por_nivel=resumen.conteo_por_nivel,
            conteo_por_clasificacion=resumen.conteo_por_clasificacion,
            con_anomalias=resumen.con_anomalias,
            suma_iee_kwh=resumen.suma_iee_kwh,
        ),
    )
