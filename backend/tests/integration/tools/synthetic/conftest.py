"""Integration fixtures for `energia.tools.synthetic.api_loader`: an httpx client over the real
FastAPI app (every context's router), wired to `energia_test` -- never to `energia`. Mirrors
`tests/integration/contexts/consumos/conftest.py`'s `lecturas_client` fixture exactly: this
module needs no FK-seeding helpers of its own (`api_loader` imports everything itself, through
the API, in FK-dependency order), just the app/client wiring.
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
async def client(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    """An httpx client over the real FastAPI app, `get_db_session` overridden to use
    `energia_test`."""
    app: FastAPI = create_app()

    async def _override_get_db_session() -> AsyncIterator[AsyncSession]:
        async with test_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_get_db_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http_client:
        yield http_client

    app.dependency_overrides.clear()
    await truncate_all_tables(test_session_factory)
