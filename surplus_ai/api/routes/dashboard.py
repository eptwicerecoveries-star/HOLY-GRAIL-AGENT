"""Serve the local read-only operator dashboard at GET /."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from surplus_ai.api.static import DASHBOARD_HTML, DASHBOARD_SECURITY_HEADERS

router = APIRouter(tags=["dashboard"])


@router.get("/", include_in_schema=False)
def dashboard_home() -> FileResponse:
    """Return the dashboard HTML only — never a user-controlled filesystem path."""
    return FileResponse(
        DASHBOARD_HTML,
        media_type="text/html; charset=utf-8",
        headers=DASHBOARD_SECURITY_HEADERS,
    )
