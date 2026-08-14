"""Injectable time sources so reliability helpers stay deterministic in tests."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime

MonotonicFn = Callable[[], float]
SleeperFn = Callable[[float], None]
WallClockFn = Callable[[], datetime]


def real_monotonic() -> float:
    return time.monotonic()


def real_sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def real_wall_clock() -> datetime:
    return datetime.now(tz=UTC)
