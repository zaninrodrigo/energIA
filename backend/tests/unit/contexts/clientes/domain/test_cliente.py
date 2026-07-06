"""Unit tests for the Cliente entity and its invariants (DOMAIN_MODEL.md §7.1)."""

from uuid import UUID

import pytest

from energia.contexts.clientes.domain.cliente import Cliente, ClienteValidationError, EstadoCliente


def test_create_builds_a_cliente_with_defaults() -> None:
    cliente = Cliente.create(numero_cliente="12345", nombre="Juana Perez")

    assert isinstance(cliente.id, UUID)
    assert cliente.numero_cliente == "12345"
    assert cliente.nombre == "Juana Perez"
    assert cliente.estado == EstadoCliente.ACTIVO
    assert cliente.documento is None
    assert cliente.localidad is None
    assert cliente.barrio is None
    assert cliente.direccion is None


def test_create_strips_surrounding_whitespace() -> None:
    cliente = Cliente.create(numero_cliente="  12345  ", nombre="  Juana Perez  ")

    assert cliente.numero_cliente == "12345"
    assert cliente.nombre == "Juana Perez"


def test_create_accepts_optional_attributes() -> None:
    cliente = Cliente.create(
        numero_cliente="12345",
        nombre="Juana Perez",
        estado="Inactivo",
        documento="20304050",
        localidad="Formosa",
        barrio="Centro",
        direccion={"calle": "San Martin", "numero": "123"},
    )

    assert cliente.estado == EstadoCliente.INACTIVO
    assert cliente.documento == "20304050"
    assert cliente.localidad == "Formosa"
    assert cliente.barrio == "Centro"
    assert cliente.direccion == {"calle": "San Martin", "numero": "123"}


def test_create_honors_an_explicit_id_for_reconstruction() -> None:
    explicit_id = UUID("11111111-1111-1111-1111-111111111111")

    cliente = Cliente.create(id=explicit_id, numero_cliente="12345", nombre="Juana Perez")

    assert cliente.id == explicit_id


@pytest.mark.parametrize("numero_cliente", [None, "", "   "])
def test_create_rejects_missing_numero_cliente(numero_cliente: str | None) -> None:
    with pytest.raises(ClienteValidationError) as exc_info:
        Cliente.create(numero_cliente=numero_cliente, nombre="Juana Perez")

    assert any("numero_cliente" in reason for reason in exc_info.value.errors)


def test_create_rejects_numero_cliente_over_the_schema_length_limit() -> None:
    """`clientes.numero_cliente` is varchar(30) (docker/postgres/init/01_schema.sql)."""
    with pytest.raises(ClienteValidationError) as exc_info:
        Cliente.create(numero_cliente="1" * 31, nombre="Juana Perez")

    assert any("numero_cliente" in reason for reason in exc_info.value.errors)


@pytest.mark.parametrize("nombre", [None, "", "   "])
def test_create_rejects_missing_nombre(nombre: str | None) -> None:
    with pytest.raises(ClienteValidationError) as exc_info:
        Cliente.create(numero_cliente="12345", nombre=nombre)

    assert any("nombre" in reason for reason in exc_info.value.errors)


def test_create_rejects_nombre_over_the_schema_length_limit() -> None:
    """`clientes.nombre` is varchar(150) (docker/postgres/init/01_schema.sql)."""
    with pytest.raises(ClienteValidationError) as exc_info:
        Cliente.create(numero_cliente="12345", nombre="a" * 151)

    assert any("nombre" in reason for reason in exc_info.value.errors)


def test_create_rejects_documento_over_the_schema_length_limit() -> None:
    """`clientes.documento` is varchar(20) (docker/postgres/init/01_schema.sql)."""
    with pytest.raises(ClienteValidationError) as exc_info:
        Cliente.create(numero_cliente="12345", nombre="Juana Perez", documento="1" * 21)

    assert any("documento" in reason for reason in exc_info.value.errors)


def test_create_rejects_localidad_over_the_schema_length_limit() -> None:
    """`clientes.localidad` is varchar(100) (docker/postgres/init/01_schema.sql)."""
    with pytest.raises(ClienteValidationError) as exc_info:
        Cliente.create(numero_cliente="12345", nombre="Juana Perez", localidad="a" * 101)

    assert any("localidad" in reason for reason in exc_info.value.errors)


def test_create_rejects_barrio_over_the_schema_length_limit() -> None:
    """`clientes.barrio` is varchar(100) (docker/postgres/init/01_schema.sql)."""
    with pytest.raises(ClienteValidationError) as exc_info:
        Cliente.create(numero_cliente="12345", nombre="Juana Perez", barrio="a" * 101)

    assert any("barrio" in reason for reason in exc_info.value.errors)


def test_create_rejects_an_invalid_estado() -> None:
    """`ck_clientes_estado` only allows 'Activo'/'Inactivo' (docker/postgres/init/01_schema.sql)."""
    with pytest.raises(ClienteValidationError) as exc_info:
        Cliente.create(numero_cliente="12345", nombre="Juana Perez", estado="Pendiente")

    assert any("estado" in reason for reason in exc_info.value.errors)


def test_create_accumulates_every_violated_invariant_in_one_error() -> None:
    """Multiple invariant violations are reported together, not just the first one found."""
    with pytest.raises(ClienteValidationError) as exc_info:
        Cliente.create(numero_cliente="", nombre="", estado="Pendiente")

    reasons = exc_info.value.errors
    assert len(reasons) == 3
    assert any("numero_cliente" in reason for reason in reasons)
    assert any("nombre" in reason for reason in reasons)
    assert any("estado" in reason for reason in reasons)


@pytest.mark.parametrize("estado", ["", "   "])
def test_create_rejects_an_explicit_empty_estado_instead_of_defaulting(estado: str) -> None:
    """An omitted `estado` (`None`) defaults to Activo, but an explicit blank string is a
    distinct, invalid input — it must be rejected, not silently defaulted, the same way an
    explicit blank `numero_cliente`/`nombre` is rejected rather than defaulted."""
    with pytest.raises(ClienteValidationError) as exc_info:
        Cliente.create(numero_cliente="12345", nombre="Juana Perez", estado=estado)

    assert any("estado" in reason for reason in exc_info.value.errors)


def test_create_defaults_a_missing_estado_to_activo() -> None:
    """`estado=None` (the field simply omitted) is not the same as an explicit blank string:
    this is the documented default, not a rejection."""
    cliente = Cliente.create(numero_cliente="12345", nombre="Juana Perez", estado=None)

    assert cliente.estado == EstadoCliente.ACTIVO


def test_create_rejects_a_direccion_over_the_8kb_serialized_size_cap() -> None:
    """`direccion` is `jsonb` with no DB-level size limit; an 8 KB cap keeps a single malformed
    or abusive record from bloating storage. Oversized `direccion` is a per-record rejection
    (`ClienteValidationError`), not a structural (422) one."""
    oversized_direccion = {"calle": "x" * 9000}

    with pytest.raises(ClienteValidationError) as exc_info:
        Cliente.create(numero_cliente="12345", nombre="Juana Perez", direccion=oversized_direccion)

    assert any("direccion" in reason for reason in exc_info.value.errors)


def test_create_accepts_a_direccion_at_or_under_the_8kb_cap() -> None:
    direccion = {"calle": "x" * 100}

    cliente = Cliente.create(numero_cliente="12345", nombre="Juana Perez", direccion=direccion)

    assert cliente.direccion == direccion
