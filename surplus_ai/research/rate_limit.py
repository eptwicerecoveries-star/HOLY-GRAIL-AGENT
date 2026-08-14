"""Process-local token-bucket pacing. Never records research evidence."""

from __future__ import annotations

from surplus_ai.research.clock import MonotonicFn, SleeperFn, real_monotonic, real_sleep


class TokenBucketRateLimiter:
    """Capacity-1 bucket. ``per_second == 0`` means unlimited (no wait).

    Local pacing never fabricates RATE_LIMITED / ERROR / SUCCESS outcomes.
    The limiter resets when the process exits; it is not a global API limiter.
    """

    def __init__(
        self,
        per_second: float,
        *,
        monotonic: MonotonicFn | None = None,
        sleeper: SleeperFn | None = None,
    ) -> None:
        self._per_second = per_second
        self._monotonic = monotonic or real_monotonic
        self._sleeper = sleeper or real_sleep
        self._capacity = 1.0
        self._tokens = 1.0
        self._last = self._monotonic()

    def acquire(self) -> None:
        if self._per_second <= 0:
            return
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return
        wait = (1.0 - self._tokens) / self._per_second
        if wait > 0:
            self._sleeper(wait)
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0

    def _refill(self) -> None:
        now = self._monotonic()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._per_second)
