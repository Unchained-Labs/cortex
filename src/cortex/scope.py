"""Per-request retrieval scope.

A ContextVar carries the path prefixes the current caller may read
("shared/", "erwin/", "sources/"). ``None`` means unrestricted — the box
owner at the CLI or the MCP export. An empty tuple means *nothing*.
The two are never conflated: ``None`` is the absence of a restriction,
an empty set is a restriction that grants nothing — collapsing them is
the classic way an access check quietly grants everything.

Scope is applied inside the store's queries (an SQL filter), not by
trimming results afterwards, so a restricted caller gets the same ranking
minus what they may not see — not a shorter list with holes.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

current_prefixes: ContextVar[tuple[str, ...] | None] = ContextVar(
    "cortex_scope_prefixes", default=None
)
current_user: ContextVar[str] = ContextVar("cortex_scope_user", default="")


@contextmanager
def scoped(prefixes: tuple[str, ...] | None, user: str = ""):
    token_p = current_prefixes.set(prefixes)
    token_u = current_user.set(user)
    try:
        yield
    finally:
        current_prefixes.reset(token_p)
        current_user.reset(token_u)


def allows_path(path: str) -> bool:
    prefixes = current_prefixes.get()
    if prefixes is None:
        return True
    return any(path.startswith(p) for p in prefixes)


def user_prefixes(username: str, extra_roots: list[str] | None = None) -> tuple[str, ...]:
    """What a signed-in dashboard user may read: the shared vault, their own
    vault, connector sources, and any extra indexed roots."""
    prefixes = ["vaults/shared/", f"vaults/{username}/", "sources/"]
    prefixes += [f"{name}/" for name in (extra_roots or [])]
    return tuple(prefixes)
