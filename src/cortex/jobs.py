"""Scheduled work: the clock that lets the brain act without being asked.

A job is a kind, an interval, and whatever settings that kind needs. Five
kinds ship:

* ``connector`` — re-sync one ingestion connector
* ``index``     — re-index, so "changed recently" stays honest
* ``rules``     — apply the tidying rules (or preview them)
* ``digest``    — write today's digest into a vault note
* ``channel_digest`` — post today's digest into a channel

Deliberately *not* a kind: "ask the model something and notify me". The
research on proactive assistants is one-sided — every large "AI decides
what you need today" product shipped since 2024 has been retired, and the
survivors are the ones where the user declared what they wanted. Every job
here is declared, deterministic, and produces a fact rather than an
opinion. A model-written briefing can be added later; it should not be the
thing that gets built first.

Intervals are hours, not cron. "Daily", "twice a day" and "hourly" are the
things people actually ask for, and an interval is legible in a panel where
a cron string is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

JOB_KINDS = ("connector", "index", "rules", "digest", "channel_digest")
MIN_INTERVAL_HOURS = 0.25


class JobError(ValueError):
    pass


@dataclass
class Job:
    name: str
    kind: str
    interval_hours: float = 24.0
    settings: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    last_run: str = ""
    last_status: str = ""
    last_detail: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "interval_hours": self.interval_hours,
            "settings": self.settings,
            "enabled": self.enabled,
            "last_run": self.last_run,
            "last_status": self.last_status,
            "last_detail": self.last_detail,
            "describes": self.describe(),
        }

    def describe(self) -> str:
        return f"{_kind_phrase(self)} {every(self.interval_hours)}"

    def due(self, now: datetime | None = None) -> bool:
        if not self.enabled:
            return False
        if not self.last_run:
            return True
        try:
            last = datetime.fromisoformat(self.last_run)
        except ValueError:
            return True
        now = now or datetime.now(UTC)
        return (now - last).total_seconds() >= self.interval_hours * 3600


def every(hours: float) -> str:
    """An interval in the words a person would use."""
    if hours < 1:
        return f"every {int(hours * 60)} minutes"
    if hours == 1:
        return "hourly"
    if hours == 12:
        return "twice a day"
    if hours < 24:
        return f"every {hours:g} hours"
    if hours == 24:
        return "daily"
    if hours == 168:
        return "weekly"
    return f"every {hours / 24:g} days"


def _kind_phrase(job: Job) -> str:
    settings = job.settings or {}
    if job.kind == "connector":
        return f"sync the {settings.get('connector', '?')} connector"
    if job.kind == "index":
        return "re-index the brain"
    if job.kind == "rules":
        mode = "preview" if settings.get("dry_run") else "apply"
        return f"{mode} the tidying rules"
    if job.kind == "digest":
        return f"write today's digest into {settings.get('vault', 'shared')}"
    if job.kind == "channel_digest":
        return f"post today's digest into #{settings.get('channel', 'general')}"
    return job.kind


def parse_job(raw: dict) -> Job:
    import re

    name = str(raw.get("name") or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9 _-]{0,47}", name, re.IGNORECASE):
        raise JobError("a job needs a short name (letters, digits, spaces, - or _)")
    kind = str(raw.get("kind") or "")
    if kind not in JOB_KINDS:
        raise JobError(f"unknown job kind {kind!r} (expected {', '.join(JOB_KINDS)})")
    try:
        interval = float(raw.get("interval_hours", 24))
    except (TypeError, ValueError) as exc:
        raise JobError("interval_hours must be a number") from exc
    if interval < MIN_INTERVAL_HOURS:
        raise JobError(f"the shortest interval is {MIN_INTERVAL_HOURS} hours")
    settings = dict(raw.get("settings") or {})
    if kind == "connector" and not str(settings.get("connector", "")).strip():
        raise JobError("a connector job needs which connector to run")
    if kind == "channel_digest" and not str(settings.get("channel", "")).strip():
        settings["channel"] = "general"
    return Job(
        name=name,
        kind=kind,
        interval_hours=interval,
        settings=settings,
        enabled=bool(raw.get("enabled", True)),
    )


def suggested_jobs() -> list[dict]:
    """What most brains want, switched off until someone says yes.

    Each carries the same ``describes`` sentence a saved job does, so no
    client has to reimplement describe() and drift from it.
    """
    return [_with_describes(raw) for raw in _SUGGESTED_JOBS]


def _with_describes(raw: dict) -> dict:
    try:
        return {**raw, "describes": parse_job(raw).describe()}
    except JobError:
        return raw


_SUGGESTED_JOBS: list[dict] = [
        {
            "name": "nightly tidy",
            "kind": "rules",
            "interval_hours": 24,
            "settings": {"dry_run": False},
            "enabled": False,
        },
        {
            "name": "refresh the calendar",
            "kind": "connector",
            "interval_hours": 6,
            "settings": {"connector": "calendar_ics"},
            "enabled": False,
        },
        {
            "name": "keep the index warm",
            "kind": "index",
            "interval_hours": 12,
            "settings": {},
            "enabled": False,
        },
        {
            "name": "morning digest in the channel",
            "kind": "channel_digest",
            "interval_hours": 24,
            "settings": {"channel": "general"},
            "enabled": False,
        },
    ]
