"""Unit tests for `motor.domain.informe_validacion` -- assembling Etapa 1's full report
(AI_ENGINE_SPEC.md §4.2, DEC-003 exclude+annotate, DEC-004 95% threshold)."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from energia.contexts.motor.domain.informe_validacion import construir_informe
from energia.contexts.motor.domain.ports import ConsumoValidacionRow


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


def _sin_lectura(fila: ConsumoValidacionRow) -> ConsumoValidacionRow:
    return replace(fila, lectura_id=None, lectura_anterior=None, lectura_actual=None)


def _excluyente(fila: ConsumoValidacionRow) -> ConsumoValidacionRow:
    """A fila that trips an EXCLUDING check (V7: dias_facturados <= 0), unlike V1 which only
    annotates. `lectura_dias_facturados` matched to 0 too so only V7 fires, not also V3."""
    return replace(fila, dias_facturados=0, lectura_dias_facturados=0)


def test_all_suministros_valid_yields_100_percent_and_threshold_met() -> None:
    lote_id = uuid4()
    filas = [_fila_valida() for _ in range(4)]  # 4 distinct suministros, no findings

    informe = construir_informe(lote_id, filas)

    assert informe.lote_id == lote_id
    assert informe.total_suministros == 4
    assert informe.suministros_excluidos == 0
    assert informe.fraccion_valida == Decimal("1")
    assert informe.umbral_cumplido is True
    assert informe.hallazgos == ()
    assert informe.exclusiones == ()


def test_one_invalid_suministro_out_of_twenty_still_meets_threshold() -> None:
    """1/20 excluded => 95% valid, exactly at DEC-004's threshold (>=95%)."""
    lote_id = uuid4()
    filas = [_fila_valida() for _ in range(19)]
    filas.append(_excluyente(_fila_valida()))

    informe = construir_informe(lote_id, filas)

    assert informe.total_suministros == 20
    assert informe.suministros_excluidos == 1
    assert informe.fraccion_valida == Decimal("19") / Decimal("20")
    assert informe.umbral_cumplido is True


def test_below_threshold_reports_umbral_incumplido() -> None:
    lote_id = uuid4()
    filas = [_fila_valida(), _excluyente(_fila_valida())]

    informe = construir_informe(lote_id, filas)

    assert informe.total_suministros == 2
    assert informe.suministros_excluidos == 1
    assert informe.fraccion_valida == Decimal("1") / Decimal("2")
    assert informe.umbral_cumplido is False


def test_v1_missing_lectura_annotates_but_does_not_exclude() -> None:
    """V1 is informative-only (CHECKS_INFORMATIVOS): a whole no-lecturas dataset (real historical
    data) must NOT be 100% excluded -- it is annotated in `hallazgos`, not excluded."""
    lote_id = uuid4()
    filas = [_sin_lectura(_fila_valida()) for _ in range(3)]

    informe = construir_informe(lote_id, filas)

    assert informe.suministros_excluidos == 0
    assert informe.fraccion_valida == Decimal("1")
    assert informe.umbral_cumplido is True
    # Still reported (annotated), just not driving exclusion.
    assert {h.check for h in informe.hallazgos} == {"V1"}
    assert informe.exclusiones == ()


def test_exclusion_aggregates_every_motivo_for_a_suministro() -> None:
    lote_id = uuid4()
    suministro_id = uuid4()
    # Both an informative finding (V1, sin lectura) and an excluding one (V7, dias 0): only the
    # excluding one appears in the exclusion motivos; V1 is annotated in `hallazgos` instead.
    fila = replace(_sin_lectura(_fila_valida(suministro_id)), dias_facturados=0)

    informe = construir_informe(lote_id, [fila])

    assert informe.suministros_excluidos == 1
    exclusion = informe.exclusiones[0]
    assert exclusion.suministro_id == suministro_id
    checks_excluyentes = {motivo.split(":")[0] for motivo in exclusion.motivos}
    assert "V7" in checks_excluyentes
    assert "V1" not in checks_excluyentes  # informative-only, never excludes
    assert "V1" in {h.check for h in informe.hallazgos}  # but still reported


def test_empty_chain_does_not_raise_zero_division() -> None:
    lote_id = uuid4()
    informe = construir_informe(lote_id, [])

    assert informe.total_suministros == 0
    assert informe.fraccion_valida == Decimal("1")
