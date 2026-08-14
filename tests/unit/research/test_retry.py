from __future__ import annotations

from typing import Any

from surplus_ai.research.models import ProviderOutcome, ProviderOutcomeStatus
from surplus_ai.research.persistence import parse_outcome_dict
from surplus_ai.research.policy import RetryPolicy
from surplus_ai.research.retry import is_retryable_outcome, run_with_retry
from tests.unit.research.conftest import FakeClock


def test_timeout_is_retryable(outcome_fixtures: dict[str, Any]) -> None:
    assert is_retryable_outcome(parse_outcome_dict(outcome_fixtures["timeout"])) is True


def test_provider_rate_limited_is_retryable(outcome_fixtures: dict[str, Any]) -> None:
    assert is_retryable_outcome(parse_outcome_dict(outcome_fixtures["rate_limited"])) is True


def test_generic_error_is_not_retryable(outcome_fixtures: dict[str, Any]) -> None:
    assert is_retryable_outcome(parse_outcome_dict(outcome_fixtures["error"])) is False


def test_error_retryable_true_is_retryable(outcome_fixtures: dict[str, Any]) -> None:
    assert is_retryable_outcome(parse_outcome_dict(outcome_fixtures["error_retryable"])) is True


def test_success_not_found_skipped_not_retryable(outcome_fixtures: dict[str, Any]) -> None:
    assert is_retryable_outcome(parse_outcome_dict(outcome_fixtures["success"])) is False
    assert is_retryable_outcome(parse_outcome_dict(outcome_fixtures["not_found"])) is False
    assert is_retryable_outcome(parse_outcome_dict(outcome_fixtures["skipped"])) is False


def test_run_with_retry_timeout_retries(outcome_fixtures: dict[str, Any]) -> None:
    outcomes = [
        parse_outcome_dict(outcome_fixtures["timeout"]),
        parse_outcome_dict(outcome_fixtures["timeout"]),
        parse_outcome_dict(outcome_fixtures["not_found"]),
    ]
    calls = {"n": 0}

    def lookup() -> ProviderOutcome:
        result = outcomes[calls["n"]]
        calls["n"] += 1
        return result

    clock = FakeClock()
    final = run_with_retry(
        lookup,
        RetryPolicy(max_attempts=3, backoff_seconds=0.5),
        sleeper=clock.sleep,
    )
    assert calls["n"] == 3
    assert final.status is ProviderOutcomeStatus.NOT_FOUND
    assert clock.sleeps == [0.5, 0.5]


def test_run_with_retry_rate_limited_retries(outcome_fixtures: dict[str, Any]) -> None:
    outcomes = [
        parse_outcome_dict(outcome_fixtures["rate_limited"]),
        parse_outcome_dict(outcome_fixtures["success"]),
    ]
    calls = {"n": 0}

    def lookup() -> ProviderOutcome:
        result = outcomes[calls["n"]]
        calls["n"] += 1
        return result

    clock = FakeClock()
    final = run_with_retry(
        lookup,
        RetryPolicy(max_attempts=3, backoff_seconds=0.5),
        sleeper=clock.sleep,
    )
    assert calls["n"] == 2
    assert final.status is ProviderOutcomeStatus.SUCCESS
    assert clock.sleeps == [0.5]


def test_run_with_retry_generic_error_no_retry(outcome_fixtures: dict[str, Any]) -> None:
    calls = {"n": 0}

    def lookup() -> ProviderOutcome:
        calls["n"] += 1
        return parse_outcome_dict(outcome_fixtures["error"])

    clock = FakeClock()
    final = run_with_retry(
        lookup,
        RetryPolicy(max_attempts=3, backoff_seconds=0.5),
        sleeper=clock.sleep,
    )
    assert calls["n"] == 1
    assert final.status is ProviderOutcomeStatus.ERROR
    assert clock.sleeps == []


def test_run_with_retry_error_retryable_retries(outcome_fixtures: dict[str, Any]) -> None:
    outcomes = [
        parse_outcome_dict(outcome_fixtures["error_retryable"]),
        parse_outcome_dict(outcome_fixtures["not_found"]),
    ]
    calls = {"n": 0}

    def lookup() -> ProviderOutcome:
        result = outcomes[calls["n"]]
        calls["n"] += 1
        return result

    clock = FakeClock()
    final = run_with_retry(
        lookup,
        RetryPolicy(max_attempts=3, backoff_seconds=0.5),
        sleeper=clock.sleep,
    )
    assert calls["n"] == 2
    assert final.status is ProviderOutcomeStatus.NOT_FOUND
    assert clock.sleeps == [0.5]


def test_max_attempts_includes_initial(outcome_fixtures: dict[str, Any]) -> None:
    calls = {"n": 0}

    def lookup() -> ProviderOutcome:
        calls["n"] += 1
        return parse_outcome_dict(outcome_fixtures["timeout"])

    clock = FakeClock()
    final = run_with_retry(
        lookup,
        RetryPolicy(max_attempts=1, backoff_seconds=0.5),
        sleeper=clock.sleep,
    )
    assert calls["n"] == 1
    assert final.status is ProviderOutcomeStatus.TIMEOUT
    assert clock.sleeps == []


def test_credentials_and_null_shapes_not_retryable() -> None:
    creds = ProviderOutcome(
        status=ProviderOutcomeStatus.ERROR,
        found=False,
        error_code="credentials_missing",
        requires_human_review=True,
        cacheable=False,
    )
    skipped = ProviderOutcome(
        status=ProviderOutcomeStatus.SKIPPED,
        found=False,
        error_code="provider_not_configured",
        requires_human_review=True,
        cacheable=False,
    )
    assert is_retryable_outcome(creds) is False
    assert is_retryable_outcome(skipped) is False
