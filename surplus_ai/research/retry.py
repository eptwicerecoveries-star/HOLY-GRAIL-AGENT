"""Bounded retries for explicitly transient provider outcomes."""

from __future__ import annotations

from collections.abc import Callable

from surplus_ai.research.clock import SleeperFn, real_sleep
from surplus_ai.research.models import ProviderOutcome, ProviderOutcomeStatus
from surplus_ai.research.policy import RetryPolicy

LookupFn = Callable[[], ProviderOutcome]


def is_retryable_outcome(outcome: ProviderOutcome) -> bool:
    """True only for TIMEOUT, provider RATE_LIMITED, or ERROR with retryable=True."""
    if outcome.status in (
        ProviderOutcomeStatus.TIMEOUT,
        ProviderOutcomeStatus.RATE_LIMITED,
    ):
        return True
    return (
        outcome.status is ProviderOutcomeStatus.ERROR and outcome.retryable is True
    )


def run_with_retry(
    lookup: LookupFn,
    policy: RetryPolicy,
    *,
    before_attempt: Callable[[], None] | None = None,
    sleeper: SleeperFn | None = None,
) -> ProviderOutcome:
    """Run *lookup* up to ``policy.max_attempts`` times (includes the first try)."""
    sleep = sleeper or real_sleep
    last: ProviderOutcome | None = None
    for attempt in range(policy.max_attempts):
        if before_attempt is not None:
            before_attempt()
        last = lookup()
        if not is_retryable_outcome(last) or attempt + 1 >= policy.max_attempts:
            return last
        sleep(policy.backoff_seconds)
    assert last is not None
    return last
