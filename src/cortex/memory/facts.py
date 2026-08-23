"""Typed long-term memory: what the brain knows, and about whom.

A flat list of remembered sentences answers "what do you know" badly. Ask
a household brain who to call about the boiler and it should not have to
search prose — it should know that *Meridian Heating* is a **person**
entry whose subject is "boiler". So a memory has a **kind** and a
**subject** as well as a body:

* ``person``     — who someone is, how to reach them, what matters about them
* ``project``    — something ongoing, and where it stands
* ``preference`` — how this household likes things done
* ``goal``       — something being worked towards
* ``fact``       — everything else, which is where untyped memories land

Two rules the design leans on:

* **Nothing is lost on upgrade.** Memories written before kinds existed
  become ``fact`` with no subject, which is exactly what they were.
* **Memory is visible and correctable.** A brain that quietly remembers a
  wrong thing about a person is worse than one that remembers nothing, so
  everything here is listable, editable and removable by a human.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

KINDS = ("person", "project", "preference", "goal", "fact")
DEFAULT_KIND = "fact"
MAX_SUBJECT = 80


class MemoryError(ValueError):
    pass


@dataclass
class Memory:
    id: int
    kind: str
    subject: str
    body: str
    source: str
    created_at: str

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "subject": self.subject,
            "body": self.body,
            "source": self.source,
            "created_at": self.created_at,
        }

    def line(self) -> str:
        """One line, for a tool result the model reads."""
        head = f"{self.kind}"
        if self.subject:
            head += f" · {self.subject}"
        return f"#{self.id} [{head}] {self.body}"


def normalise_kind(kind: str) -> str:
    kind = (kind or "").strip().lower()
    if not kind:
        return DEFAULT_KIND
    if kind not in KINDS:
        raise MemoryError(f"unknown kind {kind!r} (expected {', '.join(KINDS)})")
    return kind


def normalise_subject(subject: str) -> str:
    """Who or what this is about. Free text, but tidied and capped so it
    stays a handle rather than becoming a second body."""
    subject = " ".join((subject or "").split())[:MAX_SUBJECT]
    return subject


def guess_subject(body: str) -> str:
    """A best-effort subject for a memory saved without one.

    Deliberately conservative: it takes a leading proper noun or a
    quoted-looking name and nothing cleverer. A wrong subject is worse than
    an empty one, because the empty case is honest.
    """
    text = body.strip()
    match = re.match(r"^([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*){0,2})\b", text)
    if not match:
        return ""
    candidate = match.group(1)
    # "The boiler is serviced in March" — an opening article is not a name
    if candidate.split()[0].lower() in {"the", "a", "an", "our", "my", "we", "i"}:
        return ""
    return normalise_subject(candidate)


def format_memories(memories: list[Memory], query: str = "") -> str:
    if not memories:
        return (
            f"Nothing remembered matches {query!r}." if query
            else "Nothing has been remembered yet."
        )
    by_kind: dict[str, list[Memory]] = {}
    for memory in memories:
        by_kind.setdefault(memory.kind, []).append(memory)
    lines: list[str] = []
    for kind in KINDS:
        group = by_kind.get(kind)
        if not group:
            continue
        lines.append(f"\n{kind}:")
        lines += [f"  {m.line()}" for m in group]
    return "\n".join(lines).strip()
