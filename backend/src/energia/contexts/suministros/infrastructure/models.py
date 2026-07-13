"""SQLAlchemy ORM mapping for `suministros` and `categorias_tarifarias`
(docker/postgres/init/01_schema.sql).

This is the only place in the `suministros` context allowed to import SQLAlchemy (ADR-001): the
domain and application layers never see these models, only `Suministro` (domain/suministro.py).
Neither table is created from these models -- both already exist via the raw DDL in
`docker/postgres/init/` (production) or replayed by `tests/integration/conftest.py`
(`energia_test`) -- this module only maps to them.

`CategoriaTarifariaModel` is mapped here, not in a separate `categorias_tarifarias` package,
because `CategoriaTarifaria` belongs to the same bounded context as `Suministro`
(DOMAIN_MODEL.md §4.2, "Gestión de Suministros") -- see `SqlAlchemyCategoriaTarifariaDirectory`
(infrastructure/categoria_tarifaria_directory.py) for the read-only query it backs. It only maps
the columns that query needs (`id`, `nombre`, `deleted_at`), not every column the table has:
this model is never used to INSERT/UPDATE that table, only to SELECT from it.
"""

import uuid
from datetime import date, datetime

from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base local to the `suministros` context's infrastructure layer."""


class SuministroModel(Base):
    __tablename__ = "suministros"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    numero_suministro: Mapped[str] = mapped_column()
    cliente_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    categoria_tarifaria_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    localidad: Mapped[str | None] = mapped_column(default=None)
    barrio: Mapped[str | None] = mapped_column(default=None)
    estado: Mapped[str] = mapped_column()
    fecha_alta: Mapped[date] = mapped_column()
    created_at: Mapped[datetime] = mapped_column()
    updated_at: Mapped[datetime | None] = mapped_column(default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), default=None)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), default=None)


class CategoriaTarifariaModel(Base):
    __tablename__ = "categorias_tarifarias"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    nombre: Mapped[str] = mapped_column()
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)
