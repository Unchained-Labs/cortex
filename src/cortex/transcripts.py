"""Reading the agent transcripts already on this machine.

## Why this is not transcripts-mcp

Stormix/transcripts-mcp is the obvious answer and does more than this: Cursor
and Codex as well as Claude Code, a search index, semantic ranking. Three facts
argued against it *here*, and none of them are criticisms of it:

* This box has Claude Code transcripts and nothing else — no Cursor, no Codex —
  so its main advantage is unused.
* It is a stdio server needing Node 24. cortex's runtime image is Python, the
  host runs Node 22, and a stdio child must live inside the container, so
  adopting it means putting a second language runtime in this image.
* `npx -y transcripts-mcp` resolves an unpinned package at run time and gives it
  read access to every transcript on the machine. Those files are 0600 because
  they contain every prompt typed and every file read on this box, and the same
  container holds the vault.

If Cursor or Codex ever land here, or the search index becomes worth it, the MCP
is the right call and this module is a hundred and fifty lines to delete.

## What a transcript is

Claude Code writes one JSONL file per session under
`<config>/projects/<slug>/<session-uuid>.jsonl`, one JSON object per line. The
shape has changed across versions and will change again, so every field is read
defensively: a line that does not parse is skipped rather than failing the read,
because one bad line in a 90 MB file must not cost the other 40,000.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

#: Transcripts are large — some are tens of megabytes — and a tool result is
#: read by a model. These caps are what keep a search from becoming a context
#: overflow rather than an answer.
MAX_FILE_BYTES = 200 * 1024 * 1024
SNIPPET = 240
DEFAULT_LIMIT = 20


def roots(config_dir: str | os.PathLike[str] | None = None) -> list[Path]:
    """Where Claude Code keeps its sessions.

    CLAUDE_CONFIG_DIR may hold several paths separated by the platform
    delimiter — that is how one machine drives several accounts — so it is
    split rather than treated as a single directory.
    """
    if config_dir:
        candidates = [Path(config_dir)]
    else:
        raw = os.environ.get("CLAUDE_CONFIG_DIR", "")
        candidates = (
            [Path(p) for p in raw.split(os.pathsep) if p.strip()]
            if raw else [Path.home() / ".claude"]
        )
    return [c / "projects" for c in candidates if (c / "projects").is_dir()]


@dataclass
class Session:
    project: str
    session_id: str
    path: Path
    modified: float
    size: int

    @property
    def when(self) -> str:
        return datetime.fromtimestamp(self.modified, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def sessions(config_dir: str | None = None, project: str = "", limit: int = 200) -> list[Session]:
    """Every session file, newest first."""
    found: list[Session] = []
    for root in roots(config_dir):
        for slug in sorted(root.iterdir()):
            if not slug.is_dir():
                continue
            if project and project.lower() not in slug.name.lower():
                continue
            for f in slug.glob("*.jsonl"):
                try:
                    st = f.stat()
                except OSError:
                    continue
                found.append(Session(slug.name, f.stem, f, st.st_mtime, st.st_size))
    found.sort(key=lambda s: s.modified, reverse=True)
    return found[:limit]


def _text_of(obj: dict) -> tuple[str, str]:
    """(role, text) for one transcript line, across the shapes it has had.

    Returns ("", "") for anything that is not a human or assistant turn — tool
    results, metadata, summaries. Those are the bulk of the file and none of
    them are what "did we talk about this" means.
    """
    kind = obj.get("type") or ""
    if kind not in {"user", "assistant"}:
        return "", ""
    message = obj.get("message")
    if not isinstance(message, dict):
        return "", ""
    role = str(message.get("role") or kind)
    content = message.get("content")
    if isinstance(content, str):
        return role, content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return role, "\n".join(p for p in parts if p)
    return role, ""


def read_lines(path: Path):
    """Yield parsed objects, skipping anything that does not parse.

    One malformed line in a 90 MB transcript must not cost the other forty
    thousand, and half-written lines are normal: a session that is still running
    is being appended to while this reads it.
    """
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            logger.warning("transcript %s is over the size cap — skipped", path)
            return
    except OSError:
        return
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError as exc:
        logger.warning("could not read transcript %s: %s", path, exc)


def search(query: str, config_dir: str | None = None, project: str = "",
           limit: int = DEFAULT_LIMIT, role: str = "") -> list[dict]:
    """Plain substring search across turns, newest session first.

    Substring rather than an index, deliberately: the corpus here is one
    machine's history, the answer wanted is "have we discussed this", and an
    index is a second thing to build, keep fresh and be wrong about.
    """
    needle = (query or "").strip().lower()
    if not needle:
        return []
    hits: list[dict] = []
    for sess in sessions(config_dir, project):
        for n, obj in enumerate(read_lines(sess.path), start=1):
            who, text = _text_of(obj)
            if not text or (role and who != role):
                continue
            spot = text.lower().find(needle)
            if spot < 0:
                continue
            start = max(0, spot - SNIPPET // 3)
            hits.append({
                "project": sess.project,
                "session": sess.session_id,
                "when": sess.when,
                "role": who,
                "line": n,
                "text": ("…" if start else "") + text[start:start + SNIPPET].strip()
                        + ("…" if len(text) > start + SNIPPET else ""),
            })
            if len(hits) >= limit:
                return hits
    return hits


def transcript(session_id: str, config_dir: str | None = None,
               limit: int = 200, offset: int = 0) -> dict:
    """The human and assistant turns of one session."""
    for sess in sessions(config_dir):
        if sess.session_id != session_id:
            continue
        turns = []
        for obj in read_lines(sess.path):
            who, text = _text_of(obj)
            if text:
                turns.append({"role": who, "text": text})
        window = turns[offset:offset + limit]
        return {
            "project": sess.project, "session": sess.session_id, "when": sess.when,
            "turns": window, "total_turns": len(turns),
            "truncated": offset + len(window) < len(turns),
        }
    return {"error": f"no session {session_id!r} — list them with transcript_sessions"}
