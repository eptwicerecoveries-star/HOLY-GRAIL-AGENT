"""Shared TestClient fixture for the local API and dashboard."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from surplus_ai.api.app import create_app
from surplus_ai.api.dependencies import get_db_session


@pytest.fixture
def api_client(session: Session) -> Iterator[TestClient]:
    application = create_app()

    def _override() -> Iterator[Session]:
        yield session

    application.dependency_overrides[get_db_session] = _override
    with TestClient(application) as client:
        yield client
    application.dependency_overrides.clear()
