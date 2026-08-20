"""Calendar connector for published ICS feeds.

Settings (cortex.yaml):

    connectors:
      calendar_ics:
        urls:
          home: "https://calendar.example/private-abc123.ics"
        days_ahead: 30

Writes one distilled markdown note per upcoming event and deletes notes for
events that vanished. Scope boundary, stated up front: single events only —
recurrence rules (RRULE) are not expanded yet, so a weekly meeting appears
only if the feed materializes its occurrences.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:80] or "untitled"


def _unfold(text: str) -> list[str]:
    """ICS folds long lines with a leading space; undo that."""
    lines: list[str] = []
    for raw in text.splitlines():
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _parse_dt(value: str) -> datetime | None:
    value = value.strip()
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=UTC) if fmt.endswith("Z") else dt.replace(tzinfo=None)
        except ValueError:
            continue
    return None


def parse_events(text: str) -> list[dict]:
    events: list[dict] = []
    current: dict | None = None
    for line in _unfold(text):
        if line.startswith("BEGIN:VEVENT"):
            current = {"attendees": []}
        elif line.startswith("END:VEVENT"):
            if current and current.get("summary") and current.get("start"):
                events.append(current)
            current = None
        elif current is not None:
            key, _, value = line.partition(":")
            base = key.split(";", 1)[0].upper()
            if base == "SUMMARY":
                current["summary"] = value.strip()
            elif base == "DTSTART":
                current["start"] = _parse_dt(value)
            elif base == "DTEND":
                current["end"] = _parse_dt(value)
            elif base == "ATTENDEE":
                mail = value.replace("mailto:", "").strip()
                if mail:
                    current["attendees"].append(mail)
            elif base == "UID":
                current["uid"] = value.strip()
    return events


def sync(out_dir: Path, settings: dict) -> None:
    urls: dict[str, str] = dict(settings.get("urls") or {})
    if not urls:
        return  # unconfigured connectors return early, by contract
    days_ahead = int(settings.get("days_ahead", 30))
    now = datetime.now(UTC)
    horizon = now + timedelta(days=days_ahead)

    wanted: set[str] = set()
    for label, url in urls.items():
        res = httpx.get(url, timeout=30, follow_redirects=True)
        res.raise_for_status()
        for event in parse_events(res.text):
            start = event["start"]
            comparable = start if start.tzinfo else start.replace(tzinfo=UTC)
            if not (now - timedelta(days=1) <= comparable <= horizon):
                continue
            day = start.strftime("%Y-%m-%d")
            name = f"{day}-{slugify(event['summary'])}.md"
            wanted.add(name)
            attendees = ", ".join(event["attendees"]) or "(none listed)"
            body = (
                f"# {event['summary']}\n\n"
                f"- Calendar: {label}\n"
                f"- Start: {start.isoformat()}\n"
                + (f"- End: {event['end'].isoformat()}\n" if event.get("end") else "")
                + f"- Attendees: {attendees}\n"
            )
            (out_dir / name).write_text(body, encoding="utf-8")

    for stale in out_dir.glob("*.md"):
        if stale.name not in wanted:
            stale.unlink()
