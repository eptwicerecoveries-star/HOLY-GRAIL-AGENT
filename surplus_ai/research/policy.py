"""Nested reliability policy for Phase 6B (cache, retry, in-process rate limit)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Conservative defaults when the YAML `reliability` block is omitted.
DEFAULT_CACHE_TTL_SECONDS = 0
DEFAULT_MAX_ATTEMPTS = 1
DEFAULT_BACKOFF_SECONDS = 0.0
DEFAULT_RATE_LIMIT_PER_SECOND = 0.0


def _parse_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _parse_number(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number")
    if isinstance(value, int | float):
        return float(value)
    raise ValueError(f"{field} must be a number")


class CachePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ttl_seconds: int = Field(default=DEFAULT_CACHE_TTL_SECONDS, ge=0)

    @field_validator("ttl_seconds", mode="before")
    @classmethod
    def _ttl_type(cls, value: object) -> int:
        return _parse_int(value, "ttl_seconds")


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_attempts: int = Field(default=DEFAULT_MAX_ATTEMPTS, ge=1)
    backoff_seconds: float = Field(default=DEFAULT_BACKOFF_SECONDS, ge=0)

    @field_validator("max_attempts", mode="before")
    @classmethod
    def _attempts_type(cls, value: object) -> int:
        return _parse_int(value, "max_attempts")

    @field_validator("backoff_seconds", mode="before")
    @classmethod
    def _backoff_type(cls, value: object) -> float:
        return _parse_number(value, "backoff_seconds")


class RateLimitPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    per_second: float = Field(default=DEFAULT_RATE_LIMIT_PER_SECOND, ge=0)

    @field_validator("per_second", mode="before")
    @classmethod
    def _per_second_type(cls, value: object) -> float:
        return _parse_number(value, "per_second")


class ReliabilityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cache: CachePolicy = Field(default_factory=CachePolicy)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    rate_limit: RateLimitPolicy = Field(default_factory=RateLimitPolicy)


class CachePolicyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ttl_seconds: int | None = Field(default=None, ge=0)

    @field_validator("ttl_seconds", mode="before")
    @classmethod
    def _ttl_type(cls, value: object) -> object:
        if value is None:
            return None
        return _parse_int(value, "ttl_seconds")


class RetryPolicyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_attempts: int | None = Field(default=None, ge=1)
    backoff_seconds: float | None = Field(default=None, ge=0)

    @field_validator("max_attempts", mode="before")
    @classmethod
    def _attempts_type(cls, value: object) -> object:
        if value is None:
            return None
        return _parse_int(value, "max_attempts")

    @field_validator("backoff_seconds", mode="before")
    @classmethod
    def _backoff_type(cls, value: object) -> object:
        if value is None:
            return None
        return _parse_number(value, "backoff_seconds")


class RateLimitPolicyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    per_second: float | None = Field(default=None, ge=0)

    @field_validator("per_second", mode="before")
    @classmethod
    def _per_second_type(cls, value: object) -> object:
        if value is None:
            return None
        return _parse_number(value, "per_second")


class ReliabilityPolicyPatch(BaseModel):
    """Partial nested override; omitted fields inherit the global policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cache: CachePolicyPatch | None = None
    retry: RetryPolicyPatch | None = None
    rate_limit: RateLimitPolicyPatch | None = None


def merge_reliability(
    base: ReliabilityPolicy,
    override: ReliabilityPolicyPatch | None,
) -> ReliabilityPolicy:
    """Merge a partial provider override onto the global reliability policy."""
    if override is None:
        return base

    cache_ttl = base.cache.ttl_seconds
    if override.cache is not None and override.cache.ttl_seconds is not None:
        cache_ttl = override.cache.ttl_seconds

    max_attempts = base.retry.max_attempts
    backoff = base.retry.backoff_seconds
    if override.retry is not None:
        if override.retry.max_attempts is not None:
            max_attempts = override.retry.max_attempts
        if override.retry.backoff_seconds is not None:
            backoff = override.retry.backoff_seconds

    per_second = base.rate_limit.per_second
    if override.rate_limit is not None and override.rate_limit.per_second is not None:
        per_second = override.rate_limit.per_second

    return ReliabilityPolicy(
        cache=CachePolicy(ttl_seconds=cache_ttl),
        retry=RetryPolicy(max_attempts=max_attempts, backoff_seconds=backoff),
        rate_limit=RateLimitPolicy(per_second=per_second),
    )
