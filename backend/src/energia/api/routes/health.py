"""Liveness/readiness endpoint: reports API status and database reachability."""

import asyncio

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from energia.shared.db import get_db_session

router = APIRouter()

_DB_PROBE_TIMEOUT_SECONDS = 2.0


@router.get("/health")
async def health(
    response: Response, session: AsyncSession = Depends(get_db_session)
) -> dict[str, str]:
    """Return API status plus a best-effort database reachability check (SELECT 1).

    The probe is bounded to `_DB_PROBE_TIMEOUT_SECONDS` so a black-holed network cannot hang
    the request. When the database is unreachable or the probe times out, the response is
    HTTP 503 so infra tooling that keys off status codes (load balancers, orchestrators) can
    detect the degraded state.
    """
    try:
        await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=_DB_PROBE_TIMEOUT_SECONDS)
    except Exception:  # any failure or timeout here means the database is unreachable
        response.status_code = 503
        return {"status": "degraded", "database": "down"}
    return {"status": "ok", "database": "up"}
