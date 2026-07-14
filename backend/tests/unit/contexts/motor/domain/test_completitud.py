"""Unit tests for `motor.domain.completitud` -- Etapa 1's completeness gate (STEP 0 correction,
AI_ENGINE_SPEC.md §2.1; DEC-004, §4.2)."""

from decimal import Decimal

from energia.contexts.motor.domain.completitud import (
    UMBRAL_COMPLETITUD_MINIMA,
    evaluar_completitud,
)


def test_umbral_completitud_minima_matches_dec_004() -> None:
    assert Decimal("0.95") == UMBRAL_COMPLETITUD_MINIMA


def test_cantidad_registros_zero_is_never_ready() -> None:
    resultado = evaluar_completitud(cantidad_registros=0, consumos_activos=0)

    assert resultado.completo is False
    assert resultado.cantidad_registros == 0
    assert resultado.consumos_activos == 0
    assert resultado.motivo is not None
    assert "0" in resultado.motivo


def test_matching_counts_is_complete() -> None:
    resultado = evaluar_completitud(cantidad_registros=3, consumos_activos=3)

    assert resultado.completo is True
    assert resultado.motivo is None


def test_fewer_active_consumos_than_declared_is_not_ready() -> None:
    resultado = evaluar_completitud(cantidad_registros=5, consumos_activos=2)

    assert resultado.completo is False
    assert resultado.cantidad_registros == 5
    assert resultado.consumos_activos == 2
    assert resultado.motivo is not None
    assert "5" in resultado.motivo
    assert "2" in resultado.motivo


def test_more_active_consumos_than_declared_is_also_not_ready() -> None:
    """A mismatch in either direction is "not ready" -- not just a shortfall."""
    resultado = evaluar_completitud(cantidad_registros=3, consumos_activos=4)

    assert resultado.completo is False
    assert resultado.motivo is not None
