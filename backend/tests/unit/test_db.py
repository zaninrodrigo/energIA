"""Unit tests for energia.shared.db: engine/session-factory construction and the FastAPI dependency.

These tests never open a real network connection: SQLAlchemy's async engine and session are
lazy, so building them and entering/exiting the session context manager does not require a
reachable database.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from energia.shared.config import Settings
from energia.shared.db import build_engine, build_sessionmaker, get_db_session


def test_build_engine_uses_the_settings_database_url() -> None:
    settings = Settings(_env_file=None)

    engine = build_engine(settings)

    assert isinstance(engine, AsyncEngine)
    assert engine.url.render_as_string(hide_password=False) == settings.sqlalchemy_database_url


def test_build_sessionmaker_returns_an_async_sessionmaker() -> None:
    settings = Settings(_env_file=None)
    engine = build_engine(settings)

    session_factory = build_sessionmaker(engine)

    assert isinstance(session_factory, async_sessionmaker)


async def test_get_db_session_yields_a_single_async_session_and_stops() -> None:
    """The dependency pulls the sessionmaker from `request.app.state`, set up by the lifespan."""
    settings = Settings(_env_file=None)
    engine = build_engine(settings)
    session_factory = build_sessionmaker(engine)
    fake_request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(session_factory=session_factory))
    )

    generator = get_db_session(fake_request)  # type: ignore[arg-type]

    session = await anext(generator)
    assert isinstance(session, AsyncSession)

    with pytest.raises(StopAsyncIteration):
        await anext(generator)
