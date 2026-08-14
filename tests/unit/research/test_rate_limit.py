from __future__ import annotations

from surplus_ai.research.rate_limit import TokenBucketRateLimiter
from tests.unit.research.conftest import FakeClock


def test_per_second_zero_never_waits() -> None:
    clock = FakeClock()
    limiter = TokenBucketRateLimiter(0, monotonic=clock.monotonic, sleeper=clock.sleep)
    limiter.acquire()
    limiter.acquire()
    assert clock.sleeps == []


def test_first_acquire_does_not_wait() -> None:
    clock = FakeClock()
    limiter = TokenBucketRateLimiter(10, monotonic=clock.monotonic, sleeper=clock.sleep)
    limiter.acquire()
    assert clock.sleeps == []


def test_second_acquire_waits_for_token_with_fake_clock() -> None:
    clock = FakeClock()
    limiter = TokenBucketRateLimiter(10, monotonic=clock.monotonic, sleeper=clock.sleep)
    limiter.acquire()
    limiter.acquire()
    assert clock.sleeps == [0.1]
    assert clock.monotonic() == 1000.1


def test_limiter_acquire_returns_none_not_an_outcome() -> None:
    clock = FakeClock()
    limiter = TokenBucketRateLimiter(5, monotonic=clock.monotonic, sleeper=clock.sleep)
    assert limiter.acquire() is None
    assert limiter.acquire() is None
