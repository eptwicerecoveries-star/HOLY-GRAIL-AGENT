"""Shared TestClient fixtures for the local API and dashboard."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from surplus_ai.api.app import create_app
from surplus_ai.api.dependencies import get_db_session, get_writable_db_session
from surplus_ai.api.origin import ALLOWED_AUTH_ORIGIN
from surplus_ai.auth.passwords import hash_password
from surplus_ai.database.models.enums import UserRole
from surplus_ai.database.models.user import User

TEST_PASSWORD = "local-test-password!"
AUTH_ORIGIN_HEADERS = {"Origin": ALLOWED_AUTH_ORIGIN}


def _build_client(session: Session) -> tuple[object, TestClient]:
    application = create_app()

    def _override() -> Iterator[Session]:
        yield session

    application.dependency_overrides[get_db_session] = _override
    application.dependency_overrides[get_writable_db_session] = _override
    client = TestClient(application)
    client.__enter__()
    return application, client


@pytest.fixture
def password_user(session: Session) -> User:
    user = User(
        name="API Operator",
        email="operator@example.invalid",
        role=UserRole.AGENT,
        is_active=True,
        password_hash=hash_password(TEST_PASSWORD),
    )
    session.add(user)
    session.flush()
    return user


@pytest.fixture
def anonymous_client(session: Session) -> Iterator[TestClient]:
    application, client = _build_client(session)
    try:
        yield client
    finally:
        client.__exit__(None, None, None)
        application.dependency_overrides.clear()


@pytest.fixture
def api_client(session: Session, password_user: User) -> Iterator[TestClient]:
    """Authenticated TestClient (default for protected business API tests)."""
    application, client = _build_client(session)
    try:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": password_user.email, "password": TEST_PASSWORD},
            headers=AUTH_ORIGIN_HEADERS,
        )
        assert response.status_code == 200, response.text
        yield client
    finally:
        client.__exit__(None, None, None)
        application.dependency_overrides.clear()
