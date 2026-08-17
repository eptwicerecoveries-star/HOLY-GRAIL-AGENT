"""Bounded pagination query parameters."""

from __future__ import annotations

from typing import Annotated

from fastapi import Query

DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def pagination_params(
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_LIMIT, description="Page size (1–100)"),
    ] = DEFAULT_LIMIT,
    offset: Annotated[
        int,
        Query(ge=0, description="Row offset"),
    ] = 0,
) -> tuple[int, int]:
    return limit, offset
