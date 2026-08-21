"""Capture: getting a thought into the brain in one step.

A brain you have to organize before you can write to is a brain you stop
writing to. Capture appends a timestamped line to today's daily note,
creating it if needed, and that is the whole ceremony. Filing can happen
later, or never — search does not care which file a line is in.

Daily notes live at ``journal/YYYY-MM-DD.md`` inside a vault. The same
function backs the dashboard's capture box, the ``cortex note`` command,
and the agent's ``capture_note`` tool, so all three land identically.
"""

from __future__ import annotations

import threading
from datetime import date, datetime

from cortex.config import BrainConfig
from cortex.vaults import VaultError, vault_path

JOURNAL_DIR = "journal"

# Appending is read-modify-write, and four surfaces can do it at once (the
# dashboard, the CLI, the agent's tool, a connector). Unserialized, two
# concurrent captures read the same body and the second overwrites the
# first — a silently lost thought, which is the one failure this feature
# cannot have.
_APPEND_LOCK = threading.Lock()


def daily_note_path(day: date | None = None) -> str:
    """Vault-relative path of a day's note."""
    return f"{JOURNAL_DIR}/{(day or date.today()).isoformat()}.md"


def _header(day: date) -> str:
    # "Thursday 21 August 2026" reads better than the ISO name in the body
    return f"# {day.strftime('%A %-d %B %Y')}\n\n"


def append_note(
    config: BrainConfig,
    vault: str,
    text: str,
    source: str = "",
    when: datetime | None = None,
) -> tuple[str, str, int]:
    """Append one captured line to today's daily note.

    Returns (vault-relative path, the line written, its 1-based line
    number). Raises VaultError for an unknown vault or empty text.
    """
    text = " ".join(text.split())  # a captured thought is one line
    if not text:
        raise VaultError("nothing to capture")
    stamp = when or datetime.now()
    rel = daily_note_path(stamp.date())
    target = vault_path(config, vault, rel)
    suffix = f"  _{source}_" if source else ""
    line = f"- **{stamp.strftime('%H:%M')}** {text}{suffix}"
    with _APPEND_LOCK:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text(_header(stamp.date()), encoding="utf-8")
        body = target.read_text(encoding="utf-8")
        if body and not body.endswith("\n"):
            body += "\n"
        target.write_text(f"{body}{line}\n", encoding="utf-8")
    return rel, line, len(f"{body}{line}".splitlines())


def read_daily_note(config: BrainConfig, vault: str, day: date | None = None) -> str:
    """Today's note, or an empty string when nothing has been captured."""
    try:
        target = vault_path(config, vault, daily_note_path(day))
    except VaultError:
        return ""
    if not target.is_file():
        return ""
    return target.read_text(encoding="utf-8", errors="replace")
