"""Integration fixtures for the `suministros` context: a real DB session / HTTP client wired to
`energia_test` (never to `energia`).

Built on top of the reusable pieces in `tests/integration/conftest.py`, mirroring `tests/
integration/contexts/clientes/conftest.py` exactly for the session/client fixtures. Adds
`insert_cliente`/`get_categoria_tarifaria_id` helpers so `suministros`' own tests can set up
their FK dependencies (a `cliente_id`, a `categoria_tarifaria_id`) without importing anything
from `contexts.clientes` -- consistent with the same module-boundary discipline the production
`ClienteDirectory` port follows (see `contexts/README.md`).
"""

from collections.abc import AsyncIterator
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
async def suministros_client(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    """An httpx client over the real FastAPI app (both `clientes` and `suministros` routers),
    with `get_db_session` overridden to use `energia_test`. Because the app wires both
    contexts' routers, this same client can seed a cliente via `POST /api/v1/clientes/import`
    and then exercise `suministros` against it -- proving the two vertical slices compose, not
    just that each works in isolation.
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
    """Insert a minimal cliente row directly via raw SQL, bypassing `contexts.clientes`'
    application/domain code entirely -- `suministros`' repository-level tests (which have only a
    bare `db_session`, no HTTP client) set up their own FK dependency this way, exactly like the
    production `SqlDirectClienteDirectory` reaches the `clientes` table: a direct SQL statement,
    never a cross-context import.
    """
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
    """Return the `id` of a seeded categoria tarifaria (docker/postgres/init/04_seed.sql, which
    `energia_test` replays too) by its `nombre`. Defaults to "Residencial", one of the five
    categories the seed data guarantees exist.
    """
    result = await session.execute(
        text("SELECT id FROM categorias_tarifarias WHERE nombre = :nombre"), {"nombre": nombre}
    )
    row = result.first()
    assert row is not None, f"seeded categoria tarifaria {nombre!r} not found"
    return row[0]  # type: ignore[no-any-return]


@pytest.fixture
async def cliente_id(db_session: AsyncSession) -> UUID:
    """A freshly inserted cliente, ready to be referenced by a `Suministro`."""
    return await insert_cliente(db_session, numero_cliente="9999")


@pytest.fixture
async def categoria_tarifaria_id(db_session: AsyncSession) -> UUID:
    """The seeded "Residencial" categoria tarifaria's id."""
    return await get_categoria_tarifaria_id(db_session)
