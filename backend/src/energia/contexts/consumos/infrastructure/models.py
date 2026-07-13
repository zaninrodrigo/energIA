"""SQLAlchemy ORM mapping for `lecturas` and `lotes` (docker/postgres/init/01_schema.sql).

This is the only place in the `consumos` context allowed to import SQLAlchemy (ADR-001): the
domain and application layers never see these models, only `Lectura`/`Lote` (domain/lectura.py,
domain/lote.py). The tables are not created from these models -- they already exist via the raw
DDL in `docker/postgres/init/` (production) or replayed by `tests/integration/conftest.py`
(`energia_test`) -- this module only maps to them. Mirrors
`contexts/suministros/infrastructure/models.py` exactly.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base local to the `consumos` context's infrastructure layer."""


class LecturaModel(Base):
    __tablename__ = "lecturas"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    suministro_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    fecha_lectura: Mapped[date] = mapped_column()
    # Explicit `Numeric(12, 3)` (not left to SQLAlchemy's type inference): must match
    # `lecturas.lectura_anterior`/`lectura_actual numeric(12,3)` exactly so values round-trip as
    # `Decimal`, not `float` (which would silently reintroduce the binary-rounding issues
    # `Lectura.create()`'s `Decimal(str(value))` parsing deliberately avoids).
    lectura_anterior: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    lectura_actual: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    dias_facturados: Mapped[int] = mapped_column()
    created_at: Mapped[datetime] = mapped_column()
    updated_at: Mapped[datetime | None] = mapped_column(default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), default=None)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), default=None)


class LoteModel(Base):
    __tablename__ = "lotes"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    codigo_lote: Mapped[str] = mapped_column()
    nombre: Mapped[str | None] = mapped_column(default=None)
    # Explicit `DateTime(timezone=True)` (not left to SQLAlchemy's type inference from the
    # `datetime` annotation, which defaults to a naive `TIMESTAMP WITHOUT TIME ZONE`): must match
    # `lotes.fecha_importacion timestamptz` exactly. `Lote.create()` generates a timezone-aware
    # `datetime.now(UTC)` (domain/lote.py) -- without this explicit type, asyncpg rejects that
    # value outright ("can't subtract offset-naive and offset-aware datetimes") the moment it is
    # bound as a query parameter, since SQLAlchemy would otherwise treat the column as naive.
    fecha_importacion: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cantidad_registros: Mapped[int] = mapped_column()
    # Plain `str` column, not a DB-level enum type: `lotes.estado` is `varchar(15)` with a CHECK
    # constraint (`ck_lotes_estado`), not a Postgres `ENUM` type. `EstadoLote` (domain/lote.py)
    # is the domain-side closed set; this model stores/reads its `.value` -- see
    # `lote_repository.py`'s `_to_domain`/`save()`.
    estado: Mapped[str] = mapped_column()
    created_at: Mapped[datetime] = mapped_column()
    updated_at: Mapped[datetime | None] = mapped_column(default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), default=None)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), default=None)
