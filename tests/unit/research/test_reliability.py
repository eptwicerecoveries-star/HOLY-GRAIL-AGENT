from __future__ import annotations

from pathlib import Path

import pytest

from surplus_ai.research.exceptions import ResearchConfigError
from surplus_ai.research.policy import ReliabilityPolicy
from surplus_ai.research.registry import ProviderRegistry
from tests.unit.research.conftest import (
    SHIPPED_RELIABILITY,
    registry_from_yaml,
    write_providers_yaml,
)


def test_missing_reliability_uses_conservative_defaults(tmp_path: Path) -> None:
    registry = registry_from_yaml(tmp_path)
    policy = registry.global_reliability
    assert policy.cache.ttl_seconds == 0
    assert policy.retry.max_attempts == 1
    assert policy.retry.backoff_seconds == 0.0
    assert policy.rate_limit.per_second == 0.0


def test_shipped_reliability_values() -> None:
    policy = ProviderRegistry().global_reliability
    assert policy.cache.ttl_seconds == 86400
    assert policy.retry.max_attempts == 3
    assert policy.retry.backoff_seconds == 0.5
    assert policy.rate_limit.per_second == 0.0


def test_unknown_reliability_key_rejected(tmp_path: Path) -> None:
    path = write_providers_yaml(
        tmp_path,
        reliability={**SHIPPED_RELIABILITY, "retries": {"max_attempts": 3}},
    )
    with pytest.raises(ResearchConfigError):
        ProviderRegistry(config_path=path, counties_dir=tmp_path / "counties")


def test_unknown_nested_key_rejected(tmp_path: Path) -> None:
    path = write_providers_yaml(
        tmp_path,
        reliability={
            "cache": {"ttl_seconds": 86400, "mode": "aggressive"},
            "retry": {"max_attempts": 3, "backoff_seconds": 0.5},
            "rate_limit": {"per_second": 0},
        },
    )
    with pytest.raises(ResearchConfigError):
        ProviderRegistry(config_path=path, counties_dir=tmp_path / "counties")


def test_negative_ttl_rejected(tmp_path: Path) -> None:
    path = write_providers_yaml(
        tmp_path,
        reliability={
            "cache": {"ttl_seconds": -1},
            "retry": {"max_attempts": 3, "backoff_seconds": 0.5},
            "rate_limit": {"per_second": 0},
        },
    )
    with pytest.raises(ResearchConfigError):
        ProviderRegistry(config_path=path, counties_dir=tmp_path / "counties")


def test_max_attempts_zero_rejected(tmp_path: Path) -> None:
    path = write_providers_yaml(
        tmp_path,
        reliability={
            "cache": {"ttl_seconds": 0},
            "retry": {"max_attempts": 0, "backoff_seconds": 0.5},
            "rate_limit": {"per_second": 0},
        },
    )
    with pytest.raises(ResearchConfigError):
        ProviderRegistry(config_path=path, counties_dir=tmp_path / "counties")


def test_negative_backoff_rejected(tmp_path: Path) -> None:
    path = write_providers_yaml(
        tmp_path,
        reliability={
            "cache": {"ttl_seconds": 0},
            "retry": {"max_attempts": 1, "backoff_seconds": -0.1},
            "rate_limit": {"per_second": 0},
        },
    )
    with pytest.raises(ResearchConfigError):
        ProviderRegistry(config_path=path, counties_dir=tmp_path / "counties")


def test_negative_per_second_rejected(tmp_path: Path) -> None:
    path = write_providers_yaml(
        tmp_path,
        reliability={
            "cache": {"ttl_seconds": 0},
            "retry": {"max_attempts": 1, "backoff_seconds": 0},
            "rate_limit": {"per_second": -1},
        },
    )
    with pytest.raises(ResearchConfigError):
        ProviderRegistry(config_path=path, counties_dir=tmp_path / "counties")


def test_string_ttl_rejected(tmp_path: Path) -> None:
    path = write_providers_yaml(
        tmp_path,
        reliability={
            "cache": {"ttl_seconds": "86400"},
            "retry": {"max_attempts": 1, "backoff_seconds": 0},
            "rate_limit": {"per_second": 0},
        },
    )
    with pytest.raises(ResearchConfigError):
        ProviderRegistry(config_path=path, counties_dir=tmp_path / "counties")


def test_bool_ttl_rejected(tmp_path: Path) -> None:
    path = write_providers_yaml(
        tmp_path,
        reliability={
            "cache": {"ttl_seconds": True},
            "retry": {"max_attempts": 1, "backoff_seconds": 0},
            "rate_limit": {"per_second": 0},
        },
    )
    with pytest.raises(ResearchConfigError):
        ProviderRegistry(config_path=path, counties_dir=tmp_path / "counties")


def test_partial_provider_override_inherits_globals(tmp_path: Path) -> None:
    registry = registry_from_yaml(
        tmp_path,
        reliability=SHIPPED_RELIABILITY,
        providers={
            "manual_lookup": {
                "type": "manual",
                "description": "test",
                "reliability": {"retry": {"max_attempts": 5}},
            },
            "null": {"type": "null", "description": "test"},
        },
    )
    merged = registry.reliability_for("manual_lookup")
    assert merged.cache.ttl_seconds == 86400
    assert merged.retry.max_attempts == 5
    assert merged.retry.backoff_seconds == 0.5
    assert merged.rate_limit.per_second == 0.0
    untouched = registry.reliability_for("null")
    assert untouched.retry.max_attempts == 3
    assert untouched.cache.ttl_seconds == 86400


def test_reliability_policy_defaults_match_conservative() -> None:
    policy = ReliabilityPolicy()
    assert policy.cache.ttl_seconds == 0
    assert policy.retry.max_attempts == 1
    assert policy.retry.backoff_seconds == 0.0
    assert policy.rate_limit.per_second == 0.0
