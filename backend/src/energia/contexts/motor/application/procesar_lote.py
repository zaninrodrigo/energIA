"""ProcesarLote: Etapa 1's orchestrating use case (US-006 + US-010 trigger, AI_ENGINE_SPEC.md
§2-§4), extended with Etapa 2's duplicidades detection (US-007, §5).

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
  (h) transition `Procesando` -> `Procesado` (>= 95% valid) or `Procesando` -> `Error` (< 95%).
      `assert`ed to succeed: under the row lock held since step (c), a `False` return here would
      mean a broken invariant, not a normal race (protects a future refactor that might
      accidentally release that lock early, e.g. an intermediate commit).
  (i) return the full `InformeValidacion` + `InformeDuplicidades` plus the final `EstadoLote`.

Nothing here calls `session.commit()` -- see `presentation/routes.py`'s docstring for why a
SINGLE commit at the very end (after step (h), not after step (c) too) is what keeps this whole
sequence atomic without extra locking: Postgres's own row lock on the `UPDATE ... WHERE id =
lote_id` from step (c) blocks any concurrent `procesar` call against the SAME lote until this
transaction commits or rolls back, so a crash between (c) and (h) rolls the WHOLE sequence back
(the lote never gets stuck at `Procesando` with nothing to show for it) instead of leaving a
stuck row the way a two-commit design would. That SAME rollback is what reverts (c)'s transition
when (e) raises `LoteModificadoError`.
"""

from dataclasses import dataclass
from uuid import UUID

from energia.contexts.motor.domain.completitud import ResultadoCompletitud, evaluar_completitud
from energia.contexts.motor.domain.duplicidades import (
    InformeDuplicidades,
    construir_informe_duplicidades,
)
from energia.contexts.motor.domain.informe_validacion import InformeValidacion, construir_informe
from energia.contexts.motor.domain.lote_estado import EstadoLote
from energia.contexts.motor.domain.ports import (
    DuplicidadesDataSource,
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
    """`ProcesarLote.execute()`'s return value: the final estado plus Etapa 1's + Etapa 2's full
    reports. `duplicidades` never influences `estado_final` (DEC-005: annotate only, §5)."""

    estado_final: EstadoLote
    informe: InformeValidacion
    duplicidades: InformeDuplicidades


class ProcesarLote:
    """US-006 + US-010 + US-007: valida la integridad de un lote completo (Etapa 1), detecta
    duplicidades (Etapa 2, anotación únicamente) y decide el estado final del lote."""

    def __init__(
        self,
        *,
        lote_port: LoteProcesamientoPort,
        validacion_source: ValidacionDataSource,
        duplicidades_source: DuplicidadesDataSource,
    ) -> None:
        self._lote_port = lote_port
        self._validacion_source = validacion_source
        self._duplicidades_source = duplicidades_source

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
            estado_final=estado_final, informe=informe, duplicidades=duplicidades
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
