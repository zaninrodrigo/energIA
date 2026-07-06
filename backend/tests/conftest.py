"""Shared pytest fixtures for unit and integration tests."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI

from energia.api.app import create_app


@pytest.fixture
def app() -> Iterator[FastAPI]:
    """A fresh FastAPI application instance per test; dependency overrides reset afterwards."""
    application = create_app()
    yield application
    application.dependency_overrides.clear()
