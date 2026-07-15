"""Integration fixtures for the `motor` context: a real DB session / HTTP client wired to
`energia_test` (never to `energia`).

Built on top of the reusable pieces in `tests/integration/conftest.py`, mirroring
`tests/integration/contexts/consumos/conftest.py`'s `insert_cliente`/`insert_suministro`/
`insert_lote` exactly (duplicated here rather than imported: `motor`'s own tests must not depend
on another context's test module, the same module-boundary discipline production code follows).
Adds `insert_lectura`/`insert_consumo` -- raw-SQL helpers `motor`'s own tests need to build the
realistic V1-V7 fixtures (`SqlValidacionDataSource`'s set-based query needs real
`consumos`/`lecturas` rows to read back), which no existing `consumos` test conftest exposes
(that context's own tests build these rows through its HTTP import endpoints instead).
"""

from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from energia.api.app import create_app
from energia.shared.db import get_db_session
from tests.integration.conftest import truncate_all_tables


@pytest.fixture
async def db_session(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A session against `energia_test`, truncated after the test so the next one starts clean."""
    async with test_session_factory() as session:
        yield session
    await truncate_all_tables(test_session_factory)


@pytest.fixture
async def motor_client(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    """An httpx client over the real FastAPI app (every context's router, `motor` included), with
    `get_db_session` overridden to use `energia_test`. Lets a test seed a cliente/suministro/
    lote/consumo through the real `clientes`/`suministros`/`consumos` import endpoints and then
    exercise `POST /api/v1/motor/lotes/{codigo_lote}/procesar` against them -- proving the whole
    vertical slice composes, not just that `motor` works against hand-inserted rows.
    """
    app: FastAPI = create_app()

    async def _override_get_db_session() -> AsyncIterator[AsyncSession]:
        async with test_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_get_db_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    await truncate_all_tables(test_session_factory)


async def insert_cliente(
    session: AsyncSession, *, numero_cliente: str, nombre: str = "Cliente de prueba"
) -> UUID:
    """Mirrors `tests.integration.contexts.consumos.conftest.insert_cliente` exactly."""
    cliente_id = uuid4()
    await session.execute(
        text(
            "INSERT INTO clientes (id, numero_cliente, nombre) "
            "VALUES (:id, :numero_cliente, :nombre)"
        ),
        {"id": cliente_id, "numero_cliente": numero_cliente, "nombre": nombre},
    )
    await session.commit()
    return cliente_id


async def get_categoria_tarifaria_id(session: AsyncSession, *, nombre: str = "Residencial") -> UUID:
    """Mirrors `tests.integration.contexts.consumos.conftest.get_categoria_tarifaria_id`
    exactly -- returns the `id` of a seeded categoria tarifaria (`docker/postgres/init/
    04_seed.sql`, replayed by `energia_test` too)."""
    result = await session.execute(
        text("SELECT id FROM categorias_tarifarias WHERE nombre = :nombre"), {"nombre": nombre}
    )
    row = result.first()
    assert row is not None, f"seeded categoria tarifaria {nombre!r} not found"
    return row[0]  # type: ignore[no-any-return]


async def insert_suministro(
    session: AsyncSession,
    *,
    numero_suministro: str,
    cliente_id: UUID | None = None,
    categoria_tarifaria_id: UUID | None = None,
    localidad: str | None = None,
    fecha_alta: date = date(2024, 1, 1),
    estado: str = "Activo",
) -> UUID:
    """Mirrors `tests.integration.contexts.consumos.conftest.insert_suministro`, extended with
    optional `localidad`/`fecha_alta` (default unchanged from before) -- Etapa 3's feature tests
    (US-008, AI_ENGINE_SPEC.md §6) need control over both: `localidad` for cohort grouping
    (DEC-008) and `fecha_alta` for F14 `supply_age_days`. `estado` (default `'Activo'`, matching
    `suministros.estado`'s own DDL default) was added for Etapa 5 (§8): R1's "suministro activo"
    condition needs a way to seed a NON-active suministro too."""
    if cliente_id is None:
        cliente_id = await insert_cliente(session, numero_cliente=f"cliente-de-{numero_suministro}")
    if categoria_tarifaria_id is None:
        categoria_tarifaria_id = await get_categoria_tarifaria_id(session)

    suministro_id = uuid4()
    await session.execute(
        text(
            "INSERT INTO suministros "
            "(id, numero_suministro, cliente_id, categoria_tarifaria_id, fecha_alta, localidad, "
            "estado) "
            "VALUES (:id, :numero_suministro, :cliente_id, :categoria_tarifaria_id, "
            ":fecha_alta, :localidad, :estado)"
        ),
        {
            "id": suministro_id,
            "numero_suministro": numero_suministro,
            "cliente_id": cliente_id,
            "categoria_tarifaria_id": categoria_tarifaria_id,
            "fecha_alta": fecha_alta,
            "localidad": localidad,
            "estado": estado,
        },
    )
    await session.commit()
    return suministro_id


async def insert_lote(
    session: AsyncSession,
    *,
    codigo_lote: str,
    estado: str = "Pendiente",
    cantidad_registros: int = 0,
) -> UUID:
    """Mirrors `tests.integration.contexts.consumos.conftest.insert_lote`, extended with
    `cantidad_registros` -- `motor`'s completeness gate (`domain/completitud.py`) needs it set to
    something other than the default `0` to build a "ready" lote."""
    lote_id = uuid4()
    await session.execute(
        text(
            "INSERT INTO lotes (id, codigo_lote, fecha_importacion, cantidad_registros, estado) "
            "VALUES (:id, :codigo_lote, now(), :cantidad_registros, :estado)"
        ),
        {
            "id": lote_id,
            "codigo_lote": codigo_lote,
            "cantidad_registros": cantidad_registros,
            "estado": estado,
        },
    )
    await session.commit()
    return lote_id


async def insert_lectura(
    session: AsyncSession,
    *,
    suministro_id: UUID,
    fecha_lectura: date,
    lectura_anterior: Decimal,
    lectura_actual: Decimal,
    dias_facturados: int,
) -> UUID:
    """Raw-SQL insert of one `lecturas` row -- `motor`'s tests build V2/V3-relevant fixtures this
    way, bypassing `contexts.consumos`' domain/application code entirely (same discipline
    `insert_suministro`/`insert_lote` above already follow)."""
    lectura_id = uuid4()
    await session.execute(
        text(
            "INSERT INTO lecturas "
            "(id, suministro_id, fecha_lectura, lectura_anterior, lectura_actual, "
            "dias_facturados) "
            "VALUES (:id, :suministro_id, :fecha_lectura, :lectura_anterior, :lectura_actual, "
            ":dias_facturados)"
        ),
        {
            "id": lectura_id,
            "suministro_id": suministro_id,
            "fecha_lectura": fecha_lectura,
            "lectura_anterior": lectura_anterior,
            "lectura_actual": lectura_actual,
            "dias_facturados": dias_facturados,
        },
    )
    await session.commit()
    return lectura_id


async def insert_consumo(
    session: AsyncSession,
    *,
    suministro_id: UUID,
    lote_id: UUID,
    fecha_inicio: date,
    fecha_fin: date,
    dias_facturados: int,
    kwh: Decimal,
    lectura_id: UUID | None = None,
) -> UUID:
    """Raw-SQL insert of one `consumos` row -- see `insert_lectura`'s docstring for the same
    rationale."""
    consumo_id = uuid4()
    await session.execute(
        text(
            "INSERT INTO consumos "
            "(id, suministro_id, lote_id, lectura_id, fecha_inicio, fecha_fin, "
            "dias_facturados, kwh) "
            "VALUES (:id, :suministro_id, :lote_id, :lectura_id, :fecha_inicio, :fecha_fin, "
            ":dias_facturados, :kwh)"
        ),
        {
            "id": consumo_id,
            "suministro_id": suministro_id,
            "lote_id": lote_id,
            "lectura_id": lectura_id,
            "fecha_inicio": fecha_inicio,
            "fecha_fin": fecha_fin,
            "dias_facturados": dias_facturados,
            "kwh": kwh,
        },
    )
    await session.commit()
    return consumo_id
