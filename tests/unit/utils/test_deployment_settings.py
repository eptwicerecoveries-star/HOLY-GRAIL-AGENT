"""P4-B1 origin/host Settings contract. Fake example.invalid values only."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from surplus_ai.utils.config import ConfigurationError, Settings, get_settings

VALID_URL = "postgresql+psycopg://u:p@localhost:5432/db"
PROD_ORIGIN = "https://app.example.invalid"
PROD_HOST = "app.example.invalid"


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    monkeypatch.setenv("SURPLUS_AI_DATABASE_URL", VALID_URL)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_dev_defaults_are_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SURPLUS_AI_AUTH_ORIGINS", raising=False)
    monkeypatch.delenv("SURPLUS_AI_ALLOWED_HOSTS", raising=False)
    settings = _settings(monkeypatch, SURPLUS_AI_ENV="dev")
    assert settings.auth_origin_allowlist == ("http://127.0.0.1:8000",)
    assert settings.allowed_host_allowlist == ("127.0.0.1",)


def test_test_defaults_include_testserver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SURPLUS_AI_AUTH_ORIGINS", raising=False)
    monkeypatch.delenv("SURPLUS_AI_ALLOWED_HOSTS", raising=False)
    settings = _settings(monkeypatch, SURPLUS_AI_ENV="test")
    assert settings.auth_origin_allowlist == ("http://127.0.0.1:8000",)
    assert settings.allowed_host_allowlist == ("127.0.0.1", "testserver")


def test_comma_separated_origins_and_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch,
        SURPLUS_AI_ENV="dev",
        SURPLUS_AI_AUTH_ORIGINS=" https://app.example.invalid , https://admin.example.invalid ",
    )
    assert settings.auth_origin_allowlist == (
        "https://app.example.invalid",
        "https://admin.example.invalid",
    )


def test_duplicate_origins_keep_first(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch,
        SURPLUS_AI_ENV="dev",
        SURPLUS_AI_AUTH_ORIGINS="https://app.example.invalid,https://app.example.invalid",
    )
    assert settings.auth_origin_allowlist == ("https://app.example.invalid",)


def test_empty_origin_token_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        _settings(
            monkeypatch,
            SURPLUS_AI_ENV="dev",
            SURPLUS_AI_AUTH_ORIGINS="https://app.example.invalid,",
        )


@pytest.mark.parametrize(
    "origin",
    [
        "https://app.example.invalid/",
        "https://app.example.invalid/login",
        "https://app.example.invalid?x=1",
        "https://app.example.invalid#frag",
        "https://user@app.example.invalid",
        "https://App.example.invalid",
        "https://app.example.invalid:443",
        "not-a-url",
    ],
)
def test_non_canonical_origins_rejected(monkeypatch: pytest.MonkeyPatch, origin: str) -> None:
    with pytest.raises(ValidationError):
        _settings(monkeypatch, SURPLUS_AI_ENV="dev", SURPLUS_AI_AUTH_ORIGINS=origin)


def test_prod_https_origin_and_host_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch,
        SURPLUS_AI_ENV="prod",
        SURPLUS_AI_AUTH_ORIGINS=PROD_ORIGIN,
        SURPLUS_AI_ALLOWED_HOSTS=PROD_HOST,
    )
    assert settings.auth_origin_allowlist == (PROD_ORIGIN,)
    assert settings.allowed_host_allowlist == (PROD_HOST,)


def test_prod_https_origin_with_non_default_port(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch,
        SURPLUS_AI_ENV="prod",
        SURPLUS_AI_AUTH_ORIGINS="https://app.example.invalid:8443",
        SURPLUS_AI_ALLOWED_HOSTS=PROD_HOST,
    )
    assert settings.auth_origin_allowlist == ("https://app.example.invalid:8443",)


def test_prod_http_origin_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        _settings(
            monkeypatch,
            SURPLUS_AI_ENV="prod",
            SURPLUS_AI_AUTH_ORIGINS="http://app.example.invalid",
            SURPLUS_AI_ALLOWED_HOSTS=PROD_HOST,
        )


@pytest.mark.parametrize("missing", ["SURPLUS_AI_AUTH_ORIGINS", "SURPLUS_AI_ALLOWED_HOSTS"])
def test_prod_missing_required_lists_fail_closed(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.setenv("SURPLUS_AI_DATABASE_URL", VALID_URL)
    monkeypatch.setenv("SURPLUS_AI_ENV", "prod")
    monkeypatch.setenv("SURPLUS_AI_AUTH_ORIGINS", PROD_ORIGIN)
    monkeypatch.setenv("SURPLUS_AI_ALLOWED_HOSTS", PROD_HOST)
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("SURPLUS_AI_AUTH_ORIGINS", ""),
        ("SURPLUS_AI_AUTH_ORIGINS", "   "),
        ("SURPLUS_AI_ALLOWED_HOSTS", ""),
        ("SURPLUS_AI_ALLOWED_HOSTS", "   "),
    ],
)
def test_prod_blank_required_lists_fail_closed(
    monkeypatch: pytest.MonkeyPatch, key: str, value: str
) -> None:
    monkeypatch.setenv("SURPLUS_AI_DATABASE_URL", VALID_URL)
    monkeypatch.setenv("SURPLUS_AI_ENV", "prod")
    monkeypatch.setenv("SURPLUS_AI_AUTH_ORIGINS", PROD_ORIGIN)
    monkeypatch.setenv("SURPLUS_AI_ALLOWED_HOSTS", PROD_HOST)
    monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_prod_wildcard_host_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        _settings(
            monkeypatch,
            SURPLUS_AI_ENV="prod",
            SURPLUS_AI_AUTH_ORIGINS=PROD_ORIGIN,
            SURPLUS_AI_ALLOWED_HOSTS="*",
        )


def test_prod_wildcard_suffix_host_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        _settings(
            monkeypatch,
            SURPLUS_AI_ENV="prod",
            SURPLUS_AI_AUTH_ORIGINS=PROD_ORIGIN,
            SURPLUS_AI_ALLOWED_HOSTS="*.example.invalid",
        )


def test_host_with_scheme_or_port_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        _settings(
            monkeypatch,
            SURPLUS_AI_ENV="dev",
            SURPLUS_AI_ALLOWED_HOSTS="https://app.example.invalid",
        )
    with pytest.raises(ValidationError):
        _settings(
            monkeypatch,
            SURPLUS_AI_ENV="dev",
            SURPLUS_AI_ALLOWED_HOSTS="app.example.invalid:443",
        )


def test_uppercase_host_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        _settings(
            monkeypatch,
            SURPLUS_AI_ENV="dev",
            SURPLUS_AI_ALLOWED_HOSTS="App.example.invalid",
        )


def test_get_settings_prod_missing_origins_is_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SURPLUS_AI_DATABASE_URL", VALID_URL)
    monkeypatch.setenv("SURPLUS_AI_ENV", "prod")
    monkeypatch.setenv("SURPLUS_AI_ALLOWED_HOSTS", PROD_HOST)
    monkeypatch.delenv("SURPLUS_AI_AUTH_ORIGINS", raising=False)
    with pytest.raises(ConfigurationError):
        get_settings()
