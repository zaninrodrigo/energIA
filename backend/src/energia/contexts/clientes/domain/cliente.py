"""Cliente entity and invariants (DOMAIN_MODEL.md §7.1, docker/postgres/init/01_schema.sql).

Persistence-ignorant on purpose (ADR-001): no SQLAlchemy or any other infrastructure import
belongs in this module. `Cliente.create()` is the only way to build a `Cliente` from
untrusted input — it enforces every invariant and raises `ClienteValidationError` (with every
violation found, not just the first) instead of silently accepting bad data. Reconstructing a
`Cliente` from a trusted source (a database row already known to satisfy these invariants) can
call the dataclass constructor directly, e.g. from `infrastructure/cliente_repository.py`.
"""

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

# Schema-derived length limits (docker/postgres/init/01_schema.sql, table `clientes`). Kept in
# sync with the DB's varchar sizes so a rejected record is caught here instead of surfacing as
# an opaque database error later.
_NUMERO_CLIENTE_MAX_LENGTH = 30
_NOMBRE_MAX_LENGTH = 150
_DOCUMENTO_MAX_LENGTH = 20
_LOCALIDAD_MAX_LENGTH = 100
_BARRIO_MAX_LENGTH = 100

# `direccion` is `jsonb` (docker/postgres/init/01_schema.sql) with no DB-level size limit — this
# cap is a domain invariant, not a schema one, guarding against a single malformed/abusive
# record bloating storage indefinitely.
_DIRECCION_MAX_SERIALIZED_BYTES = 8 * 1024


class EstadoCliente(StrEnum):
    """`clientes.estado` (`ck_clientes_estado`): the only two values DOMAIN_MODEL.md §7.1
    documents for Cliente ("Activo / Inactivo")."""

    ACTIVO = "Activo"
    INACTIVO = "Inactivo"


class ClienteValidationError(ValueError):
    """Raised by `Cliente.create()` when one or more invariants are violated.

    `errors` holds every individual violation message (not just the first), so
    `ImportClientes` can report a granular rejection reason for each malformed record without
    aborting the rest of the batch.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True, slots=True)
class Cliente:
    """Domain entity: Cliente (DOMAIN_MODEL.md §7.1).

    Exists solely as the owner of one or more Suministros (RD-001/RD-002); it does not
    participate directly in anomaly detection. Field names mirror the `clientes` table exactly
    (docker/postgres/init/01_schema.sql) rather than the domain doc's camelCase, per this
    project's naming convention (see `contexts/README.md`): domain nouns and DB columns are the
    same Spanish identifiers.
    """

    id: UUID
    numero_cliente: str
    nombre: str
    estado: EstadoCliente = EstadoCliente.ACTIVO
    documento: str | None = None
    localidad: str | None = None
    barrio: str | None = None
    direccion: dict[str, Any] | None = None

    @staticmethod
    def create(
        *,
        numero_cliente: str | None,
        nombre: str | None,
        estado: str | None = None,
        documento: str | None = None,
        localidad: str | None = None,
        barrio: str | None = None,
        direccion: dict[str, Any] | None = None,
        id: UUID | None = None,
    ) -> "Cliente":
        """Validate and build a `Cliente`; raises `ClienteValidationError` on any violation.

        `id` is optional: a fresh one is generated for a brand-new client. Passing an existing
        id (as `ImportClientes` does when updating a client found by `numero_cliente`) keeps
        the row's identity stable across re-imports.
        """
        errors: list[str] = []

        numero_cliente_limpio = (numero_cliente or "").strip()
        if not numero_cliente_limpio:
            errors.append("numero_cliente es obligatorio")
        elif len(numero_cliente_limpio) > _NUMERO_CLIENTE_MAX_LENGTH:
            errors.append(
                f"numero_cliente no puede superar {_NUMERO_CLIENTE_MAX_LENGTH} caracteres"
            )

        nombre_limpio = (nombre or "").strip()
        if not nombre_limpio:
            errors.append("nombre es obligatorio")
        elif len(nombre_limpio) > _NOMBRE_MAX_LENGTH:
            errors.append(f"nombre no puede superar {_NOMBRE_MAX_LENGTH} caracteres")

        if estado is None:
            # Omitted entirely -> documented default (DOMAIN_MODEL.md §7.1: "Activo / Inactivo").
            estado_enum = EstadoCliente.ACTIVO
        elif not estado.strip():
            # An *explicit* blank estado is a distinct, invalid input -- not the same as
            # omitting the field -- so it is rejected instead of silently defaulted, matching
            # how an explicit blank numero_cliente/nombre is rejected rather than defaulted.
            errors.append("estado no puede ser una cadena vacía")
            # Placeholder value: unused because `errors` is non-empty, so `create()` raises below.
            estado_enum = EstadoCliente.ACTIVO
        else:
            try:
                estado_enum = EstadoCliente(estado)
            except ValueError:
                valores_validos = ", ".join(e.value for e in EstadoCliente)
                errors.append(f"estado inválido: {estado!r} (valores válidos: {valores_validos})")
                # Placeholder value: unused because `errors` is non-empty, so `create()` raises
                # below.
                estado_enum = EstadoCliente.ACTIVO

        if documento is not None and len(documento) > _DOCUMENTO_MAX_LENGTH:
            errors.append(f"documento no puede superar {_DOCUMENTO_MAX_LENGTH} caracteres")

        if localidad is not None and len(localidad) > _LOCALIDAD_MAX_LENGTH:
            errors.append(f"localidad no puede superar {_LOCALIDAD_MAX_LENGTH} caracteres")

        if barrio is not None and len(barrio) > _BARRIO_MAX_LENGTH:
            errors.append(f"barrio no puede superar {_BARRIO_MAX_LENGTH} caracteres")

        if direccion is not None:
            direccion_size = len(json.dumps(direccion).encode("utf-8"))
            if direccion_size > _DIRECCION_MAX_SERIALIZED_BYTES:
                errors.append(
                    f"direccion no puede superar {_DIRECCION_MAX_SERIALIZED_BYTES} bytes "
                    "serializado"
                )

        if errors:
            raise ClienteValidationError(errors)

        return Cliente(
            id=id or uuid4(),
            numero_cliente=numero_cliente_limpio,
            nombre=nombre_limpio,
            estado=estado_enum,
            documento=documento,
            localidad=localidad,
            barrio=barrio,
            direccion=direccion,
        )
