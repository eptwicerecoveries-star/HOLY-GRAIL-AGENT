"""P4-B1 TrustedHostMiddleware. Uses example.invalid only."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from surplus_ai.api.app import create_app
from surplus_ai.utils.config import ConfigurationError, get_settings

VALID_URL = "postgresql+psycopg://u:p@localhost:5432/db"


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_testclient_default_host_is_accepted() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")
    assert response.status_code == 200


def test_dev_loopback_host_accepted_evil_host_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SURPLUS_AI_ENV", "dev")
    monkeypatch.delenv("SURPLUS_AI_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("SURPLUS_AI_AUTH_ORIGINS", raising=False)
    application = create_app()
    with TestClient(application, base_url="http://127.0.0.1") as client:
        assert client.get("/health").status_code == 200
    with TestClient(application, base_url="http://evil.example") as client:
        response = client.get("/health")
    assert response.status_code == 400
    assert "invalid host" in response.text.lower()


def test_prod_configured_host_accepted_other_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SURPLUS_AI_ENV", "prod")
    monkeypatch.setenv("SURPLUS_AI_DATABASE_URL", VALID_URL)
    monkeypatch.setenv("SURPLUS_AI_AUTH_ORIGINS", "https://app.example.invalid")
    monkeypatch.setenv("SURPLUS_AI_ALLOWED_HOSTS", "app.example.invalid")
    application = create_app()
    with TestClient(application, base_url="http://app.example.invalid") as client:
        assert client.get("/health").status_code == 200
    with TestClient(application, base_url="http://other.example.invalid") as client:
        response = client.get("/health")
    assert response.status_code == 400


def test_prod_missing_hosts_fails_before_serving(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SURPLUS_AI_ENV", "prod")
    monkeypatch.setenv("SURPLUS_AI_DATABASE_URL", VALID_URL)
    monkeypatch.setenv("SURPLUS_AI_AUTH_ORIGINS", "https://app.example.invalid")
    monkeypatch.delenv("SURPLUS_AI_ALLOWED_HOSTS", raising=False)
    with pytest.raises(ConfigurationError):
        create_app()


def test_prod_wildcard_host_fails_before_serving(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SURPLUS_AI_ENV", "prod")
    monkeypatch.setenv("SURPLUS_AI_DATABASE_URL", VALID_URL)
    monkeypatch.setenv("SURPLUS_AI_AUTH_ORIGINS", "https://app.example.invalid")
    monkeypatch.setenv("SURPLUS_AI_ALLOWED_HOSTS", "*")
    with pytest.raises(ConfigurationError):
        create_app()
