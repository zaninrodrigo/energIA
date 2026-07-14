"""Etapa 1 — Validación de integridad checks (US-006, AI_ENGINE_SPEC.md §4).

Each `check_v*` function is pure: it takes the lote's already-fetched chain
(`ConsumoValidacionRow`s, `domain/ports.py`) and returns the `Hallazgo`s it finds, with no I/O of
its own -- the set-based SQL that turns raw table rows into that chain (including the window
function that computes each row's suministro-level `prev_fecha_inicio`/`prev_fecha_fin`) lives in
`infrastructure/validacion_data_source.py`, not here.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from energia.contexts.motor.domain.ports import ConsumoValidacionRow

# V2 (RD-013): tolerance for `kwh` vs `lectura_actual - lectura_anterior`. An implementation-level
# constant (not one of AI_ENGINE_SPEC.md's DEC-0xx decisions): a fixed absolute tolerance in kWh,
# accounting for rounding/measurement noise between the two independently-recorded figures.
# Pending calibration against real data (PROJECT_MASTER_SPEC.md #8, staging pending -- AI_ENGINE_
# SPEC.md §12 is the performance budget, not calibration; it does not apply here) -- like the
# DEC-009 rule thresholds, this number is a placeholder candidate, not a validated one.
TOLERANCIA_KWH_LECTURA = Decimal("0.500")

# V4 (RD-017): a period is contiguous with the immediately preceding one when it starts the day
# right after the previous one ends -- zero slack days in between. No calibration data exists yet
# (same caveat as `TOLERANCIA_KWH_LECTURA` above).
_UN_DIA = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class Hallazgo:
    """One finding from a single Etapa 1 check against one consumo/suministro."""

    check: str
    suministro_id: UUID
    consumo_id: UUID
    motivo: str


def check_v1_consumo_sin_lectura(filas: list[ConsumoValidacionRow]) -> list[Hallazgo]:
    """V1 (RD-018): `consumos.lectura_id IS NULL`."""
    return [
        Hallazgo(
            check="V1",
            suministro_id=fila.suministro_id,
            consumo_id=fila.consumo_id,
            motivo="consumo sin lectura asociada (lectura_id es NULL)",
        )
        for fila in filas
        if fila.lectura_id is None
    ]


def check_v2_kwh_vs_delta_lectura(filas: list[ConsumoValidacionRow]) -> list[Hallazgo]:
    """V2 (RD-013): `kwh` vs `lectura_actual - lectura_anterior`, dentro de
    `TOLERANCIA_KWH_LECTURA`. Solo evalúa filas con lectura resuelta (ver
    `ConsumoValidacionRow`'s docstring) -- sin lectura, V1 ya la marca por su cuenta.
    """
    hallazgos = []
    for fila in filas:
        if fila.lectura_anterior is None or fila.lectura_actual is None:
            continue
        delta = fila.lectura_actual - fila.lectura_anterior
        diferencia = abs(fila.kwh - delta)
        if diferencia > TOLERANCIA_KWH_LECTURA:
            hallazgos.append(
                Hallazgo(
                    check="V2",
                    suministro_id=fila.suministro_id,
                    consumo_id=fila.consumo_id,
                    motivo=(
                        f"kwh ({fila.kwh}) difiere del delta de lectura ({delta}) en "
                        f"{diferencia}, supera la tolerancia ({TOLERANCIA_KWH_LECTURA})"
                    ),
                )
            )
    return hallazgos


def check_v3_dias_facturados_mismatch(filas: list[ConsumoValidacionRow]) -> list[Hallazgo]:
    """V3 (RD-014): `consumos.dias_facturados` vs `lecturas.dias_facturados`, solo cuando ambos
    existen (ambos ya validados > 0 por sus respectivos `create()` en `consumos`)."""
    return [
        Hallazgo(
            check="V3",
            suministro_id=fila.suministro_id,
            consumo_id=fila.consumo_id,
            motivo=(
                f"dias_facturados del consumo ({fila.dias_facturados}) no coincide con el de "
                f"la lectura ({fila.lectura_dias_facturados})"
            ),
        )
        for fila in filas
        if fila.lectura_dias_facturados is not None
        and fila.dias_facturados != fila.lectura_dias_facturados
    ]


def check_v4_continuidad_periodos(filas: list[ConsumoValidacionRow]) -> list[Hallazgo]:
    """V4 (RD-017): hueco entre el período de esta fila y el período inmediatamente anterior del
    mismo suministro (`prev_fecha_fin`, calculado en SQL sobre TODO el historial activo del
    suministro -- ver `ConsumoValidacionRow`'s docstring, no solo este lote)."""
    hallazgos = []
    for fila in filas:
        if fila.prev_fecha_fin is None:
            continue
        if fila.fecha_inicio > fila.prev_fecha_fin + _UN_DIA:
            hallazgos.append(
                Hallazgo(
                    check="V4",
                    suministro_id=fila.suministro_id,
                    consumo_id=fila.consumo_id,
                    motivo=(
                        f"hueco entre el período anterior (fin {fila.prev_fecha_fin}) y este "
                        f"(inicio {fila.fecha_inicio})"
                    ),
                )
            )
    return hallazgos


def check_v5_solapamiento_parcial(filas: list[ConsumoValidacionRow]) -> list[Hallazgo]:
    """V5 (RD-017): el período de esta fila empieza en o antes de que termine el período
    inmediatamente anterior del mismo suministro -- el índice único parcial ya impide el
    duplicado EXACTO (`uq_consumos_suministro_periodo`), así que cualquier solapamiento detectado
    acá es necesariamente parcial."""
    hallazgos = []
    for fila in filas:
        if fila.prev_fecha_fin is None:
            continue
        if fila.fecha_inicio <= fila.prev_fecha_fin:
            hallazgos.append(
                Hallazgo(
                    check="V5",
                    suministro_id=fila.suministro_id,
                    consumo_id=fila.consumo_id,
                    motivo=(
                        f"período (inicio {fila.fecha_inicio}) solapa con el período anterior "
                        f"(fin {fila.prev_fecha_fin})"
                    ),
                )
            )
    return hallazgos


def check_v6_categoria_invalida(filas: list[ConsumoValidacionRow]) -> list[Hallazgo]:
    """V6 (RD-009): la categoría tarifaria del suministro está soft-deleted.
    `suministros.categoria_tarifaria_id` es `NOT NULL` (FK), así que una categoría "faltante" en
    sentido literal no es alcanzable -- la única forma real de "sin categoría válida" es una
    categoría soft-deleted por debajo de esa FK (que no verifica `deleted_at`)."""
    return [
        Hallazgo(
            check="V6",
            suministro_id=fila.suministro_id,
            consumo_id=fila.consumo_id,
            motivo="la categoría tarifaria del suministro está eliminada (soft-delete)",
        )
        for fila in filas
        if fila.categoria_deleted_at is not None
    ]


def check_v7_valores_negativos(filas: list[ConsumoValidacionRow]) -> list[Hallazgo]:
    """V7 (RD-016, RD-014): defensa en profundidad -- `ck_consumos_kwh_no_negativo`/
    `ck_consumos_dias_facturados_positivo` ya lo impiden a nivel de esquema, así que en la
    práctica esta lista siempre da vacía; se conserva por si esas CHECK alguna vez se relajan o
    algún camino de escritura las bypassea."""
    hallazgos = []
    for fila in filas:
        if fila.kwh < 0:
            hallazgos.append(
                Hallazgo(
                    check="V7",
                    suministro_id=fila.suministro_id,
                    consumo_id=fila.consumo_id,
                    motivo=f"kwh negativo: {fila.kwh}",
                )
            )
        if fila.dias_facturados <= 0:
            hallazgos.append(
                Hallazgo(
                    check="V7",
                    suministro_id=fila.suministro_id,
                    consumo_id=fila.consumo_id,
                    motivo=f"dias_facturados no positivo: {fila.dias_facturados}",
                )
            )
    return hallazgos


CHECKS = (
    check_v1_consumo_sin_lectura,
    check_v2_kwh_vs_delta_lectura,
    check_v3_dias_facturados_mismatch,
    check_v4_continuidad_periodos,
    check_v5_solapamiento_parcial,
    check_v6_categoria_invalida,
    check_v7_valores_negativos,
)


def ejecutar_checks(filas: list[ConsumoValidacionRow]) -> list[Hallazgo]:
    """Run every V1-V7 check over the lote's chain, returning every finding (each check's own
    findings preserve row order; checks run in V1..V7 order)."""
    hallazgos: list[Hallazgo] = []
    for check in CHECKS:
        hallazgos.extend(check(filas))
    return hallazgos
