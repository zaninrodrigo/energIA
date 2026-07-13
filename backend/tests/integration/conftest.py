"""Shared fixtures for integration tests: an isolated `energia_test` Postgres database.

Integration tests must NEVER touch the real `energia` database — that is the running dev
database and its data must stay untouched. This module provisions a dedicated `energia_test`
database on the *same* Postgres server the local docker-compose stack uses (or, in CI, the
same Postgres service container): it (re)creates that database once per test session and
replays the exact DDL used for `energia` (`docker/postgres/init/*.sql`, in lexical order — 01
schema, 02 indexes, 03 staging schema, 04 seed data) so `energia_test`'s structure never
silently drifts from what actually ships. Nothing is skipped: even though 03_staging.sql only
declares an empty `staging` schema with no tables today, replaying it keeps this fixture a
faithful mirror of production's init sequence, so a future script that *does* depend on that
schema existing won't break this fixture by surprise.

Connection settings are entirely env-driven via `energia.shared.config.Settings`
(POSTGRES_HOST, POSTGRES_HOST_PORT, POSTGRES_USER, POSTGRES_PASSWORD) — only the database name
changes. That means the exact same suite works unmodified in CI, as long as the CI Postgres
service exposes those same env vars (or their defaults already match, as they do for the local
docker-compose database).

`_test_database` / `test_engine` / `test_session_factory` below are the reusable building
blocks future integration tests are expected to reuse — and are intentionally NOT autouse, so
tests that don't touch the database (e.g. the existing health integration test, which reads
the real `energia` instance) never trigger them. Context-specific integration tests compose
their own app/client/cleanup fixtures on top of `test_session_factory` — see
`tests/integration/contexts/clientes/conftest.py` for the first example.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from energia.shared.config import Settings

_TEST_DB_NAME = "energia_test"
_MAINTENANCE_DB_NAME = "postgres"
# This file lives at backend/tests/integration/conftest.py; repo root is 3 parents up.
_INIT_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "docker" / "postgres" / "init"


def _connection_kwargs(settings: Settings, database: str) -> dict[str, object]:
    return {
        "host": settings.postgres_host,
        "port": settings.postgres_host_port,
        "user": settings.postgres_user,
        "password": settings.postgres_password.get_secret_value(),
        "database": database,
    }


async def _recreate_test_database(settings: Settings) -> None:
    """Drop (if present) and recreate `energia_test`, connected via the maintenance database.

    `DROP`/`CREATE DATABASE` cannot run inside a transaction block. A fresh asyncpg connection
    with no explicit transaction runs each `execute()` outside of one, so no autocommit
    handling is needed here. `WITH (FORCE)` (Postgres 13+) drops the database even if a
    previous, crashed test run left connections open against it.
    """
    conn = await asyncpg.connect(**_connection_kwargs(settings, _MAINTENANCE_DB_NAME))
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{_TEST_DB_NAME}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{_TEST_DB_NAME}"')
    finally:
        await conn.close()


async def _apply_init_scripts(settings: Settings) -> None:
    """Replay docker/postgres/init/*.sql against `energia_test`, in lexical (01..04) order.

    Uses asyncpg directly instead of SQLAlchemy: these scripts are multi-statement SQL,
    including a PL/pgSQL function body with embedded semicolons (`fn_set_updated_at`).
    asyncpg's simple query protocol (used by `execute()` when called with no parameters) runs
    a whole script exactly like `psql -f` would, with no need to split it into statements.
    """
    conn = await asyncpg.connect(**_connection_kwargs(settings, _TEST_DB_NAME))
    try:
        for script in sorted(_INIT_SCRIPTS_DIR.glob("*.sql")):
            await conn.execute(script.read_text())
    finally:
        await conn.close()


@pytest.fixture(scope="session")
async def _test_database() -> None:
    """(Re)create `energia_test` and apply the full DDL once per test session."""
    await _recreate_test_database(Settings(postgres_db=_MAINTENANCE_DB_NAME))
    await _apply_init_scripts(Settings(postgres_db=_TEST_DB_NAME))


@pytest.fixture
async def test_engine(_test_database: None) -> AsyncIterator[AsyncEngine]:
    """Async engine bound to `energia_test`, rebuilt fresh for every test function.

    Function-scoped on purpose, even though `_test_database` (the expensive part — dropping,
    recreating, replaying DDL) is session-scoped: an asyncpg connection is bound to the event
    loop it was created on, and pytest-asyncio gives each test function its own event loop by
    default. A session-scoped engine would be built on the first test's loop and then reused
    from later tests running on a *different, closed* loop, surfacing as opaque asyncpg
    errors ("another operation is in progress") — the same class of bug documented in
    `energia.shared.db`'s docstrings for the production engine/lifespan wiring.
    """
    settings = Settings(postgres_db=_TEST_DB_NAME)
    engine = create_async_engine(settings.sqlalchemy_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
def test_session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory over `energia_test`, for tests/fixtures to build sessions from."""
    return async_sessionmaker(test_engine, expire_on_commit=False)


# Tables seeded once per test session by `docker/postgres/init/04_seed.sql` (replayed by
# `_apply_init_scripts` above, not per test): reference/catalog data, not per-test application
# data. `truncate_all_tables` must never wipe these -- otherwise the first test in the session
# that triggers a truncate (any test using `db_session` or a context's HTTP client fixture)
# silently deletes this seed data for the rest of the session, since `_test_database` only
# (re)applies the init scripts once, session-scoped. Grow this set if a future seed script adds
# another catalog table.
#
# WARNING: being excluded from truncation cuts both ways -- these rows are shared, mutable state
# across every test in the session, not just protected from deletion. A test that *mutates* a
# row in one of these tables (e.g. soft-deleting a `categoria_tarifaria` or otherwise updating
# one in place) leaks that change into every other test that runs afterward in the same session,
# since nothing here ever resets them back to their seeded values. Any test that needs to mutate
# a seeded reference row must restore it (in the test itself or a fixture teardown) before it
# finishes -- or, better, must not be written that way at all; assert against a copy/fake instead
# of mutating the shared seed.
_SEEDED_REFERENCE_TABLES = {"categorias_tarifarias"}


async def truncate_all_tables(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Truncate every table in the `public` schema of `energia_test`, except
    `_SEEDED_REFERENCE_TABLES`.

    Generic on purpose (queries `pg_tables` instead of a hardcoded table list) so every future
    context's integration tests get cleanup between tests for free, without editing this file
    again. Exposed as a plain function (not a fixture) so each context wires it into its own
    cleanup fixture at whatever point makes sense (see `tests/integration/contexts/clientes/
    conftest.py`).
    """
    async with session_factory() as session:
        result = await session.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        tables = [row[0] for row in result if row[0] not in _SEEDED_REFERENCE_TABLES]
        if not tables:
            return
        quoted_tables = ", ".join(f'"{name}"' for name in tables)
        await session.execute(text(f"TRUNCATE TABLE {quoted_tables} RESTART IDENTITY CASCADE"))
        await session.commit()
