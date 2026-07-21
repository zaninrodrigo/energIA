"""Unit tests for the Suministro entity and its invariants (DOMAIN_MODEL.md §7.2)."""

from datetime import date
from uuid import UUID, uuid4

import pytest

from energia.contexts.suministros.domain.suministro import Suministro, SuministroValidationError

_CLIENTE_ID = uuid4()
_CATEGORIA_ID = uuid4()


def _create(**overrides: object) -> Suministro:
    kwargs: dict[str, object] = {
        "numero_suministro": "S-12345",
        "cliente_id": _CLIENTE_ID,
        "categoria_tarifaria_id": _CATEGORIA_ID,
        "fecha_alta": "2024-01-15",
    }
    kwargs.update(overrides)
    return Suministro.create(**kwargs)  # type: ignore[arg-type]


def test_create_builds_a_suministro_with_defaults() -> None:
    suministro = _create()

    assert isinstance(suministro.id, UUID)
    assert suministro.numero_suministro == "S-12345"
    assert suministro.cliente_id == _CLIENTE_ID
    assert suministro.categoria_tarifaria_id == _CATEGORIA_ID
    assert suministro.fecha_alta == date(2024, 1, 15)
    assert suministro.estado == "Activo"
    assert suministro.localidad is None
    assert suministro.barrio is None


def test_create_strips_surrounding_whitespace_from_numero_suministro() -> None:
    suministro = _create(numero_suministro="  S-12345  ")

    assert suministro.numero_suministro == "S-12345"


def test_create_accepts_a_date_object_for_fecha_alta() -> None:
    suministro = _create(fecha_alta=date(2023, 5, 1))

    assert suministro.fecha_alta == date(2023, 5, 1)


def test_create_accepts_optional_attributes() -> None:
    suministro = _create(estado="Suspendido", localidad="Formosa", barrio="Centro")

    assert suministro.estado == "Suspendido"
    assert suministro.localidad == "Formosa"
    assert suministro.barrio == "Centro"


def test_create_honors_an_explicit_id_for_reconstruction() -> None:
    explicit_id = UUID("11111111-1111-1111-1111-111111111111")

    suministro = _create(id=explicit_id)

    assert suministro.id == explicit_id


@pytest.mark.parametrize("numero_suministro", [None, "", "   "])
def test_create_rejects_missing_numero_suministro(numero_suministro: str | None) -> None:
    with pytest.raises(SuministroValidationError) as exc_info:
        _create(numero_suministro=numero_suministro)

    assert any("numero_suministro" in reason for reason in exc_info.value.errors)


def test_create_rejects_numero_suministro_over_the_schema_length_limit() -> None:
    """`suministros.numero_suministro` is varchar(30) (docker/postgres/init/01_schema.sql)."""
    with pytest.raises(SuministroValidationError) as exc_info:
        _create(numero_suministro="1" * 31)

    assert any("numero_suministro" in reason for reason in exc_info.value.errors)


def test_create_rejects_a_missing_cliente_id() -> None:
    """RD-006: "No puede existir un suministro sin cliente."."""
    with pytest.raises(SuministroValidationError) as exc_info:
        _create(cliente_id=None)

    assert any("cliente_id" in reason for reason in exc_info.value.errors)


def test_create_rejects_a_missing_categoria_tarifaria_id() -> None:
    """RD-005/RD-008: todo suministro pertenece a una única categoría tarifaria."""
    with pytest.raises(SuministroValidationError) as exc_info:
        _create(categoria_tarifaria_id=None)

    assert any("categoria_tarifaria_id" in reason for reason in exc_info.value.errors)


def test_create_rejects_a_missing_fecha_alta() -> None:
    with pytest.raises(SuministroValidationError) as exc_info:
        _create(fecha_alta=None)

    assert any("fecha_alta" in reason for reason in exc_info.value.errors)


def test_create_rejects_an_invalid_fecha_alta_format() -> None:
    with pytest.raises(SuministroValidationError) as exc_info:
        _create(fecha_alta="15/01/2024")

    assert any("fecha_alta" in reason for reason in exc_info.value.errors)


def test_create_rejects_localidad_over_the_schema_length_limit() -> None:
    """`suministros.localidad` is varchar(100) (docker/postgres/init/01_schema.sql)."""
    with pytest.raises(SuministroValidationError) as exc_info:
        _create(localidad="a" * 101)

    assert any("localidad" in reason for reason in exc_info.value.errors)


def test_create_rejects_barrio_over_the_schema_length_limit() -> None:
    """`suministros.barrio` is varchar(100) (docker/postgres/init/01_schema.sql)."""
    with pytest.raises(SuministroValidationError) as exc_info:
        _create(barrio="a" * 101)

    assert any("barrio" in reason for reason in exc_info.value.errors)


def test_create_accepts_and_strips_medidor_and_coordinates() -> None:
    suministro = _create(medidor=" 334604 ", latitud=-26.1848, longitud=-58.1953)

    assert suministro.medidor == "334604"
    assert suministro.latitud == -26.1848
    assert suministro.longitud == -58.1953


def test_create_rejects_medidor_over_the_schema_length_limit() -> None:
    """`suministros.medidor` is varchar(20) (docker/postgres/init/01_schema.sql)."""
    with pytest.raises(SuministroValidationError) as exc_info:
        _create(medidor="9" * 21)

    assert any("medidor" in reason for reason in exc_info.value.errors)


def test_create_rejects_coordinates_out_of_range() -> None:
    with pytest.raises(SuministroValidationError) as exc_info:
        _create(latitud=-100.0, longitud=200.0)

    reasons = exc_info.value.errors
    assert any("latitud" in reason for reason in reasons)
    assert any("longitud" in reason for reason in reasons)


def test_create_rejects_estado_over_the_schema_length_limit() -> None:
    """`suministros.estado` is varchar(15) (docker/postgres/init/01_schema.sql)."""
    with pytest.raises(SuministroValidationError) as exc_info:
        _create(estado="a" * 16)

    assert any("estado" in reason for reason in exc_info.value.errors)


def test_create_accepts_estado_whose_stripped_length_is_within_the_limit() -> None:
    """The length check must apply to the *stripped* value, mirroring numero_suministro's
    handling: a raw value longer than 15 chars only due to surrounding whitespace is not a
    schema violation once stripped."""
    suministro = _create(estado=" " + "A" * 15)

    assert suministro.estado == "A" * 15


@pytest.mark.parametrize("estado", ["", "   "])
def test_create_rejects_an_explicit_empty_estado_instead_of_defaulting(estado: str) -> None:
    """An omitted `estado` (`None`) defaults to Activo, but an explicit blank string is a
    distinct, invalid input -- rejected, not silently defaulted (mirrors Cliente.estado)."""
    with pytest.raises(SuministroValidationError) as exc_info:
        _create(estado=estado)

    assert any("estado" in reason for reason in exc_info.value.errors)


def test_create_defaults_a_missing_estado_to_activo() -> None:
    suministro = _create(estado=None)

    assert suministro.estado == "Activo"


def test_create_accumulates_every_violated_invariant_in_one_error() -> None:
    with pytest.raises(SuministroValidationError) as exc_info:
        _create(numero_suministro="", cliente_id=None, categoria_tarifaria_id=None, fecha_alta=None)

    reasons = exc_info.value.errors
    assert len(reasons) == 4
    assert any("numero_suministro" in reason for reason in reasons)
    assert any("cliente_id" in reason for reason in reasons)
    assert any("categoria_tarifaria_id" in reason for reason in reasons)
    assert any("fecha_alta" in reason for reason in reasons)
