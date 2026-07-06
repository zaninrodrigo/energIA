"""Unit tests for the uvicorn entrypoint module."""

from fastapi import FastAPI

from energia.main import app


def test_main_exposes_a_ready_to_serve_fastapi_app() -> None:
    assert isinstance(app, FastAPI)
    assert "/health" in app.openapi()["paths"]
