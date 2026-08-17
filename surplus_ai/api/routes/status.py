"""Safe application/database status for local operators."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from surplus_ai import __version__
from surplus_ai.api.dependencies import get_db_session
from surplus_ai.api.schemas import StatusResponse
from surplus_ai.database.migrator import current_revision, head_revision
from surplus_ai.utils.config import get_settings

router = APIRouter(tags=["status"])


@router.get("/status", response_model=StatusResponse)
def status(session: Session = Depends(get_db_session)) -> StatusResponse:
    settings = get_settings()
    reachable = False
    try:
        session.execute(text("SELECT 1"))
        reachable = True
    except Exception:
        reachable = False

    current: str | None = None
    head: str | None = None
    up_to_date = False
    try:
        current = current_revision()
        head = head_revision()
        up_to_date = bool(current and head and current == head)
    except Exception:
        current = None
        head = None
        up_to_date = False

    return StatusResponse(
        application="surplus-ai",
        version=__version__,
        environment=settings.env,
        database_reachable=reachable,
        alembic_current=current,
        alembic_head=head,
        migrations_up_to_date=up_to_date,
    )
