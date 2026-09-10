"""Load a brain's ``.env`` into the process environment.

Tokens for GitHub, GitLab, a search API, or a model provider are read from
environment variables (``api_key_env`` in cortex.yaml, ``token_env`` on a
repo). A file next to cortex.yaml is where a self-hoster keeps them: it
travels with the brain, it is gitignored here, and it is one place to look.

Twenty lines rather than python-dotenv: the format needed is ``KEY=value``,
``#`` comments, optional quotes, and "never override what the shell already
set" — which is the behaviour ``docker compose`` and every shell have, so a
variable exported before ``cortex serve`` still wins.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ENV_NAME = ".env"
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse_env(text: str) -> dict[str, str]:
    """``KEY=value`` pairs from a dotenv-style file. Quotes are stripped from
    a quoted value; an unquoted one keeps everything up to a ``#`` comment."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        out[key] = value
    return out


def load_env_file(path: Path) -> list[str]:
    """Set every variable in ``path`` that the environment does not already
    hold. Returns the names that were set, for a status readout."""
    if not path.is_file():
        return []
    try:
        pairs = parse_env(path.read_text(encoding="utf-8"))
    except OSError:
        return []
    added: list[str] = []
    for key, value in pairs.items():
        if key in os.environ:
            continue
        os.environ[key] = value
        added.append(key)
    return added
