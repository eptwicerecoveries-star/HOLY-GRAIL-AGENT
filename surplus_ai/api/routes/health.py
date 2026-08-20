"""Process liveness (/health) and database readiness (/ready)."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from surplus_ai.api.dependencies import get_db_session
from surplus_ai.api.schemas import HealthResponse, ReadyResponse

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness only — does not query PostgreSQL."""
    return HealthResponse(status="ok")


@router.get("/ready", response_model=None)
def ready(session: Session = Depends(get_db_session)) -> ReadyResponse | JSONResponse:
    """Unauthenticated DB connectivity check. Not a migration/provider probe."""
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        logger.info("readiness_failed")
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return ReadyResponse(status="ready")
