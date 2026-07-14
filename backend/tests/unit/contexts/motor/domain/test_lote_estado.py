"""Unit tests for `motor.domain.lote_estado` -- the motor's own mirror of `consumos`' Lote state
machine (RD-010, STEP 0 correction of AI_ENGINE_SPEC.md §2.2)."""

import pytest

from energia.contexts.motor.domain.lote_estado import (
    ALLOWED_TRANSITIONS,
    EstadoLote,
    TransicionNoPermitidaError,
    validar_transicion,
)


@pytest.mark.parametrize(
    ("actual", "nuevo"),
    [
        (EstadoLote.PENDIENTE, EstadoLote.PROCESANDO),
        (EstadoLote.ERROR, EstadoLote.PROCESANDO),
        (EstadoLote.PROCESANDO, EstadoLote.PROCESADO),
        (EstadoLote.PROCESANDO, EstadoLote.ERROR),
    ],
)
def test_allowed_transitions_do_not_raise(actual: EstadoLote, nuevo: EstadoLote) -> None:
    validar_transicion(actual, nuevo)


@pytest.mark.parametrize(
    ("actual", "nuevo"),
    [
        (EstadoLote.PROCESADO, EstadoLote.PROCESANDO),  # RD-010: Procesado is terminal
        (EstadoLote.PENDIENTE, EstadoLote.PROCESADO),  # cannot skip Procesando
        (EstadoLote.PENDIENTE, EstadoLote.ERROR),  # cannot skip Procesando
        (EstadoLote.ERROR, EstadoLote.PROCESADO),  # cannot skip Procesando
        (EstadoLote.PENDIENTE, EstadoLote.PENDIENTE),  # no self-transition
    ],
)
def test_disallowed_transitions_raise(actual: EstadoLote, nuevo: EstadoLote) -> None:
    with pytest.raises(TransicionNoPermitidaError):
        validar_transicion(actual, nuevo)


def test_procesado_is_terminal() -> None:
    assert ALLOWED_TRANSITIONS[EstadoLote.PROCESADO] == frozenset()


def test_estado_lote_matches_schema_check_constraint_values() -> None:
    """Mirrors `ck_lotes_estado` (docker/postgres/init/01_schema.sql) exactly."""
    assert {estado.value for estado in EstadoLote} == {
        "Pendiente",
        "Procesando",
        "Procesado",
        "Error",
    }


def test_mirrors_consumos_estado_lote_exactly() -> None:
    """FIX 4 (drift guard): `motor`'s own `EstadoLote`/`ALLOWED_TRANSITIONS` (this module's
    docstring explains WHY they are a deliberate, small duplication of `consumos`' state
    machine, not an import of it) must stay byte-for-byte in sync with what they mirror.

    Tests are explicitly allowed to import across bounded contexts to assert this -- PRODUCTION
    code is not (see `contexts/README.md`'s cross-context directory-port pattern, and this
    module's own docstring): a test comparing two independent domain modules is not a runtime
    dependency between them, it is a regression guard that fails loudly the moment either one
    drifts from the other, exactly the failure mode the "mirror, don't import" design accepts as
    a tradeoff.
    """
    from energia.contexts.consumos.domain.lote import (
        ALLOWED_TRANSITIONS as CONSUMOS_ALLOWED_TRANSITIONS,
    )
    from energia.contexts.consumos.domain.lote import (
        EstadoLote as ConsumosEstadoLote,
    )

    assert {estado.value for estado in EstadoLote} == {
        estado.value for estado in ConsumosEstadoLote
    }

    motor_transitions = {
        estado.value: {destino.value for destino in destinos}
        for estado, destinos in ALLOWED_TRANSITIONS.items()
    }
    consumos_transitions = {
        estado.value: {destino.value for destino in destinos}
        for estado, destinos in CONSUMOS_ALLOWED_TRANSITIONS.items()
    }
    assert motor_transitions == consumos_transitions
