"""Etapa 1 completeness gate (STEP 0 correction, AI_ENGINE_SPEC.md §2.1; DEC-004, §4.2).

A lote is ready for the motor to run its Etapa 1 checks only once its data load is COMPLETE --
this module implements the corrected trigger definition: `cantidad_registros > 0` AND the count
of active (non-soft-deleted) `consumos` rows for that `lote_id` equals `cantidad_registros`
exactly. `cantidad_registros == 0` means "not ready" (the lote has not declared how many records
it expects, or was imported with the default), not "trivially complete with zero records" -- an
empty lote is never a valid input to Etapa 1's checks.
"""

from dataclasses import dataclass
from decimal import Decimal

# DEC-004 (AI_ENGINE_SPEC.md §4.2, accepted 2026-07-14): at least 95% of a lote's suministros must
# pass Etapa 1 validation for the motor to mark it `Procesado`; below this, `Error`. Reused by
# `domain/informe_validacion.py` for the SAME 95% number -- DEC-004 is one decision applied to
# both the completeness gate and Etapa 1's outcome, not two independent thresholds that happen to
# share a value.
UMBRAL_COMPLETITUD_MINIMA = Decimal("0.95")


@dataclass(frozen=True, slots=True)
class ResultadoCompletitud:
    """Outcome of the completeness gate for one lote -- both counts are always reported (even
    when `completo` is `True`), so a 422 response can show the caller exactly what was compared.
    """

    completo: bool
    cantidad_registros: int
    consumos_activos: int
    motivo: str | None


def evaluar_completitud(*, cantidad_registros: int, consumos_activos: int) -> ResultadoCompletitud:
    """Evaluate AI_ENGINE_SPEC.md §2.1's completeness rule for one lote.

    `cantidad_registros == 0` is reported with its own, more specific `motivo` (distinct from a
    generic mismatch) since it is never ready regardless of `consumos_activos` -- a lote that has
    not (yet) declared how many records it expects has not finished being described, it is not
    "trivially complete with zero records".
    """
    if cantidad_registros == 0:
        return ResultadoCompletitud(
            completo=False,
            cantidad_registros=cantidad_registros,
            consumos_activos=consumos_activos,
            motivo="cantidad_registros es 0: el lote no declara registros esperados",
        )

    if consumos_activos != cantidad_registros:
        return ResultadoCompletitud(
            completo=False,
            cantidad_registros=cantidad_registros,
            consumos_activos=consumos_activos,
            motivo=(
                f"cantidad de consumos activos ({consumos_activos}) no coincide con "
                f"cantidad_registros declarada ({cantidad_registros})"
            ),
        )

    return ResultadoCompletitud(
        completo=True,
        cantidad_registros=cantidad_registros,
        consumos_activos=consumos_activos,
        motivo=None,
    )
