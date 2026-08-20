"""Built-in tools: the hot path every brain gets without any plugin."""

from __future__ import annotations

import shutil
import subprocess
import time
from datetime import datetime
from typing import TYPE_CHECKING

from cortex.memory.indexer import scan_files
from cortex.memory.search import format_result, hybrid_search
from cortex.plugins import ToolPlugin, ToolRegistry

if TYPE_CHECKING:
    from cortex.brain import Brain

_MISSING_FILE = "No such file: {path}"


def register_builtin(registry: ToolRegistry, brain: Brain) -> None:
    def search_brain(query: str, k: int = 8) -> str:
        vector = brain.embed_query_sync(query)
        result = hybrid_search(brain.store, query, vector, k_files=k, now=time.time())
        return format_result(result, query)

    def grep_exact(pattern: str) -> str:
        roots = brain.config.indexed_roots()
        if not roots:
            return "The brain has no indexed directories yet."
        if shutil.which("rg"):
            return _ripgrep(pattern, [str(r) for r in roots])
        return _python_grep(pattern, roots)

    def read_file(path: str, start_line: int = 1, num_lines: int = 200) -> str:
        root = brain.config.root.resolve()
        target = (root / path).resolve()
        # Out-of-root and missing paths return the identical message so the
        # tool leaks no existence information outside the brain.
        if not str(target).startswith(str(root) + "/"):
            return _MISSING_FILE.format(path=path)
        if not target.is_file():
            return _MISSING_FILE.format(path=path)
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, start_line)
        window = lines[start - 1 : start - 1 + max(1, num_lines)]
        if not window:
            return f"{path} has {len(lines)} lines; start_line {start} is past the end."
        numbered = [f"{start + i:>5} | {line}" for i, line in enumerate(window)]
        return f"{path} (lines {start}-{start + len(window) - 1} of {len(lines)}):\n" + "\n".join(
            numbered
        )

    def list_sources() -> str:
        config = brain.config
        lines = [f"Brain: {config.name} at {config.root}"]
        for root in config.indexed_roots():
            count = len(scan_files([root]))
            lines.append(f"- {root.name}/ — {count} indexable files")
        stats = brain.store.stats()
        lines.append(
            f"Index: {stats['files']} files, {stats['chunks']} chunks, "
            f"{stats['vectors']} vectors, {stats['facts']} remembered facts."
        )
        return "\n".join(lines)

    def remember(fact: str, source: str = "conversation") -> str:
        fact = fact.strip()
        if not fact:
            return "Refusing to remember an empty fact."
        fact_id = brain.store.add_fact(fact, source)
        return f"Remembered (fact #{fact_id}): {fact}"

    def recall(query: str = "") -> str:
        rows = (
            brain.store.search_facts(query) if query.strip() else brain.store.recent_facts()
        )
        if not rows:
            return "No remembered facts match." if query else "Nothing has been remembered yet."
        return "\n".join(f"#{r['id']} [{r['created_at']}] {r['body']}" for r in rows)

    def current_time() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    registry.register(
        ToolPlugin(
            name="search_brain",
            description=(
                "Hybrid search (full-text + embeddings, rank-fused, recency-aware) over "
                "everything in the brain: notes, connector sources, extra paths. "
                "Use this first for almost every question."
            ),
            parameters={
                "query": {"type": "string", "description": "What to look for."},
                "k": {"type": "integer", "description": "Max files to return (default 8)."},
            },
            required=("query",),
            func=search_brain,
        )
    )
    registry.register(
        ToolPlugin(
            name="grep_exact",
            description=(
                "Exact literal search across the brain's files. Use this FIRST when the "
                "user pastes an identifier, error message, or any literal string."
            ),
            parameters={"pattern": {"type": "string", "description": "Literal to find."}},
            required=("pattern",),
            func=grep_exact,
        )
    )
    registry.register(
        ToolPlugin(
            name="read_file",
            description="Read a slice of a file inside the brain, path relative to the brain root.",
            parameters={
                "path": {"type": "string", "description": "e.g. notes/projects/garden.md"},
                "start_line": {"type": "integer", "description": "1-based, default 1."},
                "num_lines": {"type": "integer", "description": "Default 200."},
            },
            required=("path",),
            func=read_file,
        )
    )
    registry.register(
        ToolPlugin(
            name="list_sources",
            description="What the brain contains: indexed directories, counts, fact count.",
            func=list_sources,
        )
    )
    registry.register(
        ToolPlugin(
            name="remember",
            description=(
                "Store a durable fact in long-term memory. Use when the user states "
                "something worth keeping (preferences, decisions, dates, names)."
            ),
            parameters={
                "fact": {"type": "string", "description": "One self-contained sentence."},
                "source": {"type": "string", "description": "Where it came from."},
            },
            required=("fact",),
            func=remember,
        )
    )
    registry.register(
        ToolPlugin(
            name="recall",
            description="Search remembered facts; with no query, list the most recent.",
            parameters={"query": {"type": "string", "description": "Optional filter."}},
            func=recall,
        )
    )
    registry.register(
        ToolPlugin(
            name="current_time",
            description="The current local date and time.",
            func=current_time,
        )
    )


def _ripgrep(pattern: str, roots: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["rg", "--fixed-strings", "-i", "--max-count", "3", "-n", "--max-filesize", "1M",
             "-g", "!.git", "-g", "!.cortex", pattern, *roots],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"grep failed: {exc}"
    if proc.returncode not in (0, 1):
        return f"grep failed: {proc.stderr.strip()[:300]}"
    out = proc.stdout.strip()
    if not out:
        return f"No exact matches for {pattern!r}."
    lines = out.splitlines()
    shown = lines[:40]
    tail = f"\n… {len(lines) - len(shown)} more matches not shown." if len(lines) > 40 else ""
    return "\n".join(shown) + tail


def _python_grep(pattern: str, roots: list) -> str:
    needle = pattern.lower()
    hits: list[str] = []
    for key, path in scan_files(roots).items():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if needle in line.lower():
                hits.append(f"{key}:{lineno}: {line.strip()[:200]}")
                if len(hits) >= 40:
                    break
        if len(hits) >= 40:
            break
    if not hits:
        return f"No exact matches for {pattern!r}."
    return "\n".join(hits)
