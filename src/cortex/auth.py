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


# ---- sign-in throttling -----------------------------------------------------
#
# scrypt at n=2**14 costs an attacker roughly 50-100 ms a guess, which is a
# speed bump and not a defence. Cortex is meant to be reachable by a whole
# household, so weak passwords are likely by construction and the guessing is
# worth making expensive in wall-clock terms too.
#
# A fixed window over failures, keyed by client and username together: locking
# on username alone lets anyone lock a housemate out, and locking on address
# alone lets one bad client wedge everyone behind the same NAT.

FAILURE_LIMIT = 8
FAILURE_WINDOW_S = 300.0
# Failures are only ever recorded for keys that have already reached the login
# handler, but the table still needs a ceiling: a spray across many usernames
# should not be able to grow it without bound.
_MAX_TRACKED = 4096


class Throttle:
    """Counts recent sign-in failures and refuses once there are too many."""

    def __init__(
        self,
        limit: int = FAILURE_LIMIT,
        window_s: float = FAILURE_WINDOW_S,
        max_tracked: int = _MAX_TRACKED,
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self.max_tracked = max_tracked
        # key -> (failures, window_started_at)
        self._hits: dict[str, tuple[int, float]] = {}

    def _now(self) -> float:
        return time.monotonic()

    def _prune(self, now: float) -> None:
        stale = [k for k, (_, started) in self._hits.items() if now - started >= self.window_s]
        for k in stale:
            del self._hits[k]
        if len(self._hits) > self.max_tracked:
            # Oldest windows first; these are the closest to expiring anyway.
            for k, _ in sorted(self._hits.items(), key=lambda kv: kv[1][1])[
                : len(self._hits) - self.max_tracked
            ]:
                del self._hits[k]

    def retry_after(self, key: str) -> float:
        """Seconds until this key may try again. 0.0 when it may try now."""
        now = self._now()
        entry = self._hits.get(key)
        if entry is None:
            return 0.0
        failures, started = entry
        if now - started >= self.window_s:
            del self._hits[key]
            return 0.0
        if failures < self.limit:
            return 0.0
        return self.window_s - (now - started)

    def record_failure(self, key: str) -> None:
        now = self._now()
        failures, started = self._hits.get(key, (0, now))
        if now - started >= self.window_s:
            failures, started = 0, now
        self._hits[key] = (failures + 1, started)
        # After the insert, so the ceiling actually holds: pruning first left
        # max_tracked entries and then added one more.
        self._prune(now)

    def clear(self, key: str) -> None:
        """A correct password forgives the failures before it."""
        self._hits.pop(key, None)
