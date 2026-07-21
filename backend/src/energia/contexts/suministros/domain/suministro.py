"""Suministro entity and invariants (DOMAIN_MODEL.md §7.2, docker/postgres/init/01_schema.sql).

Persistence-ignorant on purpose (ADR-001), mirroring `contexts/clientes/domain/cliente.py`'s
pattern exactly: no SQLAlchemy or any other infrastructure import belongs in this module.
`Suministro.create()` is the only way to build a `Suministro` from untrusted input -- it
enforces every invariant and raises `SuministroValidationError` (with every violation found,
not just the first) instead of silently accepting bad data. Reconstructing a `Suministro` from a
trusted source (a database row already known to satisfy these invariants) can call the dataclass
constructor directly, e.g. from `infrastructure/suministro_repository.py`.

`cliente_id` and `categoria_tarifaria_id` are plain, already-resolved UUIDs here -- resolving the
natural keys a source record actually carries (`numero_cliente`, the categoria's `nombre`) is
`ImportSuministros`' job (application layer), via the `ClienteDirectory` /
`CategoriaTarifariaDirectory` ports (domain/ports.py). This entity only enforces that *some*
cliente and categoria were resolved (RD-006, RD-005/RD-008) -- not that the referenced rows
exist, which the application layer already guarantees by construction before calling `create()`.
"""

from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid4

# Schema-derived length limits (docker/postgres/init/01_schema.sql, table `suministros`). Kept
# in sync with the DB's varchar sizes so a rejected record is caught here instead of surfacing
# as an opaque database error later.
_NUMERO_SUMINISTRO_MAX_LENGTH = 30
_LOCALIDAD_MAX_LENGTH = 100
_BARRIO_MAX_LENGTH = 100
_ESTADO_MAX_LENGTH = 15
_MEDIDOR_MAX_LENGTH = 20

# `suministros.estado` has no CHECK constraint (unlike `clientes.estado`): DOMAIN_MODEL.md §7.2
# does not enumerate closed values for it, so 01_schema.sql deliberately leaves it an open
# varchar(15) with this default -- see that file's comment on the column. This entity therefore
# validates length only, not membership in a fixed set.
_ESTADO_DEFAULT = "Activo"


class SuministroValidationError(ValueError):
    """Raised by `Suministro.create()` when one or more invariants are violated.

    `errors` holds every individual violation message (not just the first), so
    `ImportSuministros` can report a granular rejection reason for each malformed record
    without aborting the rest of the batch.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True, slots=True)
class Suministro:
    """Domain entity: Suministro (DOMAIN_MODEL.md §7.2). Aggregate Root of the Suministros
    bounded context.

    Field names mirror the `suministros` table exactly (docker/postgres/init/01_schema.sql)
    rather than the domain doc's camelCase, per this project's naming convention (see
    `contexts/README.md`): domain nouns and DB columns are the same Spanish identifiers.
    """

    id: UUID
    numero_suministro: str
    cliente_id: UUID
    categoria_tarifaria_id: UUID
    fecha_alta: date
    estado: str = _ESTADO_DEFAULT
    localidad: str | None = None
    barrio: str | None = None
    # medidor: serial del aparato físico (numero_suministro es el ruta-folio). Geo-referencia
    # opcional del punto de suministro (REAL_DATA_IMPORT_SPEC.md).
    medidor: str | None = None
    latitud: float | None = None
    longitud: float | None = None

    @staticmethod
    def create(
        *,
        numero_suministro: str | None,
        cliente_id: UUID | None,
        categoria_tarifaria_id: UUID | None,
        fecha_alta: date | str | None,
        estado: str | None = None,
        localidad: str | None = None,
        barrio: str | None = None,
        medidor: str | None = None,
        latitud: float | None = None,
        longitud: float | None = None,
        id: UUID | None = None,
    ) -> "Suministro":
        """Validate and build a `Suministro`; raises `SuministroValidationError` on any
        violation.

        `id` is optional: a fresh one is generated for a brand-new suministro. Passing an
        existing id (as `ImportSuministros` does when updating a row found by
        `numero_suministro`) keeps the row's identity stable across re-imports.
        """
        errors: list[str] = []

        numero_suministro_limpio = (numero_suministro or "").strip()
        if not numero_suministro_limpio:
            errors.append("numero_suministro es obligatorio")
        elif len(numero_suministro_limpio) > _NUMERO_SUMINISTRO_MAX_LENGTH:
            errors.append(
                f"numero_suministro no puede superar {_NUMERO_SUMINISTRO_MAX_LENGTH} caracteres"
            )

        if cliente_id is None:
            # RD-006: "No puede existir un suministro sin cliente."
            errors.append("cliente_id es obligatorio")

        if categoria_tarifaria_id is None:
            # RD-005/RD-008: "Todo suministro pertenece a una única categoría tarifaria."
            errors.append("categoria_tarifaria_id es obligatorio")

        fecha_alta_valida: date | None
        if fecha_alta is None:
            errors.append("fecha_alta es obligatoria")
            fecha_alta_valida = None
        elif isinstance(fecha_alta, date):
            fecha_alta_valida = fecha_alta
        else:
            try:
                fecha_alta_valida = date.fromisoformat(fecha_alta.strip())
            except ValueError:
                errors.append(f"fecha_alta inválida: {fecha_alta!r} (formato esperado: YYYY-MM-DD)")
                fecha_alta_valida = None

        if estado is None:
            # Omitted entirely -> documented default (docker/postgres/init/01_schema.sql:
            # `estado varchar(15) NOT NULL DEFAULT 'Activo'`).
            estado_limpio = _ESTADO_DEFAULT
        elif not estado.strip():
            # An *explicit* blank estado is a distinct, invalid input -- not the same as
            # omitting the field -- so it is rejected instead of silently defaulted, matching
            # `Cliente.create()`'s handling of an explicit blank estado.
            errors.append("estado no puede ser una cadena vacía")
            # Placeholder value: unused because `errors` is non-empty, so `create()` raises below.
            estado_limpio = _ESTADO_DEFAULT
        elif len(estado.strip()) > _ESTADO_MAX_LENGTH:
            # Mirrors numero_suministro_limpio: the length limit is checked against the
            # *stripped* value (the one actually stored), not the raw input -- otherwise
            # surrounding whitespace alone could reject an otherwise schema-valid estado.
            errors.append(f"estado no puede superar {_ESTADO_MAX_LENGTH} caracteres")
            # Placeholder value: unused because `errors` is non-empty, so `create()` raises below.
            estado_limpio = _ESTADO_DEFAULT
        else:
            estado_limpio = estado.strip()

        if localidad is not None and len(localidad) > _LOCALIDAD_MAX_LENGTH:
            errors.append(f"localidad no puede superar {_LOCALIDAD_MAX_LENGTH} caracteres")

        if barrio is not None and len(barrio) > _BARRIO_MAX_LENGTH:
            errors.append(f"barrio no puede superar {_BARRIO_MAX_LENGTH} caracteres")

        medidor_limpio = medidor.strip() if medidor is not None else None
        if medidor_limpio is not None and len(medidor_limpio) > _MEDIDOR_MAX_LENGTH:
            errors.append(f"medidor no puede superar {_MEDIDOR_MAX_LENGTH} caracteres")

        if latitud is not None and not (-90 <= latitud <= 90):
            errors.append(f"latitud fuera de rango [-90, 90]: {latitud}")

        if longitud is not None and not (-180 <= longitud <= 180):
            errors.append(f"longitud fuera de rango [-180, 180]: {longitud}")

        if errors:
            raise SuministroValidationError(errors)

        # Narrows `cliente_id`/`categoria_tarifaria_id`/`fecha_alta_valida` from their `| None`
        # parameter types to their non-optional dataclass field types for mypy. Safe: each would
        # already have appended to `errors` above if `None`/invalid, and `create()` already
        # raised and returned above whenever `errors` is non-empty.
        assert cliente_id is not None
        assert categoria_tarifaria_id is not None
        assert fecha_alta_valida is not None

        return Suministro(
            id=id or uuid4(),
            numero_suministro=numero_suministro_limpio,
            cliente_id=cliente_id,
            categoria_tarifaria_id=categoria_tarifaria_id,
            fecha_alta=fecha_alta_valida,
            estado=estado_limpio,
            localidad=localidad,
            barrio=barrio,
            medidor=medidor_limpio,
            latitud=latitud,
            longitud=longitud,
        )
