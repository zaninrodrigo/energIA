"""SQLAlchemy ORM mapping for `lecturas` (docker/postgres/init/01_schema.sql).

This is the only place in the `consumos` context allowed to import SQLAlchemy (ADR-001): the
domain and application layers never see this model, only `Lectura` (domain/lectura.py). The
table is not created from this model -- it already exists via the raw DDL in
`docker/postgres/init/` (production) or replayed by `tests/integration/conftest.py`
(`energia_test`) -- this module only maps to it. Mirrors
`contexts/suministros/infrastructure/models.py` exactly.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Numeric
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
