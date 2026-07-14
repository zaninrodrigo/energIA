"""FastAPI router for the `motor` context: `POST /api/v1/motor/lotes/{codigo_lote}/procesar`
(US-006 + US-010 trigger, AI_ENGINE_SPEC.md §2-§4).

See docs/03-architecture/API_SPEC.md ("Contexto: Motor de Inteligencia Energética") for the full
contract.
"""

from fastapi import APIRouter, Depends, HTTPException, status
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
from energia.contexts.motor.infrastructure.lote_procesamiento import SqlLoteProcesamientoPort
from energia.contexts.motor.infrastructure.validacion_data_source import SqlValidacionDataSource
from energia.contexts.motor.presentation.schemas import (
    ExclusionSchema,
    HallazgoSchema,
    InformeValidacionSchema,
    ProcesarLoteResponseSchema,
)
from energia.shared.db import get_db_session

motor_router = APIRouter(prefix="/api/v1/motor", tags=["motor"])


def _to_response(resultado: ResultadoProcesamiento) -> ProcesarLoteResponseSchema:
    informe = resultado.informe
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
    """Run Etapa 1 (US-006, integrity validation) over `codigo_lote` and decide its final
    `estado` (`Procesado` if >= 95% of its suministros pass, `Error` otherwise -- DEC-004).

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
