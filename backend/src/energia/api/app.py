"""FastAPI application factory: wires routers and lifespan."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from energia.api.routes.health import router as health_router
from energia.contexts.clientes.presentation.routes import router as clientes_router
from energia.contexts.consumos.presentation.routes import lecturas_router, lotes_router
from energia.contexts.suministros.presentation.routes import router as suministros_router
from energia.shared.config import get_settings
from energia.shared.db import build_engine, build_sessionmaker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the async engine/session factory on this app's event loop, dispose on shutdown.

    Building these inside the lifespan (instead of as module-level singletons) means they are
    bound to the event loop the app actually runs on, so a second independent event loop
    (e.g. a second `asyncio.run()` in tests, or an ASGI server worker restart) does not hit
    "attached to a different loop" errors.
    """
    engine = build_engine(get_settings())
    app.state.engine = engine
    app.state.session_factory = build_sessionmaker(engine)
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(title="EnergIA API", lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(clientes_router)
    app.include_router(suministros_router)
    app.include_router(lecturas_router)
    app.include_router(lotes_router)
    return app
