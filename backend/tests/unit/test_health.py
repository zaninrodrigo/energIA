"""Unit tests for GET /health, with the database dependency overridden both ways."""

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from energia.api.routes import health as health_module
from energia.shared.db import get_db_session


class _FakeSessionUp:
    """Stands in for a healthy database session: SELECT 1 succeeds."""

    async def execute(self, *args: object, **kwargs: object) -> None:
        return None


class _FakeSessionDown:
    """Stands in for an unreachable database: SELECT 1 raises."""

    async def execute(self, *args: object, **kwargs: object) -> None:
        raise ConnectionRefusedError("database unreachable")


class _FakeSessionHangs:
    """Stands in for a black-holed network: SELECT 1 never returns."""

    async def execute(self, *args: object, **kwargs: object) -> None:
        await asyncio.sleep(10)


async def _override_session_up() -> _FakeSessionUp:
    return _FakeSessionUp()


async def _override_session_down() -> _FakeSessionDown:
    return _FakeSessionDown()


async def _override_session_hangs() -> _FakeSessionHangs:
    return _FakeSessionHangs()


async def test_health_reports_database_up_when_select_1_succeeds(app: FastAPI) -> None:
    app.dependency_overrides[get_db_session] = _override_session_up

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "up"}


async def test_health_returns_503_when_select_1_fails(app: FastAPI) -> None:
    app.dependency_overrides[get_db_session] = _override_session_down

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "down"}


async def test_health_returns_503_when_db_probe_times_out(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A black-holed network must not hang the endpoint: the probe is bounded by a timeout."""
    monkeypatch.setattr(health_module, "_DB_PROBE_TIMEOUT_SECONDS", 0.05)
    app.dependency_overrides[get_db_session] = _override_session_hangs

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "down"}
