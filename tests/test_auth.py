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


# ---- sign-in throttling -----------------------------------------------------


def test_throttle_allows_up_to_the_limit_then_refuses():
    t = auth.Throttle(limit=3, window_s=60)
    key = "10.0.0.4|erwin"
    for _ in range(3):
        assert t.retry_after(key) == 0.0
        t.record_failure(key)
    assert t.retry_after(key) > 0


def test_a_correct_password_forgives_the_failures_before_it():
    t = auth.Throttle(limit=2, window_s=60)
    key = "10.0.0.4|erwin"
    t.record_failure(key)
    t.record_failure(key)
    assert t.retry_after(key) > 0
    t.clear(key)
    assert t.retry_after(key) == 0.0


def test_the_window_expires():
    t = auth.Throttle(limit=1, window_s=0.01)
    key = "10.0.0.4|erwin"
    t.record_failure(key)
    assert t.retry_after(key) > 0
    time.sleep(0.02)
    assert t.retry_after(key) == 0.0


def test_one_client_cannot_lock_out_another():
    """The key is client and username together, on purpose."""
    t = auth.Throttle(limit=2, window_s=60)
    for _ in range(3):
        t.record_failure("10.0.0.9|erwin")
    assert t.retry_after("10.0.0.9|erwin") > 0
    # Same user, different machine: unaffected.
    assert t.retry_after("10.0.0.4|erwin") == 0.0
    # Same machine, different user: unaffected.
    assert t.retry_after("10.0.0.9|sam") == 0.0


def test_the_table_does_not_grow_without_bound():
    t = auth.Throttle(limit=5, window_s=600, max_tracked=50)
    for i in range(500):
        t.record_failure(f"10.0.0.{i}|erwin")
    assert len(t._hits) <= 50
