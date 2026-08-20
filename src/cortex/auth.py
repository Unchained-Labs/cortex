"""Accounts and sessions.

Passwords are scrypt-hashed with a per-user salt. Sessions are stateless
HMAC-signed tokens (username.expiry.signature) minted with a per-brain
secret stored at ``.cortex/secret``; logging out clears the cookie, and
rotating the secret file invalidates every session at once.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from pathlib import Path

SESSION_COOKIE = "cortex_session"
SESSION_DAYS = 14

# Usernames become vault directory names and search-path prefixes, so the
# alphabet is strict and collisions with reserved roots are refused.
_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
RESERVED_NAMES = {"shared", "sources", "cortex", "admin-api", "api", "assets", "app"}


class AuthError(ValueError):
    pass


def validate_username(username: str) -> str:
    if not _USERNAME_RE.match(username):
        raise AuthError(
            "usernames are 2-32 chars of lowercase letters, digits, '-' or '_'"
        )
    if username in RESERVED_NAMES:
        raise AuthError(f"{username!r} is reserved")
    return username


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=bytes.fromhex(salt), n=2**14, r=8, p=1
    )
    return digest.hex(), salt


def verify_password(password: str, pw_hash: str, salt: str) -> bool:
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, pw_hash)


def load_secret(state_dir: Path) -> bytes:
    path = state_dir / "secret"
    if path.is_file():
        return path.read_bytes()
    state_dir.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_bytes(32)
    path.write_bytes(secret)
    path.chmod(0o600)
    return secret


def mint_session(secret: bytes, username: str, now: float | None = None) -> str:
    expiry = int((now or time.time()) + SESSION_DAYS * 86400)
    payload = f"{username}.{expiry}"
    sig = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def check_session(secret: bytes, token: str, now: float | None = None) -> str | None:
    """Return the username for a valid, unexpired token, else None."""
    parts = token.rsplit(".", 2)
    if len(parts) != 3:
        return None
    username, expiry_s, sig = parts
    payload = f"{username}.{expiry_s}"
    expected = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        expiry = int(expiry_s)
    except ValueError:
        return None
    if (now or time.time()) > expiry:
        return None
    return username
