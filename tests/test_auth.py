import time

import pytest

from cortex import auth


def test_password_roundtrip():
    pw_hash, salt = auth.hash_password("correct horse")
    assert auth.verify_password("correct horse", pw_hash, salt)
    assert not auth.verify_password("wrong horse", pw_hash, salt)


def test_salt_makes_hashes_differ():
    h1, _ = auth.hash_password("same")
    h2, _ = auth.hash_password("same")
    assert h1 != h2


def test_session_roundtrip(tmp_path):
    secret = auth.load_secret(tmp_path)
    token = auth.mint_session(secret, "erwin")
    assert auth.check_session(secret, token) == "erwin"


def test_session_expiry(tmp_path):
    secret = auth.load_secret(tmp_path)
    token = auth.mint_session(secret, "erwin", now=time.time() - 15 * 86400)
    assert auth.check_session(secret, token) is None


def test_session_tamper(tmp_path):
    secret = auth.load_secret(tmp_path)
    token = auth.mint_session(secret, "erwin")
    forged = token.replace("erwin", "admin")
    assert auth.check_session(secret, forged) is None
    assert auth.check_session(secret, "garbage") is None
    other_secret = auth.load_secret(tmp_path / "other")
    assert auth.check_session(other_secret, token) is None


def test_secret_persists(tmp_path):
    assert auth.load_secret(tmp_path) == auth.load_secret(tmp_path)


def test_username_rules():
    assert auth.validate_username("erwin") == "erwin"
    assert auth.validate_username("sam-2") == "sam-2"
    for bad in ("Shared", "shared", "sources", "a", "has space", "dot.name", "x" * 40):
        with pytest.raises(auth.AuthError):
            auth.validate_username(bad)
