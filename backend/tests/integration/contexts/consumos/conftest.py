"""Integration fixtures for the `consumos` context: a real DB session / HTTP client wired to
`energia_test` (never to `energia`).

Built on top of the reusable pieces in `tests/integration/conftest.py`, mirroring `tests/
integration/contexts/suministros/conftest.py` exactly for the session/client fixtures. Adds
`insert_suministro` so `consumos`' own tests can set up its FK dependency (a `suministro_id`,
which itself needs a `cliente_id` and `categoria_tarifaria_id`) without importing anything from
`contexts.clientes`/`contexts.suministros` -- consistent with the same module-boundary discipline
the production `SuministroDirectory` port follows (see `contexts/README.md`).
"""

from collections.abc import AsyncIterator
from datetime import date
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
async def lecturas_client(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    """An httpx client over the real FastAPI app (`clientes`, `suministros` and `consumos`
    routers), with `get_db_session` overridden to use `energia_test`. Because the app wires all
    three contexts' routers, this same client can seed a cliente and a suministro through their
    own import endpoints and then exercise `consumos` against them -- proving all three vertical
    slices compose, not just that each works in isolation.
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


@pytest.fixture
async def lotes_client(lecturas_client: AsyncClient) -> AsyncClient:
    """Alias of `lecturas_client` for readability in Lote-focused tests -- same app/client
    (every context's router, `energia_test`-backed session), `Lote` needs no FK seeding helper
    unlike `Lectura` (`lotes` references no other table), so no separate fixture body is needed.
    """
    return lecturas_client


@pytest.fixture
async def consumos_client(lecturas_client: AsyncClient) -> AsyncClient:
    """Alias of `lecturas_client` for readability in Consumo-focused tests -- same app/client
    (every context's router, `energia_test`-backed session)."""
    return lecturas_client


async def insert_cliente(
    session: AsyncSession, *, numero_cliente: str, nombre: str = "Cliente de prueba"
) -> UUID:
    """Insert a minimal cliente row directly via raw SQL, bypassing `contexts.clientes`'
    application/domain code entirely -- mirrors `tests.integration.contexts.suministros.conftest.
    insert_cliente` exactly.
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
    `energia_test` replays too) by its `nombre`. Mirrors `tests.integration.contexts.suministros.
    conftest.get_categoria_tarifaria_id` exactly.
    """
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
) -> UUID:
    """Insert a minimal suministro row directly via raw SQL, bypassing
    `contexts.suministros`' application/domain code entirely -- `consumos`' repository-level
    tests (which have only a bare `db_session`, no HTTP client) set up their FK dependency this
    way, exactly like the production `SqlDirectSuministroDirectory` reaches the `suministros`
    table: a direct SQL statement, never a cross-context import.

    `cliente_id`/`categoria_tarifaria_id` are resolved to a fresh cliente / the seeded
    "Residencial" categoria when not given, so a caller that only cares about the suministro's
    own identity does not have to set up its FK chain by hand every time.
    """
    if cliente_id is None:
        cliente_id = await insert_cliente(session, numero_cliente=f"cliente-de-{numero_suministro}")
    if categoria_tarifaria_id is None:
        categoria_tarifaria_id = await get_categoria_tarifaria_id(session)

    suministro_id = uuid4()
    await session.execute(
        text(
            "INSERT INTO suministros "
            "(id, numero_suministro, cliente_id, categoria_tarifaria_id, fecha_alta) "
            "VALUES (:id, :numero_suministro, :cliente_id, :categoria_tarifaria_id, :fecha_alta)"
        ),
        {
            "id": suministro_id,
            "numero_suministro": numero_suministro,
            "cliente_id": cliente_id,
            "categoria_tarifaria_id": categoria_tarifaria_id,
            "fecha_alta": date(2024, 1, 1),
        },
    )
    await session.commit()
    return suministro_id


@pytest.fixture
async def suministro_id(db_session: AsyncSession) -> UUID:
    """A freshly inserted suministro, ready to be referenced by a `Lectura`."""
    return await insert_suministro(db_session, numero_suministro="SUM-9999")


async def insert_lote(
    session: AsyncSession, *, codigo_lote: str, estado: str = "Pendiente"
) -> UUID:
    """Insert a minimal lote row directly via raw SQL -- `Consumo`'s own tests (which need a
    `lote_id` FK, `fk_consumos_lote`) set up this dependency the same way
    `insert_suministro`/`insert_cliente` do, bypassing `ImportLotes`/`Lote.create()` entirely.
    """
    lote_id = uuid4()
    await session.execute(
        text(
            "INSERT INTO lotes (id, codigo_lote, fecha_importacion, cantidad_registros, estado) "
            "VALUES (:id, :codigo_lote, now(), 0, :estado)"
        ),
        {"id": lote_id, "codigo_lote": codigo_lote, "estado": estado},
    )
    await session.commit()
    return lote_id


@pytest.fixture
async def lote_id(db_session: AsyncSession) -> UUID:
    """A freshly inserted lote, ready to be referenced by a `Consumo`."""
    return await insert_lote(db_session, codigo_lote="LOTE-9999")
