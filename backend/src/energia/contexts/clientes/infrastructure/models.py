"""SQLAlchemy ORM mapping for the `clientes` table (docker/postgres/init/01_schema.sql).

This is the only place in the `clientes` context allowed to import SQLAlchemy (ADR-001): the
domain and application layers never see `ClienteModel`, only `Cliente` (domain/cliente.py).
The table itself is not created from this model — it already exists via the raw DDL in
`docker/postgres/init/` (production) or replayed by `tests/integration/conftest.py`
(`energia_test`) — this class only maps to it.
"""

import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base local to the `clientes` context's infrastructure layer."""


class ClienteModel(Base):
    __tablename__ = "clientes"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    numero_cliente: Mapped[str] = mapped_column()
    nombre: Mapped[str] = mapped_column()
    estado: Mapped[str] = mapped_column()
    documento: Mapped[str | None] = mapped_column(default=None)
    localidad: Mapped[str | None] = mapped_column(default=None)
    barrio: Mapped[str | None] = mapped_column(default=None)
    direccion: Mapped[dict[str, object] | None] = mapped_column(JSONB, default=None)
    created_at: Mapped[datetime] = mapped_column()
    updated_at: Mapped[datetime | None] = mapped_column(default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), default=None)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), default=None)
