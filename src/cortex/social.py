"""Scheduled posts, held in the vault as markdown.

## Why the queue is markdown and not a table

A post is a draft long before it is a post: it gets rewritten, argued with,
abandoned. That is a document, and the vault is already the place documents live
— versioned by the same editor, searchable by the same index, and readable
without this module existing. A database row would be a second store to back up,
a second thing to open, and a place drafts go to be forgotten.

It also means the approval gate is the one already built for reviews: a post
sits unticked until a human ticks it. Nothing here publishes anything on its
own, and there is deliberately no credential in this module — `queue_post`
writes a file, and something else with an account decides whether it ever
leaves the building.

## The shape

`social/queue.md`, newest first:

    - [ ] **2026-09-08 · linkedin** — Weekly note on the review loop
          **Hook:** the line that has to earn the click
          **Body:** the post itself, as it would be published
          **Source:** reviews/fragmentation-2026-09-06.md#F1

The tick means the same thing it means in reviews/ — a human approved it — and
`write_note` strips ticks under reviews/ for that reason. It does NOT strip them
here, because a person drafting in the vault editor must be able to approve
their own post; what publishes is a separate step that reads this file.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

QUEUE = "social/queue.md"

#: Where a post can go. Kept short on purpose: a channel nobody has an account
#: for is a draft that silently never ships.
CHANNELS = ("linkedin", "x", "clankergram", "blog", "newsletter")

_ENTRY = re.compile(
    r"^\s*[-*]\s*\[(?P<tick>[ xX])\]\s*\*\*(?P<when>\d{4}-\d{2}-\d{2})\s*[·.\-]\s*"
    r"(?P<channel>[a-z]+)\*\*\s*[—-]\s*(?P<title>.+?)\s*$"
)


def parse(text: str) -> list[dict[str, Any]]:
    """Every queued post, with its line number so one can be edited in place."""
    out: list[dict[str, Any]] = []
    lines = text.splitlines()
    for n, line in enumerate(lines, start=1):
        m = _ENTRY.match(line)
        if not m:
            continue
        # The indented block under an entry is its body, up to the next entry.
        body: list[str] = []
        for follow in lines[n:]:
            if _ENTRY.match(follow) or (follow.strip() and not follow.startswith((" ", "\t"))):
                break
            if follow.strip():
                body.append(follow.strip())
        out.append({
            "line": n,
            "approved": m.group("tick").lower() == "x",
            "date": m.group("when"),
            "channel": m.group("channel"),
            "title": m.group("title"),
            "body": "\n".join(body),
        })
    return out


def render(when: str, channel: str, title: str, hook: str, body: str,
           source: str = "") -> str:
    """One queue entry, in the shape `parse` reads back."""
    block = [f"- [ ] **{when} · {channel}** — {title}"]
    if hook:
        block.append(f"      **Hook:** {hook}")
    block.append(f"      **Body:** {body}")
    if source:
        # Where the claim came from. A weekly post built from the brain should
        # be traceable to the finding it is about, or it is just an opinion with
        # a date on it.
        block.append(f"      **Source:** {source}")
    return "\n".join(block)


def validate(when: str, channel: str, title: str, body: str) -> str:
    """The reason this cannot be queued, or "" if it can."""
    channel = (channel or "").strip().lower()
    if channel not in CHANNELS:
        return f"channel must be one of {', '.join(CHANNELS)}"
    try:
        date.fromisoformat((when or "").strip())
    except ValueError:
        return "date must be YYYY-MM-DD"
    if len((title or "").strip()) < 8:
        return "give it a title somebody could recognise in a list"
    if len((body or "").strip()) < 40:
        # A stub in the queue costs the same attention as a real draft and
        # carries none of the information.
        return "the body is too short to be a draft — write the post, not a note to write it"
    return ""
