"""Built-in tools: the hot path every brain gets without any plugin.

Every tool reads the caller's scope from ``cortex.scope`` — the dashboard
sets it per request, the CLI and MCP export leave it unrestricted. Paths in
and out of these tools are index keys ("vaults/shared/garden.md"), the same form
search results cite, so the model can chain search → read without
translation.

Remembered facts are brain-wide by design: a household or team brain wants
"the wifi password lives in the safe" visible to everyone. Do not remember
secrets you would not put in the shared vault.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from datetime import datetime
from typing import TYPE_CHECKING

from cortex import scope
from cortex.memory.indexer import scan_files
from cortex.memory.search import format_result, hybrid_search
from cortex.plugins import ToolPlugin, ToolRegistry

if TYPE_CHECKING:
    from cortex.brain import Brain

_MISSING_FILE = "No such file: {path}"


def register_builtin(registry: ToolRegistry, brain: Brain) -> None:
    def search_brain(query: str, k: int = 8) -> str:
        vector = brain.embed_query_sync(query)
        result = hybrid_search(
            brain.store,
            query,
            vector,
            k_files=k,
            now=time.time(),
            prefixes=scope.current_prefixes.get(),
        )
        return format_result(result, query)

    def grep_exact(pattern: str) -> str:
        prefixes = scope.current_prefixes.get()
        pairs = brain.config.root_pairs()
        if not pairs:
            return "The brain has no indexed directories yet."
        if prefixes is None and shutil.which("rg"):
            # rg prints filesystem paths, which only match index keys when
            # nothing is scoped away — so it serves the unrestricted caller
            # and the scoped one gets the (slower) key-aware scan.
            return _ripgrep(pattern, [str(r) for _, r in pairs])
        return _python_grep(pattern, pairs)

    def read_file(path: str, start_line: int = 1, num_lines: int = 200) -> str:
        # Out-of-scope, traversal, and missing paths all return the identical
        # message: existence is not leaked by wording.
        if not scope.allows_path(path):
            return _MISSING_FILE.format(path=path)
        target = brain.config.resolve_key(path)
        if target is None or not target.is_file():
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
        lines = [f"Brain: {config.name}"]
        for prefix, root in config.root_pairs():
            if not scope.allows_path(f"{prefix}/"):
                continue
            count = len(scan_files([(prefix, root)]))
            lines.append(f"- {prefix}/ — {count} indexable files")
        stats = brain.store.stats()
        lines.append(
            f"Index: {stats['files']} files, {stats['chunks']} chunks, "
            f"{stats['vectors']} vectors, {stats['facts']} remembered facts."
        )
        return "\n".join(lines)

    def remember(fact: str, source: str = "") -> str:
        fact = fact.strip()
        if not fact:
            return "Refusing to remember an empty fact."
        who = scope.current_user.get() or "owner"
        fact_id = brain.store.add_fact(fact, source or f"chat:{who}")
        return f"Remembered (fact #{fact_id}, visible to the whole brain): {fact}"

    def recall(query: str = "") -> str:
        rows = (
            brain.store.search_facts(query) if query.strip() else brain.store.recent_facts()
        )
        if not rows:
            return "No remembered facts match." if query else "Nothing has been remembered yet."
        return "\n".join(f"#{r['id']} [{r['created_at']}] {r['body']}" for r in rows)

    def current_time() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def _writable_vault() -> str:
        """Where this caller's writes land: their own vault in the
        dashboard, the shared vault for the box owner at the CLI."""
        user = scope.current_user.get()
        if user and (brain.config.vaults_dir / user).is_dir():
            return user
        return "shared"

    def capture_note(text: str, vault: str = "") -> str:
        from cortex.capture import append_note
        from cortex.vaults import VaultError

        target = vault or _writable_vault()
        if not scope.allows_path(f"vaults/{target}/"):
            return f"You cannot write to the {target} vault."
        who = scope.current_user.get()
        try:
            rel, line, _ = append_note(
                brain.config, target, text, source=f"via cortex{f' for {who}' if who else ''}"
            )
        except VaultError as exc:
            return f"Could not capture that: {exc}"
        brain.request_reindex()
        return f"Captured in vaults/{target}/{rel}:\n{line}"

    def complete_task(path: str, line: int) -> str:
        """Tick one markdown checkbox, addressed exactly as the digest and
        search report it, so the model cannot tick the wrong thing."""
        if not scope.allows_path(path):
            return _MISSING_FILE.format(path=path)
        target = brain.config.resolve_key(path)
        if target is None or not target.is_file():
            return _MISSING_FILE.format(path=path)
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        if not 1 <= line <= len(lines):
            return f"{path} has {len(lines)} lines; there is no line {line}."
        original = lines[line - 1]
        if "[ ]" not in original:
            return f"Line {line} of {path} is not an open task: {original.strip()!r}"
        lines[line - 1] = original.replace("[ ]", "[x]", 1)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        brain.request_reindex()
        return f"Done: {lines[line - 1].strip()}  ({path}:{line})"

    def clip_url(url: str, vault: str = "") -> str:
        from cortex import clip as clipper
        from cortex.vaults import VaultError

        target = vault or _writable_vault()
        if not scope.allows_path(f"vaults/{target}/"):
            return f"You cannot write to the {target} vault."
        try:
            clip = clipper.fetch(url)
            rel = clipper.save(brain.config, target, clip)
        except VaultError as exc:
            return f"Could not clip that: {exc}"
        brain.request_reindex()
        words = len(clip.text.split())
        return f"Saved {clip.title!r} ({words} words) to vaults/{target}/{rel}"

    def daily_digest() -> str:
        from cortex.digest import build_digest, format_digest

        return format_digest(
            build_digest(
                brain.config,
                brain.store,
                prefixes=scope.current_prefixes.get(),
                vault=_writable_vault(),
            )
        )

    registry.register(
        ToolPlugin(
            name="search_brain",
            description=(
                "Hybrid search (full-text + embeddings, rank-fused, recency-aware) over "
                "the vaults and sources this caller can read. Use this first for almost "
                "every question."
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
                "Exact literal search across readable files. Use this FIRST when the "
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
            description=(
                "Read a slice of a file by its index key, e.g. vaults/shared/garden.md or "
                "sources/calendar_ics/2026-09-01-standup.md — the same paths search cites."
            ),
            parameters={
                "path": {"type": "string", "description": "Index key path."},
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
            description="What this caller can read: vaults, sources, counts.",
            func=list_sources,
        )
    )
    registry.register(
        ToolPlugin(
            name="remember",
            description=(
                "Store a durable fact in long-term memory, visible to every user of this "
                "brain. Use for shared preferences, decisions, dates — never for one "
                "person's private secrets."
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
    registry.register(
        ToolPlugin(
            name="capture_note",
            description=(
                "Write a line into today's daily note. Use this whenever the user asks "
                "you to note, add, jot, remember-in-writing, or add to a list — it is "
                "the only way you can put something into a vault."
            ),
            parameters={
                "text": {"type": "string", "description": "One line to record."},
                "vault": {
                    "type": "string",
                    "description": "Vault name; defaults to the caller's own.",
                },
            },
            required=("text",),
            func=capture_note,
        )
    )
    registry.register(
        ToolPlugin(
            name="complete_task",
            description=(
                "Tick an open markdown task, using the exact path and line number that "
                "search or the digest reported for it."
            ),
            parameters={
                "path": {"type": "string", "description": "Index key, e.g. vaults/shared/x.md"},
                "line": {"type": "integer", "description": "1-based line of the task."},
            },
            required=("path", "line"),
            func=complete_task,
        )
    )
    registry.register(
        ToolPlugin(
            name="clip_url",
            description=(
                "Fetch a web page and save its readable text into the brain, so it can "
                "be searched later. Use when the user shares a link to keep."
            ),
            parameters={
                "url": {"type": "string", "description": "The http(s) URL to save."},
                "vault": {"type": "string", "description": "Vault; defaults to caller's."},
            },
            required=("url",),
            func=clip_url,
        )
    )
    registry.register(
        ToolPlugin(
            name="daily_digest",
            description=(
                "What is on today: upcoming events, open tasks, and what changed "
                "recently. Use for 'what's on', 'what should I do', 'catch me up'."
            ),
            func=daily_digest,
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


def _python_grep(pattern: str, pairs: list) -> str:
    needle = pattern.lower()
    hits: list[str] = []
    for key, path in scan_files(pairs).items():
        if not scope.allows_path(key):
            continue
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
