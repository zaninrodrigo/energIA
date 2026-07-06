"""Integration test for GET /health against the real running energia-db container.

Requires the local docker-compose database to be up (see /docker-compose.yml,
host port 5434). Settings defaults already point at it, so no extra env vars
are needed to run this locally.
"""

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from energia.api.app import create_app

pytestmark = pytest.mark.integration


async def test_health_reports_database_up_against_the_real_database() -> None:
    app = create_app()

    # httpx's ASGITransport does not trigger the ASGI lifespan on its own; LifespanManager
    # runs startup (building the real engine/session factory on app.state) and shutdown.
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "up"}
