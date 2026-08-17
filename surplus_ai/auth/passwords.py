"""Password hashing and local length policy.

Uses pwdlib.PasswordHash.recommended() (Argon2). No custom salts, parameters,
or hashlib-based password storage. The optional auth extra owns the algorithm.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from surplus_ai.auth.exceptions import AuthDependencyError, PasswordPolicyError

if TYPE_CHECKING:
    from pwdlib import PasswordHash

MIN_PASSWORD_LENGTH = 15
MAX_PASSWORD_LENGTH = 128

_POLICY_MESSAGE = "Password must be between 15 and 128 characters."
_AUTH_EXTRA_MESSAGE = (
    "Password hashing requires the optional auth extra. "
    'Install with: python -m pip install -e ".[auth]"'
)


def _import_password_hash() -> type[PasswordHash]:
    try:
        from pwdlib import PasswordHash
    except ImportError as exc:
        raise AuthDependencyError(_AUTH_EXTRA_MESSAGE) from exc
    return PasswordHash


@lru_cache(maxsize=1)
def _recommended_hasher() -> PasswordHash:
    return _import_password_hash().recommended()


def validate_password(password: str) -> None:
    """Reject passwords outside 15–128 Unicode code points. Does not strip or truncate."""
    if not isinstance(password, str):
        raise PasswordPolicyError(_POLICY_MESSAGE)
    length = len(password)
    if length < MIN_PASSWORD_LENGTH or length > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(_POLICY_MESSAGE)


def hash_password(password: str) -> str:
    """Return an encoded Argon2 hash. Never returns the plaintext."""
    validate_password(password)
    return _recommended_hasher().hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Return True only when password matches the encoded hash. Fail closed otherwise."""
    if not isinstance(password, str) or not isinstance(password_hash, str) or not password_hash:
        return False
    try:
        return bool(_recommended_hasher().verify(password, password_hash))
    except Exception:
        return False
