"""Login throttle buckets (P4-B2-A). Deterministic digests; not HTTP-bound.

Digests are storage minimization / pseudonymous operational identifiers.
They are not anonymization: IP digests remain dictionary-testable.
"""

from __future__ import annotations

import hashlib
import ipaddress
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from sqlalchemy import ColumnElement, and_, case, literal, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from surplus_ai.database.models.login_throttle_bucket import LoginThrottleBucket

UNKNOWN_CLIENT = "unknown-client"

IP_ATTEMPT_THRESHOLD = 20
IP_WINDOW = timedelta(minutes=15)
IP_BLOCK_DURATION = timedelta(minutes=15)

CREDENTIAL_FAILURE_THRESHOLD = 5
CREDENTIAL_WINDOW = timedelta(minutes=15)
CREDENTIAL_BLOCK_DURATION = timedelta(minutes=15)

WallClockFn = Callable[[], datetime]


class LoginThrottleScope(str, Enum):
    IP = "ip"
    CREDENTIAL = "credential"


@dataclass(frozen=True)
class ThrottleDecision:
    blocked: bool
    retry_after_seconds: int | None


def _now(wall_clock: WallClockFn | None) -> datetime:
    current = wall_clock() if wall_clock is not None else datetime.now(tz=UTC)
    if current.tzinfo is None:
        raise ValueError("wall_clock must be timezone-aware UTC")
    return current


def retry_after_seconds(*, blocked_until: datetime, now: datetime) -> int:
    """Seconds until block expiry; never zero while actively blocked."""
    return max(1, math.ceil((blocked_until - now).total_seconds()))


def canonical_client_ip(host: str | None) -> str:
    """Canonical peer IP from request.client.host only. No forwarded headers."""
    if host is None:
        return UNKNOWN_CLIENT
    stripped = host.strip()
    if stripped == "":
        return UNKNOWN_CLIENT
    try:
        parsed = ipaddress.ip_address(stripped)
    except ValueError:
        return UNKNOWN_CLIENT
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        return str(parsed.ipv4_mapped)
    return str(parsed)


def digest_ip_key(canonical_ip: str) -> str:
    return hashlib.sha256(canonical_ip.encode("utf-8")).hexdigest()


def digest_credential_key(canonical_ip: str, submitted_email: str) -> str:
    payload = canonical_ip + "\0" + submitted_email
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _window_expired(
    *,
    window_started_at: Any,
    blocked_until: Any,
    now: datetime,
    window: timedelta,
) -> ColumnElement[bool]:
    return or_(
        window_started_at <= now - window,
        and_(blocked_until.isnot(None), blocked_until <= now),
    )


def _not_actively_blocked(now: datetime) -> ColumnElement[bool]:
    return or_(
        LoginThrottleBucket.blocked_until.is_(None),
        LoginThrottleBucket.blocked_until <= now,
    )


def _blocked_decision(
    session: Session,
    *,
    scope: LoginThrottleScope,
    key_digest: str,
    now: datetime,
) -> ThrottleDecision:
    blocked_until = session.scalar(
        select(LoginThrottleBucket.blocked_until).where(
            LoginThrottleBucket.scope == scope.value,
            LoginThrottleBucket.key_digest == key_digest,
            LoginThrottleBucket.blocked_until.isnot(None),
            LoginThrottleBucket.blocked_until > now,
        )
    )
    if blocked_until is None:
        return ThrottleDecision(blocked=True, retry_after_seconds=1)
    return ThrottleDecision(
        blocked=True,
        retry_after_seconds=retry_after_seconds(blocked_until=blocked_until, now=now),
    )


def consume_ip_login_attempt(
    session: Session,
    *,
    canonical_ip: str,
    wall_clock: WallClockFn | None = None,
) -> ThrottleDecision:
    """Atomically consume one IP login-attempt slot. Caller must commit before Argon2."""
    now = _now(wall_clock)
    key_digest = digest_ip_key(canonical_ip)
    expired = _window_expired(
        window_started_at=LoginThrottleBucket.window_started_at,
        blocked_until=LoginThrottleBucket.blocked_until,
        now=now,
        window=IP_WINDOW,
    )
    next_count = case((expired, literal(1)), else_=LoginThrottleBucket.event_count + 1)
    blocked_until_expr = case(
        (expired, literal(None)),
        (
            LoginThrottleBucket.event_count + 1 >= IP_ATTEMPT_THRESHOLD,
            literal(now + IP_BLOCK_DURATION),
        ),
        else_=literal(None),
    )
    stmt = (
        insert(LoginThrottleBucket)
        .values(
            scope=LoginThrottleScope.IP.value,
            key_digest=key_digest,
            window_started_at=now,
            event_count=1,
            blocked_until=None,
        )
        .on_conflict_do_update(
            index_elements=["scope", "key_digest"],
            set_={
                "window_started_at": case(
                    (expired, literal(now)),
                    else_=LoginThrottleBucket.window_started_at,
                ),
                "event_count": next_count,
                "blocked_until": blocked_until_expr,
            },
            where=_not_actively_blocked(now),
        )
        .returning(LoginThrottleBucket.event_count, LoginThrottleBucket.blocked_until)
    )
    row = session.execute(stmt).one_or_none()
    if row is None:
        return _blocked_decision(
            session, scope=LoginThrottleScope.IP, key_digest=key_digest, now=now
        )
    return ThrottleDecision(blocked=False, retry_after_seconds=None)


def precheck_credential_throttle(
    session: Session,
    *,
    canonical_ip: str,
    submitted_email: str,
    wall_clock: WallClockFn | None = None,
) -> ThrottleDecision:
    """Read-only credential-bucket precheck. No writes."""
    now = _now(wall_clock)
    key_digest = digest_credential_key(canonical_ip, submitted_email)
    blocked_until = session.scalar(
        select(LoginThrottleBucket.blocked_until).where(
            LoginThrottleBucket.scope == LoginThrottleScope.CREDENTIAL.value,
            LoginThrottleBucket.key_digest == key_digest,
        )
    )
    if blocked_until is not None and blocked_until > now:
        return ThrottleDecision(
            blocked=True,
            retry_after_seconds=retry_after_seconds(blocked_until=blocked_until, now=now),
        )
    return ThrottleDecision(blocked=False, retry_after_seconds=None)


def record_credential_failure(
    session: Session,
    *,
    canonical_ip: str,
    submitted_email: str,
    wall_clock: WallClockFn | None = None,
) -> None:
    """Atomically record one credential failure. Active blocks are not extended."""
    now = _now(wall_clock)
    key_digest = digest_credential_key(canonical_ip, submitted_email)
    expired = _window_expired(
        window_started_at=LoginThrottleBucket.window_started_at,
        blocked_until=LoginThrottleBucket.blocked_until,
        now=now,
        window=CREDENTIAL_WINDOW,
    )
    blocked_until_expr = case(
        (expired, literal(None)),
        (
            LoginThrottleBucket.event_count + 1 >= CREDENTIAL_FAILURE_THRESHOLD,
            literal(now + CREDENTIAL_BLOCK_DURATION),
        ),
        else_=literal(None),
    )
    stmt = (
        insert(LoginThrottleBucket)
        .values(
            scope=LoginThrottleScope.CREDENTIAL.value,
            key_digest=key_digest,
            window_started_at=now,
            event_count=1,
            blocked_until=None,
        )
        .on_conflict_do_update(
            index_elements=["scope", "key_digest"],
            set_={
                "window_started_at": case(
                    (expired, literal(now)),
                    else_=LoginThrottleBucket.window_started_at,
                ),
                "event_count": case(
                    (expired, literal(1)),
                    else_=LoginThrottleBucket.event_count + 1,
                ),
                "blocked_until": blocked_until_expr,
            },
            where=_not_actively_blocked(now),
        )
    )
    session.execute(stmt)


def clear_credential_bucket_on_success(
    session: Session,
    *,
    canonical_ip: str,
    submitted_email: str,
) -> bool:
    """Delete the credential bucket row for this IP+email. Never touches IP scope."""
    key_digest = digest_credential_key(canonical_ip, submitted_email)
    row = session.scalar(
        select(LoginThrottleBucket).where(
            LoginThrottleBucket.scope == LoginThrottleScope.CREDENTIAL.value,
            LoginThrottleBucket.key_digest == key_digest,
        )
    )
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True
