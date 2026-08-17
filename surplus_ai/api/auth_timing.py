"""Login timing mitigation for unknown emails (P3-B2).

Uses the existing password hasher only. The dummy hash is not an account.
"""

from __future__ import annotations

from functools import lru_cache

from surplus_ai.auth.passwords import MIN_PASSWORD_LENGTH, hash_password, verify_password


@lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    # Process-local Argon2 hash; regenerated only on first use per process.
    return hash_password("0" * MIN_PASSWORD_LENGTH)


def run_unknown_user_password_check(password: str) -> None:
    """Burn comparable Argon2 verify work for an unknown email. Result ignored."""
    verify_password(password, _dummy_password_hash())
