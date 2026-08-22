"""A small library of ready-made skills, installable in one click.

The Extend panel used to open on four empty lists and a warning about
arbitrary code execution — it told you what you had, never what any of it
was for, and the only way to get anything was to write it. Most people do
not want to write a skill; they want the one that already does the thing.

These are procedures, not code: markdown instructions the agent follows.
They are safe to install, easy to read, and meant to be edited afterwards
— each one is a starting point someone will want to make their own.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LibrarySkill:
    name: str
    description: str
    instructions: str

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "instructions": self.instructions,
        }


SKILLS: list[LibrarySkill] = [
    LibrarySkill(
        name="weekly-review",
        description="Walk through the week: what moved, what slipped, what matters next",
        instructions="""\
1. Call daily_digest for today's state.
2. Search for notes changed in the last seven days and summarize what moved,
   grouped by area rather than by file.
3. List the open tasks that have been open longest — those are the ones that
   need a decision, not more time.
4. Ask which of them should be dropped rather than carried. Carrying a task
   for a month is a decision too, just an unmade one.
5. End with the single most important thing for the coming week.

Keep it under a page. A review nobody finishes is a review nobody repeats.
""",
    ),
    LibrarySkill(
        name="meeting-notes",
        description="Turn a rough set of notes into a decision record",
        instructions="""\
When given rough meeting notes:

1. Extract, in this order: the decision, who owns each action, and the dates.
2. Write it back with capture_note, one line per action, each naming its owner.
3. Say plainly what was discussed but *not* decided — an open question that
   looks settled is the expensive kind of mistake.
4. Do not invent attendees, dates or decisions. If the notes do not say who
   owns something, write "owner unassigned" rather than guessing.
""",
    ),
    LibrarySkill(
        name="shopping-list",
        description="Keep a running list and tick things off",
        instructions="""\
The list lives in the shared vault. When asked to add something:

1. Search for an existing shopping list note before making a new one.
2. Add the item as a markdown task (`- [ ] milk`) using capture_note if there
   is no list yet, and read_file plus complete_task to tick items off.
3. When asked what is on the list, show only unticked items.
4. Never reorder or rewrite someone else's items — add yours and leave theirs.
""",
    ),
    LibrarySkill(
        name="trip-planning",
        description="Keep everything about one trip in one place",
        instructions="""\
For a trip, keep a single note per trip under trips/.

1. Search for an existing note for this trip first; add to it rather than
   starting a second one.
2. Record dates, where you are staying, and the booking references you are
   given — never invent a reference number.
3. Keep the open items as markdown tasks so they show up in Today.
4. Flag anything with a deadline (visas, passport validity, advance bookings)
   in a `> [!warning]` callout, with the date it stops being possible.
""",
    ),
    LibrarySkill(
        name="house-manual",
        description="Answer 'how does the boiler work' from what the house already knows",
        instructions="""\
Questions about how something in the house works:

1. Search before answering — the answer is usually written down already, and
   a wrong answer about a boiler is worse than no answer.
2. Cite the note so the reader can check it themselves.
3. If nothing is written down, say so and offer to record what they tell you
   with capture_note, so the next person does not have to ask.
""",
    ),
    LibrarySkill(
        name="inbox-triage",
        description="Sort today's captured lines into what needs doing",
        instructions="""\
Given today's captured lines:

1. Read today's daily note.
2. Sort the lines into: needs an action, worth keeping as a note, and neither.
3. For each action, propose the one next step in plain words.
4. Suggest which of the keepers belong in a longer-lived note, and where —
   but do not move anything yourself. Filing is the owner's call.
""",
    ),
]


@dataclass(frozen=True)
class LibraryConnector:
    name: str
    description: str
    kind: str  # "builtin" (already ships) or "template" (writes a starter file)
    settings: dict
    code: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
            "settings": self.settings,
        }


RSS_CONNECTOR = r'''"""Follow a feed and keep its items as notes.

Settings:
    feeds: {label: url}
    keep:  how many recent items per feed (default 30)
"""

import re
import xml.etree.ElementTree as ET
from html import unescape

import httpx

TAGS = re.compile(r"<[^>]+>")


def _clean(text):
    return " ".join(unescape(TAGS.sub(" ", text or "")).split())


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:70] or "item"


def sync(out_dir, settings):
    feeds = dict(settings.get("feeds") or {})
    if not feeds:
        return  # unconfigured connectors return early, by contract
    keep = int(settings.get("keep", 30))
    wanted = set()
    for label, url in feeds.items():
        res = httpx.get(url, timeout=30, follow_redirects=True)
        res.raise_for_status()
        root = ET.fromstring(res.text)
        # RSS puts items at channel/item; Atom uses a namespaced entry
        items = root.findall(".//item") or root.findall(
            ".//{http://www.w3.org/2005/Atom}entry"
        )
        for item in items[:keep]:
            def field(*names):
                for n in names:
                    found = item.find(n)
                    if found is not None:
                        return found.text or found.get("href") or ""
                return ""

            title = _clean(field("title", "{http://www.w3.org/2005/Atom}title"))
            if not title:
                continue
            link = field("link", "{http://www.w3.org/2005/Atom}link")
            when = _clean(field("pubDate", "{http://www.w3.org/2005/Atom}updated"))
            body = _clean(
                field("description", "{http://www.w3.org/2005/Atom}summary")
            )[:1200]
            name = f"{_slug(label)}-{_slug(title)}.md"
            wanted.add(name)
            (out_dir / name).write_text(
                f"# {title}\n\n- Feed: {label}\n- Published: {when}\n"
                f"- Link: {link}\n\n{body}\n",
                encoding="utf-8",
            )
    for stale in out_dir.glob("*.md"):
        if stale.name not in wanted:
            stale.unlink()
'''


CONNECTORS: list[LibraryConnector] = [
    LibraryConnector(
        name="calendar_ics",
        description="Read a published calendar link so today's events show up in Today",
        kind="builtin",
        settings={"urls": {"home": "https://calendar.example/private-abc.ics"},
                  "days_ahead": 30},
    ),
    LibraryConnector(
        name="rss",
        description="Follow a feed and keep its recent items as searchable notes",
        kind="template",
        settings={"feeds": {"example": "https://example.com/feed.xml"}, "keep": 30},
        code=RSS_CONNECTOR,
    ),
]


def as_dicts() -> list[dict]:
    return [s.as_dict() for s in SKILLS]


def connectors_as_dicts() -> list[dict]:
    return [c.as_dict() for c in CONNECTORS]


def get_connector(name: str) -> LibraryConnector | None:
    return next((c for c in CONNECTORS if c.name == name), None)


def get(name: str) -> LibrarySkill | None:
    return next((s for s in SKILLS if s.name == name), None)
