"""Bearer keys for the MCP endpoint.

The endpoint can write to the vault, so the properties under test are the ones
that decide whether a leaked database or a wrong guess turns into a write:
plaintext is never stored, a key resolves to exactly one account, and anything
that is not a valid key resolves to nobody.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cortex import auth


@pytest.fixture()
def store(tmp_path):
    from cortex.memory.store import Store

    s = Store(tmp_path / "brain.db")
    s.add_user("erwin", "hash", "salt", "admin")
    return s


def _add(store, token: str, name: str = "leclanker", user: str = "erwin") -> None:
    store.add_api_key(
        auth.hash_api_key(token), name, user,
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def test_minted_keys_are_prefixed_and_unique() -> None:
    keys = {auth.mint_api_key() for _ in range(50)}
    assert len(keys) == 50
    assert all(k.startswith(auth.API_KEY_PREFIX) for k in keys)


def test_plaintext_is_never_stored(store) -> None:
    token = auth.mint_api_key()
    _add(store, token)
    # The whole database, as text. The key must not appear anywhere in it —
    # this is the property that makes a stolen backup not a stolen credential.
    dump = "\n".join(store.db.iterdump())
    assert token not in dump
    assert auth.hash_api_key(token) in dump


def test_a_valid_key_resolves_to_its_user(store) -> None:
    token = auth.mint_api_key()
    _add(store, token, user="erwin")
    assert auth.check_api_key(store, token) == "erwin"


@pytest.mark.parametrize("token", [
    "",
    "ctx_not-a-real-key",
    "sk-or-v1-wrong-kind-of-credential",
    "Bearer ctx_leading-scheme",       # the caller must strip the scheme
])
def test_anything_else_resolves_to_nobody(store, token: str) -> None:
    _add(store, auth.mint_api_key())
    assert auth.check_api_key(store, token) is None


def test_revoking_takes_effect_immediately(store) -> None:
    token = auth.mint_api_key()
    _add(store, token, name="leclanker")
    assert auth.check_api_key(store, token) == "erwin"
    assert store.delete_api_key("leclanker") == 1
    assert auth.check_api_key(store, token) is None


def test_use_is_recorded(store) -> None:
    # An unused key is the one that can be revoked without asking anybody, and
    # there is no way to know which those are without writing down last use.
    token = auth.mint_api_key()
    _add(store, token)
    assert store.list_api_keys()[0]["last_used_at"] is None
    auth.check_api_key(store, token)
    assert store.list_api_keys()[0]["last_used_at"] is not None


def test_listing_never_exposes_the_hash(store) -> None:
    _add(store, auth.mint_api_key())
    row = store.list_api_keys()[0]
    assert "token_hash" not in row.keys()
