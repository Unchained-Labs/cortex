"""Recall over the brain's own conversations.

A brain that answered a question last week and cannot find that answer
this week is not much of a brain. Hermes Agent calls this session search;
here it is ``recall_conversation``: full-text over every agent thread the
caller owns, grouped by thread with the matching lines, and the whole
thread on request. Nothing leaves the caller's own threads — the store
filters by owner, and the box owner (no scope) sees everything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cortex import scope
from cortex.plugins import ToolPlugin, ToolRegistry

if TYPE_CHECKING:
    from cortex.brain import Brain

MAX_THREADS = 8
MAX_LINES_PER_THREAD = 3
READ_LIMIT = 60


def _owner_filter() -> str | None | bool:
    """None = every thread (the box owner); a username = that person's;
    False = nobody's (a scoped caller with no identity)."""
    user = scope.current_user.get()
    if user:
        return user
    return None if scope.current_prefixes.get() is None else False


def register_recall_tools(registry: ToolRegistry, brain: Brain) -> None:
    def recall_conversation(query: str = "", thread: str = "") -> str:
        owner = _owner_filter()
        if owner is False:
            return "No conversations are readable from here."
        thread = (thread or "").strip()
        if thread:
            actual = brain.store.thread_owner(thread)
            if actual is None or (owner is not None and actual != owner):
                return f"No conversation {thread!r} of yours."
            rows = brain.store.history(thread, limit=READ_LIMIT)
            out = [f"Conversation {thread} ({len(rows)} most recent messages):"]
            for r in rows:
                who = "you" if r["role"] == "user" else "cortex"
                body = r["body"].strip().replace("\n", " ")
                out.append(f"[{r['created_at'][:16]}] {who}: {body[:600]}")
            return "\n".join(out)
        query = (query or "").strip()
        if not query:
            return "Give words to search for, or a thread id to read."
        rows = brain.store.search_messages(query, owner)
        if not rows:
            return f"Nothing in past conversations matches {query!r}."
        by_thread: dict[str, list] = {}
        for r in rows:
            by_thread.setdefault(r["thread"], []).append(r)
            if len(by_thread) > MAX_THREADS and r["thread"] not in list(by_thread)[:MAX_THREADS]:
                by_thread.pop(r["thread"])
        out = [f"{len(by_thread)} conversation(s) mention {query!r}:"]
        for tid, hits in list(by_thread.items())[:MAX_THREADS]:
            title = hits[0]["title"] or tid
            out.append(f"--- {title} (thread {tid}, {hits[0]['created_at'][:10]}) ---")
            for h in hits[:MAX_LINES_PER_THREAD]:
                who = "you" if h["role"] == "user" else "cortex"
                out.append(f"  {who}: {h['snippet']}")
        out.append("Pass a thread id to read the whole conversation.")
        return "\n".join(out)

    registry.register(
        ToolPlugin(
            name="recall_conversation",
            description=(
                "Search past conversations with this person for something discussed before, "
                "or read one whole by its thread id. Use it when they say 'as we discussed', "
                "'that thing you found', or ask what was decided last time."
            ),
            parameters={
                "query": {"type": "string", "description": "Words to look for."},
                "thread": {
                    "type": "string",
                    "description": "A thread id from an earlier result, to read it in full.",
                },
            },
            required=(),
            func=recall_conversation,
        )
    )
