"""Unit tests for the Lote entity and its invariants (DOMAIN_MODEL.md §7.4).

Mirrors `tests/unit/contexts/suministros/domain/test_suministro.py`'s structure. Two things are
specific to `Lote` and covered here: `estado` is never accepted as a `create()` input (RD-010's
intent -- an imported lote is always born `Pendiente`), and the `EstadoLote` state machine
(RD-010) -- `Lote.transition_to()` and the `ALLOWED_TRANSITIONS` map.
"""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from energia.contexts.consumos.domain.lote import (
    ALLOWED_TRANSITIONS,
    EstadoLote,
    Lote,
    LoteEstadoTransitionError,
    LoteValidationError,
)


def _create(**overrides: object) -> Lote:
    kwargs: dict[str, object] = {"codigo_lote": "LOTE-2024-01"}
    kwargs.update(overrides)
    return Lote.create(**kwargs)  # type: ignore[arg-type]


def test_create_builds_a_lote_with_the_given_values() -> None:
    lote = _create(nombre="Enero 2024", cantidad_registros=150)

    assert isinstance(lote.id, UUID)
    assert lote.codigo_lote == "LOTE-2024-01"
    assert lote.nombre == "Enero 2024"
    assert lote.cantidad_registros == 150
    assert isinstance(lote.fecha_importacion, datetime)


def test_create_defaults_cantidad_registros_to_zero_when_omitted() -> None:
    lote = _create()

    assert lote.cantidad_registros == 0


def test_create_defaults_nombre_to_none_when_omitted() -> None:
    lote = _create()

    assert lote.nombre is None


def test_create_always_returns_a_lote_born_pendiente() -> None:
    """RD-010's intent: `create()` has no `estado` parameter at all -- there is no way for
    external input (the import payload) to fabricate a `Procesado`/`Procesando`/`Error` lote."""
    lote = _create()

    assert lote.estado is EstadoLote.PENDIENTE


def test_create_generates_a_fresh_fecha_importacion_when_omitted() -> None:
    before = datetime.now(UTC)

    lote = _create()

    after = datetime.now(UTC)
    assert before <= lote.fecha_importacion <= after


def test_create_honors_an_explicit_fecha_importacion_for_reconstruction() -> None:
    explicit = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

    lote = _create(fecha_importacion=explicit)

    assert lote.fecha_importacion == explicit


def test_create_honors_an_explicit_id_for_reconstruction() -> None:
    explicit_id = UUID("11111111-1111-1111-1111-111111111111")

    lote = _create(id=explicit_id)

    assert lote.id == explicit_id


def test_create_rejects_a_missing_codigo_lote() -> None:
    with pytest.raises(LoteValidationError) as exc_info:
        _create(codigo_lote=None)

    assert any("codigo_lote" in reason for reason in exc_info.value.errors)


def test_create_rejects_a_blank_codigo_lote() -> None:
    with pytest.raises(LoteValidationError) as exc_info:
        _create(codigo_lote="   ")

    assert any("codigo_lote" in reason for reason in exc_info.value.errors)


def test_create_rejects_a_codigo_lote_over_the_schema_length_limit() -> None:
    """`lotes.codigo_lote` is varchar(50)."""
    with pytest.raises(LoteValidationError) as exc_info:
        _create(codigo_lote="X" * 51)

    assert any("codigo_lote" in reason for reason in exc_info.value.errors)


def test_create_accepts_a_codigo_lote_at_the_schema_length_limit() -> None:
    lote = _create(codigo_lote="X" * 50)

    assert lote.codigo_lote == "X" * 50


def test_create_strips_surrounding_whitespace_from_codigo_lote() -> None:
    lote = _create(codigo_lote="  LOTE-2024-02  ")

    assert lote.codigo_lote == "LOTE-2024-02"


def test_create_rejects_a_nombre_over_the_schema_length_limit() -> None:
    """`lotes.nombre` is varchar(150)."""
    with pytest.raises(LoteValidationError) as exc_info:
        _create(nombre="X" * 151)

    assert any("nombre" in reason for reason in exc_info.value.errors)


def test_create_accepts_a_nombre_at_the_schema_length_limit() -> None:
    lote = _create(nombre="X" * 150)

    assert lote.nombre == "X" * 150


def test_create_accepts_numeric_string_for_cantidad_registros() -> None:
    lote = _create(cantidad_registros="42")

    assert lote.cantidad_registros == 42


def test_create_rejects_a_non_integer_cantidad_registros() -> None:
    with pytest.raises(LoteValidationError) as exc_info:
        _create(cantidad_registros="not-a-number")

    assert any("cantidad_registros" in reason for reason in exc_info.value.errors)


def test_create_rejects_a_negative_cantidad_registros() -> None:
    """`ck_lotes_cantidad_registros_no_negativa` (docker/postgres/init/01_schema.sql)."""
    with pytest.raises(LoteValidationError) as exc_info:
        _create(cantidad_registros=-1)

    assert any("cantidad_registros" in reason for reason in exc_info.value.errors)


def test_create_accepts_a_zero_cantidad_registros() -> None:
    lote = _create(cantidad_registros=0)

    assert lote.cantidad_registros == 0


def test_create_rejects_a_boolean_cantidad_registros() -> None:
    """`bool` is a subclass of `int` in Python -- `True`/`False` must not silently pass as 1/0."""
    with pytest.raises(LoteValidationError) as exc_info:
        _create(cantidad_registros=True)

    assert any("cantidad_registros" in reason for reason in exc_info.value.errors)


def test_create_rejects_a_cantidad_registros_of_an_unsupported_type() -> None:
    with pytest.raises(LoteValidationError) as exc_info:
        _create(cantidad_registros=42.5)

    assert any("cantidad_registros" in reason for reason in exc_info.value.errors)


def test_create_accepts_a_cantidad_registros_at_the_postgres_integer_max() -> None:
    """`lotes.cantidad_registros` is a plain Postgres `integer` (docker/postgres/init/
    01_schema.sql) -- 2_147_483_647 is the largest value that column can hold."""
    lote = _create(cantidad_registros=2_147_483_647)

    assert lote.cantidad_registros == 2_147_483_647


def test_create_rejects_a_cantidad_registros_one_past_the_postgres_integer_max() -> None:
    with pytest.raises(LoteValidationError) as exc_info:
        _create(cantidad_registros=2_147_483_648)

    assert any("cantidad_registros" in reason for reason in exc_info.value.errors)


def test_create_rejects_the_reproduced_out_of_range_cantidad_registros() -> None:
    """Reviewer-reproduced case: `99999999999` passes Pydantic (an unbounded Python int) and
    every check `Lote.create()` had before this fix, then dies at the database as an opaque
    `DBAPIError` (not `IntegrityError`) instead of a per-record rejection -- a 500 for the whole
    batch. Must be rejected here, before ever reaching the repository."""
    with pytest.raises(LoteValidationError) as exc_info:
        _create(cantidad_registros=99999999999)

    assert any("cantidad_registros" in reason for reason in exc_info.value.errors)


def test_create_accumulates_every_violated_invariant_in_one_error() -> None:
    with pytest.raises(LoteValidationError) as exc_info:
        _create(codigo_lote=None, nombre="X" * 151, cantidad_registros=-1)

    reasons = exc_info.value.errors
    assert any("codigo_lote" in reason for reason in reasons)
    assert any("nombre" in reason for reason in reasons)
    assert any("cantidad_registros" in reason for reason in reasons)


# -- EstadoLote transition rules (RD-010: "un lote no puede ejecutarse dos veces") -----------


def test_allowed_transitions_covers_every_estado() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(EstadoLote)


@pytest.mark.parametrize(
    ("origen", "destino"),
    [
        (EstadoLote.PENDIENTE, EstadoLote.PROCESANDO),
        (EstadoLote.PROCESANDO, EstadoLote.PROCESADO),
        (EstadoLote.PROCESANDO, EstadoLote.ERROR),
        (EstadoLote.ERROR, EstadoLote.PROCESANDO),
    ],
)
def test_transition_to_allows_the_documented_transitions(
    origen: EstadoLote, destino: EstadoLote
) -> None:
    lote = replace(_create(), estado=origen)

    transitioned = lote.transition_to(destino)

    assert transitioned.estado is destino
    # Every other field is preserved -- only `estado` changes.
    assert transitioned.id == lote.id
    assert transitioned.codigo_lote == lote.codigo_lote


@pytest.mark.parametrize(
    ("origen", "destino"),
    [
        (EstadoLote.PENDIENTE, EstadoLote.PROCESADO),
        (EstadoLote.PENDIENTE, EstadoLote.ERROR),
        (EstadoLote.PENDIENTE, EstadoLote.PENDIENTE),
        (EstadoLote.PROCESANDO, EstadoLote.PENDIENTE),
        (EstadoLote.PROCESANDO, EstadoLote.PROCESANDO),
        (EstadoLote.PROCESADO, EstadoLote.PENDIENTE),
        (EstadoLote.PROCESADO, EstadoLote.PROCESANDO),
        (EstadoLote.PROCESADO, EstadoLote.PROCESADO),
        (EstadoLote.PROCESADO, EstadoLote.ERROR),
        (EstadoLote.ERROR, EstadoLote.PENDIENTE),
        (EstadoLote.ERROR, EstadoLote.PROCESADO),
        (EstadoLote.ERROR, EstadoLote.ERROR),
    ],
)
def test_transition_to_rejects_every_undocumented_transition(
    origen: EstadoLote, destino: EstadoLote
) -> None:
    """RD-010: "un lote no puede ejecutarse dos veces" -- `Procesado` is terminal (a lote that
    already succeeded may never be re-run), and `Pendiente`/`Procesando` may only move forward,
    never sideways or backward. `Error` is no longer terminal (`Error` -> `Procesando` is now
    allowed, confirmed by business 2026-07-13 -- see the test above), but it still may not jump
    directly to `Procesado` or back to `Pendiente`."""
    lote = replace(_create(), estado=origen)

    with pytest.raises(LoteEstadoTransitionError) as exc_info:
        lote.transition_to(destino)

    assert origen.value in str(exc_info.value)
    assert destino.value in str(exc_info.value)
