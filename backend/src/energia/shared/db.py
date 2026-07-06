"""SQLAlchemy async engine, session factory and the FastAPI session dependency."""

from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from energia.shared.config import Settings


def build_engine(settings: Settings) -> AsyncEngine:
    """Build the async SQLAlchemy engine for the given settings.

    Engine creation is lazy: no network connection is opened until a query runs.
    """
    return create_async_engine(settings.sqlalchemy_database_url, pool_pre_ping=True)


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build the session factory bound to the given engine."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a single request-scoped AsyncSession.

    The session factory is built once per application lifespan (see
    `energia.api.app.lifespan`) and stored on `app.state`, so it is bound to the event loop
    the app is actually running on. A module-level singleton engine would instead be bound
    to whichever event loop happened to be active at import time, breaking with "attached to
    a different loop" as soon as a second `asyncio.run()`/event loop uses it.
    """
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        yield session
