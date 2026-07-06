"""Uvicorn entrypoint. Run with `make run` or `uvicorn energia.main:app`."""

import uvicorn

from energia.api.app import create_app

app = create_app()


def run() -> None:
    """Start the development server with auto-reload."""
    uvicorn.run("energia.main:app", host="0.0.0.0", port=8000, reload=True)  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    run()
