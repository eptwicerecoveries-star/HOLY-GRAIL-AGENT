"""P3-A password hashing and length policy tests. Fake secrets only."""

from __future__ import annotations

import pytest

from surplus_ai.auth.exceptions import AuthDependencyError, PasswordPolicyError
from surplus_ai.auth.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hash_password,
    validate_password,
    verify_password,
)

_FAKE_OK = "test-passphrase-ok"
_FAKE_OTHER = "other-passphrase!!"


def test_hash_is_not_plaintext() -> None:
    encoded = hash_password(_FAKE_OK)
    assert encoded != _FAKE_OK
    assert _FAKE_OK not in encoded


def test_hash_verifies_correct_password() -> None:
    encoded = hash_password(_FAKE_OK)
    assert verify_password(_FAKE_OK, encoded) is True


def test_wrong_password_fails() -> None:
    encoded = hash_password(_FAKE_OK)
    assert verify_password(_FAKE_OTHER, encoded) is False


def test_same_password_hashes_differ() -> None:
    first = hash_password(_FAKE_OK)
    second = hash_password(_FAKE_OK)
    assert first != second
    assert verify_password(_FAKE_OK, first)
    assert verify_password(_FAKE_OK, second)


def test_encoded_hash_identifies_argon2() -> None:
    encoded = hash_password(_FAKE_OK)
    assert encoded.startswith("$argon2")


def test_verify_fails_closed_on_empty_or_unknown_hash() -> None:
    assert verify_password(_FAKE_OK, "") is False
    assert verify_password(_FAKE_OK, "not-a-real-hash") is False


def test_policy_rejects_14_characters() -> None:
    with pytest.raises(PasswordPolicyError, match="15 and 128"):
        validate_password("a" * 14)
    with pytest.raises(PasswordPolicyError):
        hash_password("a" * 14)


def test_policy_accepts_15_and_128() -> None:
    fifteen = "b" * MIN_PASSWORD_LENGTH
    upper = "c" * MAX_PASSWORD_LENGTH
    validate_password(fifteen)
    validate_password(upper)
    assert verify_password(fifteen, hash_password(fifteen))
    encoded_upper = hash_password(upper)
    assert verify_password(upper, encoded_upper)
    assert not verify_password(upper[:127], encoded_upper)


def test_policy_rejects_129_characters() -> None:
    with pytest.raises(PasswordPolicyError, match="15 and 128"):
        validate_password("d" * 129)


def test_policy_accepts_spaces_and_simple_passphrase() -> None:
    spaces = " " * 15
    phrase = "correct horse battery"
    validate_password(spaces)
    validate_password(phrase)
    assert verify_password(spaces, hash_password(spaces))
    assert verify_password(phrase, hash_password(phrase))


def test_policy_does_not_strip_or_truncate() -> None:
    padded = "  passphrase-ok  "
    encoded = hash_password(padded)
    assert verify_password(padded, encoded)
    assert not verify_password(padded.strip(), encoded)


def test_policy_error_does_not_include_password() -> None:
    secret = "too-short-pass"
    with pytest.raises(PasswordPolicyError) as exc_info:
        validate_password(secret)
    assert secret not in str(exc_info.value)


def test_missing_pwdlib_is_controlled_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    import surplus_ai.auth.passwords as passwords

    real_import = builtins.__import__

    def _blocked_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "pwdlib" or name.startswith("pwdlib."):
            raise ImportError("No module named 'pwdlib'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)
    passwords._recommended_hasher.cache_clear()
    try:
        with pytest.raises(AuthDependencyError, match="optional auth extra"):
            hash_password(_FAKE_OK)
    finally:
        passwords._recommended_hasher.cache_clear()
