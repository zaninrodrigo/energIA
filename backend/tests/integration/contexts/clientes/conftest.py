"""Integration fixtures for the `clientes` context: a real DB session / HTTP client wired to
`energia_test` (never to `energia`).

Built on top of the reusable pieces in `tests/integration/conftest.py`. Only tests that
request `db_session` or `clientes_client` trigger `energia_test` creation/schema setup — the
health integration test, which doesn't request either, is unaffected.
"""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
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
async def clientes_client(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    """An httpx client over the real FastAPI app, with `get_db_session` overridden to use
    `energia_test` instead of the database `lifespan` would otherwise wire up (the real
    `energia`). No `LifespanManager` needed: overriding the dependency directly makes the
    lifespan-built engine irrelevant to these tests, same pattern as `tests/unit/test_health.py`.
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
