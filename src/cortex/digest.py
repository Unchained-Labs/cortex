"""The daily digest: what the brain knows about today.

Deliberately computed without the model. A digest is a fact, not an
opinion — open tasks, today's events, what changed — so it must be
instant, free, and identical every time you look. It also has to work on a
brain with no model configured at all, which is exactly the state a new
install is in.

The agent can still summarize it (``daily_digest`` is a tool), but the
numbers it reads come from here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from cortex.config import BrainConfig
from cortex.memory.indexer import scan_files
from cortex.memory.store import Store

TASK_RE = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.+?)\s*$")
EVENT_START_RE = re.compile(r"^-\s+Start:\s*(\S+)", re.MULTILINE)
EVENT_TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
MAX_SCAN_FILES = 2000
# A daily surface must be bounded and finishable. An unbounded "you have 47
# things pending" counter grows every day you do not act on it, and the
# products that shipped one (Anki's due count) document the same outcome:
# people stop opening it. Readwise shows a fixed five and does not
# accumulate; ChatGPT Pulse stops and says so. Five, most-recently-touched.
MAX_TASKS = 5


@dataclass
class Task:
    path: str
    text: str
    line: int


@dataclass
class Event:
    path: str
    title: str
    start: str
    today: bool


@dataclass
class Changed:
    path: str
    mtime: float


@dataclass
class Remembered:
    path: str
    when: str
    years: int


@dataclass
class Digest:
    day: str
    tasks: list[Task] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    changed: list[Changed] = field(default_factory=list)
    on_this_day: list[Remembered] = field(default_factory=list)
    captured_today: int = 0

    def is_empty(self) -> bool:
        return not (
            self.tasks
            or self.events
            or self.changed
            or self.on_this_day
            or self.captured_today
        )

    def as_dict(self) -> dict:
        return {
            "day": self.day,
            "tasks": [{"path": t.path, "text": t.text, "line": t.line} for t in self.tasks],
            "events": [
                {"path": e.path, "title": e.title, "start": e.start, "today": e.today}
                for e in self.events
            ],
            "changed": [{"path": c.path, "mtime": c.mtime} for c in self.changed],
            "on_this_day": [
                {"path": r.path, "when": r.when, "years": r.years} for r in self.on_this_day
            ],
            "captured_today": self.captured_today,
        }


def _allowed(key: str, prefixes: tuple[str, ...] | None) -> bool:
    if prefixes is None:
        return True
    return any(key.startswith(p) for p in prefixes)


def open_tasks(
    config: BrainConfig, prefixes: tuple[str, ...] | None = None, limit: int = MAX_TASKS
) -> list[Task]:
    """A few unchecked markdown tasks, from the most recently touched files.

    Deliberately returns a handful and no total. The scan stops at
    MAX_SCAN_FILES in directory order, so any total would be a guess
    presented as a fact — and a total is the wrong thing to show anyway.
    """
    found: list[tuple[float, Task]] = []
    pairs = [(p, r) for p, r in config.root_pairs() if _allowed(f"{p}/", prefixes)]
    for i, (key, path) in enumerate(scan_files(pairs).items()):
        if i >= MAX_SCAN_FILES:
            break
        if path.suffix.lower() not in (".md", ".markdown") or not _allowed(key, prefixes):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if "[ ]" not in text:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = TASK_RE.match(line)
            if match and match.group(1) == " ":
                found.append((mtime, Task(path=key, text=match.group(2), line=lineno)))
    found.sort(key=lambda pair: pair[0], reverse=True)
    return [task for _, task in found[:limit]]


def upcoming_events(
    config: BrainConfig, prefixes: tuple[str, ...] | None = None, days: int = 7
) -> list[Event]:
    """Events from calendar connector output, today first."""
    events: list[Event] = []
    sources = config.sources_dir
    if not sources.is_dir():
        return events
    today = date.today()
    horizon = today + timedelta(days=days)
    for path in sorted(sources.rglob("*.md")):
        key = f"sources/{path.relative_to(sources)}"
        if not _allowed(key, prefixes):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        start_match = EVENT_START_RE.search(text)
        if not start_match:
            continue
        raw = start_match.group(1)
        try:
            when = datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if not (today <= when <= horizon):
            continue
        title_match = EVENT_TITLE_RE.search(text)
        events.append(
            Event(
                path=key,
                title=title_match.group(1).strip() if title_match else path.stem,
                start=raw,
                today=when == today,
            )
        )
    events.sort(key=lambda e: e.start)
    return events


def on_this_day(
    config: BrainConfig, prefixes: tuple[str, ...] | None = None, today: date | None = None
) -> list[Remembered]:
    """Daily notes written on this calendar day in earlier years.

    Only journal entries — things the owner deliberately wrote as a diary —
    are resurfaced, and only ever as a plain link. Resurfacing is a
    well-documented way to hurt people (a note from a hard year is still a
    note from a hard year), so it never wraps anything in celebration and
    it stays silent when there is nothing.
    """
    today = today or date.today()
    out: list[Remembered] = []
    for prefix, root in config.root_pairs():
        journal = root / "journal"
        if not journal.is_dir() or not _allowed(f"{prefix}/", prefixes):
            continue
        for path in sorted(journal.glob("*.md")):
            try:
                when = date.fromisoformat(path.stem)
            except ValueError:
                continue
            if when.year >= today.year or (when.month, when.day) != (today.month, today.day):
                continue
            key = f"{prefix}/journal/{path.name}"
            if not _allowed(key, prefixes):
                continue
            out.append(
                Remembered(path=key, when=when.isoformat(), years=today.year - when.year)
            )
    out.sort(key=lambda r: r.years)
    return out


def recent_changes(
    store: Store, prefixes: tuple[str, ...] | None = None, days: int = 3, limit: int = 8
) -> list[Changed]:
    cutoff = (datetime.now() - timedelta(days=days)).timestamp()
    return [
        Changed(path=row["path"], mtime=row["mtime"])
        for row in store.recent_files(prefixes, cutoff, limit)
    ]


def build_digest(
    config: BrainConfig,
    store: Store,
    prefixes: tuple[str, ...] | None = None,
    vault: str = "",
) -> Digest:
    digest = Digest(
        day=date.today().isoformat(),
        tasks=open_tasks(config, prefixes),
        events=upcoming_events(config, prefixes),
        changed=recent_changes(store, prefixes),
        on_this_day=on_this_day(config, prefixes),
    )
    if vault:
        from cortex.capture import read_daily_note

        note = read_daily_note(config, vault)
        digest.captured_today = sum(1 for line in note.splitlines() if line.startswith("- **"))
    return digest


def format_digest(digest: Digest) -> str:
    """Plain text, for the CLI and for the agent's tool result."""
    if digest.is_empty():
        return (
            f"{digest.day}: nothing on. No open tasks, no upcoming events, "
            "and nothing changed recently."
        )
    lines = [f"# {digest.day}"]
    today_events = [e for e in digest.events if e.today]
    later = [e for e in digest.events if not e.today]
    if today_events:
        lines.append("\n## Today")
        lines += [f"- {e.start[11:16]} {e.title}  ({e.path})" for e in today_events]
    if later:
        lines.append("\n## Coming up")
        lines += [f"- {e.start[:10]} {e.title}  ({e.path})" for e in later[:5]]
    if digest.tasks:
        lines.append("\n## Open tasks")
        lines += [f"- [ ] {t.text}  ({t.path}:{t.line})" for t in digest.tasks]
    if digest.on_this_day:
        lines.append("\n## On this day")
        for r in digest.on_this_day:
            years = "a year ago" if r.years == 1 else f"{r.years} years ago"
            lines.append(f"- {years}: {r.path}")
    if digest.changed:
        lines.append("\n## Changed recently")
        lines += [f"- {c.path}" for c in digest.changed]
    if digest.captured_today:
        lines.append(f"\nCaptured today: {digest.captured_today}")
    # End definitively. A daily surface should finish, not trail off into
    # everything you have not done.
    lines.append("\nThat is everything for today.")
    return "\n".join(lines)
