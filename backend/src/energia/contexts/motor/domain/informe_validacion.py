"""InformeValidacion: Etapa 1's output contract (AI_ENGINE_SPEC.md §4.2).

DEC-003 (exclude + annotate, not abort) and DEC-004 (95% completeness threshold) both apply at
the SUMINISTRO level, not the individual check-finding level: a suministro with ANY V1-V7 finding
against ANY of its consumo rows in this lote is excluded from scoring and every one of its
reasons recorded, but the lote itself is only marked `Error` when the fraction of EXCLUDED
suministros crosses DEC-004's threshold (`domain/completitud.py`'s `UMBRAL_COMPLETITUD_MINIMA` --
reused here for the SAME 95% number, since AI_ENGINE_SPEC.md §4.2/DEC-004 is one decision applied
to Etapa 1's outcome, not two separate thresholds that happen to share a value).
"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from energia.contexts.motor.domain.checks import Hallazgo, ejecutar_checks
from energia.contexts.motor.domain.completitud import UMBRAL_COMPLETITUD_MINIMA
from energia.contexts.motor.domain.ports import ConsumoValidacionRow


@dataclass(frozen=True, slots=True)
class ExclusionSuministro:
    """One suministro excluded from Etapa 1 scoring, with every reason it was excluded for
    (DEC-003: "excluir + anotar el motivo")."""

    suministro_id: UUID
    motivos: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InformeValidacion:
    """Etapa 1's full validation report for one lote (AI_ENGINE_SPEC.md §4.2)."""

    lote_id: UUID
    total_suministros: int
    hallazgos: tuple[Hallazgo, ...]
    exclusiones: tuple[ExclusionSuministro, ...]
    fraccion_valida: Decimal
    umbral_cumplido: bool

    @property
    def suministros_excluidos(self) -> int:
        return len(self.exclusiones)


def construir_informe(lote_id: UUID, filas: list[ConsumoValidacionRow]) -> InformeValidacion:
    """Run every V1-V7 check over `filas` and assemble the full `InformeValidacion` -- the single
    entry point `ProcesarLote` (application layer) calls once the lote's chain has been fetched.
    """
    hallazgos = ejecutar_checks(filas)

    motivos_por_suministro: dict[UUID, list[str]] = defaultdict(list)
    for hallazgo in hallazgos:
        motivos_por_suministro[hallazgo.suministro_id].append(
            f"{hallazgo.check}: {hallazgo.motivo}"
        )

    total_suministros = len({fila.suministro_id for fila in filas})
    exclusiones = tuple(
        ExclusionSuministro(suministro_id=suministro_id, motivos=tuple(motivos))
        for suministro_id, motivos in motivos_por_suministro.items()
    )

    if total_suministros == 0:
        # Only reachable if `filas` is empty -- the completeness gate (`domain/completitud.py`)
        # already guards against this before `construir_informe` is ever called (it requires
        # `cantidad_registros > 0` and a matching active-consumo count), but a 0/0 fraction is
        # undefined, so it is handled explicitly rather than raising `ZeroDivisionError`.
        fraccion_valida = Decimal("1")
    else:
        validos = total_suministros - len(exclusiones)
        fraccion_valida = Decimal(validos) / Decimal(total_suministros)

    return InformeValidacion(
        lote_id=lote_id,
        total_suministros=total_suministros,
        hallazgos=tuple(hallazgos),
        exclusiones=exclusiones,
        fraccion_valida=fraccion_valida,
        umbral_cumplido=fraccion_valida >= UMBRAL_COMPLETITUD_MINIMA,
    )
