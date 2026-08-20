"""Service keys for the HTTP server.

``ctx_``-prefixed secrets. Only the SHA-256 hash is stored; the plaintext is
shown once at issue time. Verification is constant-time.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

PREFIX = "ctx_"


def generate_key() -> str:
    return PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def key_prefix(key: str) -> str:
    """First 10 characters, enough to identify a key in a list without
    revealing it."""
    return key[:10]


def verify_key(key: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_key(key), stored_hash)
