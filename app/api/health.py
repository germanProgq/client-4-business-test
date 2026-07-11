import logging

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session

logger = logging.getLogger("approval_service.health")

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness() -> dict:
    """Liveness probe: process is up. Deliberately does not touch the
    database -- a slow/unavailable DB should surface via /ready, not make
    the orchestrator kill and restart a perfectly healthy process."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness(response: Response, session: AsyncSession = Depends(get_db_session)) -> dict:
    """Readiness probe: can this instance actually serve traffic."""
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("readiness check failed")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable"}
    return {"status": "ready"}
