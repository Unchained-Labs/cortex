"""Who this brain is for, as a file rather than a config string.

The persona used to be one line in ``cortex.yaml``: a thing you set once
during setup and never opened again. Identity deserves better, because it
is the part of the system that should get *more* accurate over time — who
lives here, how they like things done, what the household is currently
dealing with.

So it is ``identity.md`` at the brain root: a real note, editable in the
dashboard, read into every system prompt.

The agent may **propose** changes to it and may not make them. A system
that quietly rewrites its own instructions is one nobody can reason
about, and the failure is silent — you would never know which version
answered you. Proposals queue with the reason and the exact new text; a
human accepts or discards. That is the LangChain agent-inbox shape, and
it is the right one here: an accepted proposal is a labelled correction,
not a mystery diff.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from cortex.config import BrainConfig

IDENTITY_FILE = "identity.md"
MAX_IDENTITY_CHARS = 8000

STARTER = """\
# About this brain

Replace this with whatever the brain should always know. It is read into
every conversation, so keep it short and true — a page of stale detail is
worse than three accurate lines.

## Who it is for

-

## How we like things done

-

## What we are dealing with at the moment

-
"""


class IdentityError(ValueError):
    pass


@dataclass
class Proposal:
    id: int
    text: str
    reason: str
    created_at: str
    status: str = "pending"

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "reason": self.reason,
            "created_at": self.created_at,
            "status": self.status,
        }


def path(config: BrainConfig):
    return config.root / IDENTITY_FILE


def read(config: BrainConfig) -> str:
    """The identity file, or "" when there is none."""
    target = path(config)
    if not target.is_file():
        return ""
    return target.read_text(encoding="utf-8", errors="replace")


def write(config: BrainConfig, text: str) -> str:
    if len(text) > MAX_IDENTITY_CHARS:
        raise IdentityError(
            f"identity is {len(text)} characters; keep it under {MAX_IDENTITY_CHARS}. "
            "It is read into every conversation, so length costs you on every turn."
        )
    target = path(config)
    target.write_text(text, encoding="utf-8")
    return text


def ensure(config: BrainConfig) -> bool:
    """Create the starter file if there is none. True if it wrote one."""
    target = path(config)
    if target.exists():
        return False
    target.write_text(STARTER, encoding="utf-8")
    return True


def effective(config: BrainConfig) -> str:
    """What actually goes into the prompt.

    The file wins; the old ``persona:`` string is still honoured so an
    existing cortex.yaml keeps working, and both appear when both exist —
    dropping someone's configured persona on upgrade would be the rude
    way to do this.
    """
    parts = []
    persona = (config.persona or "").strip()
    if persona:
        parts.append(persona)
    body = read(config).strip()
    if body and body.strip() != STARTER.strip():
        parts.append(body)
    return "\n\n".join(parts)


def is_untouched(config: BrainConfig) -> bool:
    """True when the file is still exactly the starter text — used to keep
    a placeholder out of the prompt and to nudge in the UI."""
    body = read(config).strip()
    return not body or body == STARTER.strip()


def summarise(text: str, limit: int = 120) -> str:
    """A one-line gist, for listing a proposal next to what it replaces."""
    flat = " ".join(re.sub(r"^#+\s*", "", text, flags=re.M).split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
