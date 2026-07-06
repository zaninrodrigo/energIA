"""Unit tests for the FastAPI application factory and its lifespan."""

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from energia.api.app import create_app


def test_create_app_returns_a_fastapi_instance() -> None:
    app = create_app()

    assert isinstance(app, FastAPI)


def test_create_app_registers_the_health_route() -> None:
    app = create_app()

    openapi_schema = app.openapi()

    assert "/health" in openapi_schema["paths"]


async def test_lifespan_wires_engine_and_session_factory_onto_app_state() -> None:
    """httpx's ASGITransport skips the lifespan; LifespanManager actually triggers it."""
    app = create_app()

    async with LifespanManager(app):
        assert isinstance(app.state.engine, AsyncEngine)
        assert isinstance(app.state.session_factory, async_sessionmaker)


async def test_lifespan_disposes_the_engine_on_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app()
    disposed_engines: list[AsyncEngine] = []
    original_dispose = AsyncEngine.dispose

    async def _tracking_dispose(self: AsyncEngine) -> None:
        disposed_engines.append(self)
        await original_dispose(self)

    monkeypatch.setattr(AsyncEngine, "dispose", _tracking_dispose)

    async with LifespanManager(app):
        engine = app.state.engine

    assert disposed_engines == [engine]
