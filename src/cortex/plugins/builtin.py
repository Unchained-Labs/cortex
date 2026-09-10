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

import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
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

    def remember(fact: str, source: str = "", kind: str = "", subject: str = "") -> str:
        from cortex.memory import facts as factsmod

        fact = fact.strip()
        if not fact:
            return "Refusing to remember an empty fact."
        try:
            kind = factsmod.normalise_kind(kind)
        except factsmod.MemoryError as exc:
            return str(exc)
        subject = factsmod.normalise_subject(subject) or (
            factsmod.guess_subject(fact) if kind == "person" else ""
        )
        who = scope.current_user.get() or "owner"
        fact_id = brain.store.add_fact(
            fact, source or f"chat:{who}", kind=kind, subject=subject
        )
        about = f" about {subject}" if subject else ""
        return (
            f"Remembered as a {kind}{about} (#{fact_id}, visible to the whole brain): {fact}"
        )

    def _to_memories(rows) -> list:
        from cortex.memory.facts import Memory

        return [
            Memory(
                id=r["id"],
                kind=r["kind"] if "kind" in r.keys() else "fact",
                subject=r["subject"] if "subject" in r.keys() else "",
                body=r["body"],
                source=r["source"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def recall(query: str = "", kind: str = "") -> str:
        from cortex.memory import facts as factsmod

        if kind:
            try:
                rows = brain.store.facts_by_kind(factsmod.normalise_kind(kind))
            except factsmod.MemoryError as exc:
                return str(exc)
        elif query.strip():
            rows = brain.store.search_facts(query)
        else:
            rows = brain.store.facts_by_kind()
        return factsmod.format_memories(_to_memories(rows), query)

    def recall_about(subject: str) -> str:
        """Everything known about one person, project or thing."""
        from cortex.memory import facts as factsmod

        if not subject.strip():
            return "Which person or thing?"
        rows = brain.store.facts_about(subject)
        return factsmod.format_memories(_to_memories(rows), subject)

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

    def write_note(path: str, text: str, mode: str = "replace", vault: str = "") -> str:
        """Create or update a whole document in a vault.

        capture_note appends one line to today's journal, which is right for
        "note that down" and useless for building documentation: an agent asked
        to document an app has nowhere to put a structured page, and a model
        given a goal it has no tool for does not report that — it reports
        success. That is exactly what happened the first time a worker was
        pointed at this: it announced a note at apps/jinsen.md that was never
        written, because nothing could have written it.

        `append` exists so a long document can be built across several runs
        without re-sending what is already there, and so two runs extending the
        same page do not silently drop each other's work.
        """
        from cortex.vaults import VaultError, read_file, write_file

        target = vault or _writable_vault()
        if not scope.allows_path(f"vaults/{target}/"):
            return f"You cannot write to the {target} vault."
        rel = path.strip().lstrip("/")
        # Accept the index key form the read tools hand back, so a model can
        # round-trip a path it was just given instead of having to strip it.
        prefix = f"vaults/{target}/"
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
        if not rel:
            return "Give a path inside the vault, e.g. apps/jinsen.md"
        if ".." in Path(rel).parts:
            return "Paths cannot climb out of the vault."
        if mode not in {"replace", "append"}:
            return "mode must be 'replace' or 'append'."

        existing = ""
        try:
            existing = read_file(brain.config, target, rel)[0]
        except (FileNotFoundError, VaultError):
            existing = ""

        if mode == "append" and existing:
            body = existing.rstrip("\n") + "\n\n" + text.strip() + "\n"
        else:
            body = text.strip() + "\n"

        # An agent may not tick a box under reviews/.
        #
        # A ticked checkbox there means "a human authorised an agent to act on
        # this", and it is the only thing standing between a review and an
        # agent changing code unattended. The reviewing worker is TOLD to leave
        # every box empty; it wrote nine of them ticked anyway, with an empty
        # worklog proving nobody had approved a thing. A rule that exists only
        # as an instruction is one the model reasons its way around, so the
        # rule lives here instead.
        #
        # Scoped to reviews/ because that is where the tick carries authority.
        # Elsewhere a checkbox is an ordinary to-do and ticking it is fine.
        normalised = 0
        if rel.startswith("reviews/"):
            body, normalised = _untick(body)

        try:
            write_file(brain.config, target, rel, body, create=not existing)
        except VaultError as exc:
            return f"Could not write that: {exc}"
        brain.request_reindex()
        verb = "Appended to" if (mode == "append" and existing) else (
            "Updated" if existing else "Created")
        lines = body.count("\n")
        note = ""
        if normalised:
            # Said out loud, so the agent learns the rule rather than believing
            # it wrote something it did not.
            note = (
                f" {normalised} ticked checkbox(es) were reset to unticked: under "
                "reviews/, a tick means a human approved that item for automated "
                "work, so only a person may set one."
            )
        return f"{verb} vaults/{target}/{rel} ({lines} lines).{note}"

    def approved_findings(limit: int = 20) -> str:
        """Findings a human has approved for automated work, already parsed.

        This exists because asking a model to FIND them did not work. The brief
        said "grep_exact for '- [x]' under reviews/", which is two failure modes
        stacked: ripgrep parsed the leading dash as a flag and returned an error
        the caller read as "no matches", and once that was fixed the model still
        short-circuited to the brief's "nothing approved, stop" branch rather
        than constructing a bracket pattern correctly.

        Both are the same lesson. A step that must happen every run should be a
        tool call with no arguments to get wrong, not a sentence hoping the
        model builds the right regex. Fifteen approved findings sat unworked
        for days behind that.

        Returns the file, line and title of each, so the caller can read the
        review for context and record the work against a stable key.
        """
        import re as _re

        reviews = brain.config.shared_vault / "reviews"
        if not reviews.is_dir():
            return "No reviews/ directory yet — nothing has been reviewed."

        pattern = _re.compile(
            r"^\s*[-*]\s*\[[xX]\]\s*\*\*(?P<id>[A-Za-z]+\d+)\s*[·.\-]\s*(?P<title>[^*]+?)\*\*(?P<rest>.*)$"
        )
        done = ""
        worklog = reviews / "_worklog.md"
        if worklog.is_file():
            done = worklog.read_text(encoding="utf-8", errors="replace")

        out: list[str] = []
        for path in sorted(reviews.glob("*.md"), reverse=True):
            if path.name.startswith("_"):
                continue
            key_base = f"reviews/{path.name}"
            for n, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
            ):
                m = pattern.match(line)
                if not m:
                    continue
                key = f"{key_base}#{m.group('id')}"
                # Already worked. The worklog is the record of what was done, so
                # an item in it is finished whatever its box still says — the
                # tick is the human's approval and is never removed.
                #
                # Matched as the bold key record_work writes, not as a
                # substring: "…#F1" is a prefix of "…#F10", and a plain `in`
                # test made the tenth finding vanish the moment the first was
                # done. The first scheduled review caught it (F1 of
                # reviews/cortex-2026-09-10.md).
                if f"**{key}**" in done:
                    continue
                rest = m.group("rest").strip(" —-·")
                out.append(
                    f"{key} (line {n}) — {m.group('title').strip()}"
                    f"{' — ' + rest if rest else ''}"
                )
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break

        if not out:
            return (
                "No approved findings outstanding. Either nothing is ticked, or "
                "everything ticked is already in reviews/_worklog.md."
            )
        return (
            f"{len(out)} approved finding(s) waiting, highest-priority first:\n"
            + "\n".join(out)
        )

    def transcript_sessions(project: str = "", limit: int = 20) -> str:
        """Agent coding sessions on this machine, newest first."""
        from cortex import transcripts

        rows = transcripts.sessions(project=project, limit=max(1, min(limit, 200)))
        if not rows:
            return ("No transcripts found. Claude Code writes them under "
                    "<config>/projects/; set CLAUDE_CONFIG_DIR if they live elsewhere.")
        return "\n".join(
            f"{s.when}  {s.project}  {s.session_id}  ({s.size // 1024} KB)" for s in rows
        )

    def transcript_search(query: str, project: str = "", limit: int = 20,
                          role: str = "") -> str:
        """Have we discussed this before, and where."""
        from cortex import transcripts

        hits = transcripts.search(query, project=project,
                                  limit=max(1, min(limit, 100)), role=role)
        if not hits:
            # A clean "no" is the useful half of this tool: it is what lets the
            # agent say "we have not covered this" instead of guessing.
            return f"Nothing in any transcript matches {query!r}."
        out = [f"{len(hits)} match(es) for {query!r}:"]
        for h in hits:
            out.append(f"- {h['when']} · {h['project']} · {h['role']} · session {h['session']}\n"
                       f"    {h['text']}")
        return "\n".join(out)

    def transcript_read(session: str, limit: int = 60, offset: int = 0) -> str:
        """The conversation of one session, by its id."""
        from cortex import transcripts

        data = transcripts.transcript(session, limit=max(1, min(limit, 400)), offset=offset)
        if "error" in data:
            return data["error"]
        head = (f"{data['project']} · {data['session']} · {data['when']} · "
                f"{data['total_turns']} turns")
        body = "\n\n".join(
            f"[{t['role']}] {t['text'][:1500]}" for t in data["turns"]
        )
        tail = ("\n\n… more turns; call again with a higher offset."
                if data["truncated"] else "")
        return f"{head}\n\n{body}{tail}"

    def queue_post(channel: str, title: str, body: str, when: str = "",
                   hook: str = "", source: str = "", vault: str = "") -> str:
        """Put a draft post in the queue for a human to approve.

        Writes a file. It does not publish, and there is no credential in this
        brain — approval and publishing are separate steps on purpose.
        """
        from datetime import date as _date

        from cortex import social
        from cortex.vaults import VaultError, read_file, write_file

        target = vault or _writable_vault()
        if not scope.allows_path(f"vaults/{target}/"):
            return f"You cannot write to the {target} vault."
        when = (when or _date.today().isoformat()).strip()
        problem = social.validate(when, channel, title, body)
        if problem:
            return f"Not queued: {problem}."

        try:
            existing = read_file(brain.config, target, social.QUEUE)[0]
            fresh = False
        except (FileNotFoundError, VaultError):
            # A separate flag, not `existing.strip()`: the default header below
            # is non-empty, so testing the text said "this file exists" for a
            # file that does not and write_file refused to create it.
            existing = "# Post queue\n\nTick a post to approve it for publishing.\n"
            fresh = True

        entry = social.render(when, channel.strip().lower(), title.strip(),
                              hook.strip(), body.strip(), source.strip())
        # Newest first: the queue is read top-down by a person deciding what
        # goes out this week, and the oldest draft is the least likely answer.
        head, _, rest = existing.partition("\n\n")
        body_text = f"{head}\n\n{entry}\n\n{rest.lstrip()}".rstrip() + "\n"
        try:
            write_file(brain.config, target, social.QUEUE, body_text, create=fresh)
        except VaultError as exc:
            return f"Could not write the queue: {exc}"
        brain.request_reindex()
        return (f"Queued for {channel.strip().lower()} on {when}: {title.strip()!r}. "
                f"It is unticked in vaults/{target}/{social.QUEUE} — nothing publishes "
                "until a human approves it.")

    def post_queue(only: str = "") -> str:
        """What is queued, and what has been approved.

        `only`: 'approved' | 'pending' | '' for everything.
        """
        from cortex import social
        from cortex.vaults import VaultError, read_file

        target = _writable_vault()
        try:
            text = read_file(brain.config, target, social.QUEUE)[0]
        except (FileNotFoundError, VaultError):
            return "The post queue is empty. Add one with queue_post."

        rows = social.parse(text)
        if only == "approved":
            rows = [r for r in rows if r["approved"]]
        elif only == "pending":
            rows = [r for r in rows if not r["approved"]]
        if not rows:
            return f"No {only or ''} posts in the queue.".replace("  ", " ")

        out = []
        for r in rows:
            mark = "APPROVED" if r["approved"] else "pending "
            out.append(f"[{mark}] {r['date']} · {r['channel']} · {r['title']} (line {r['line']})")
            if r["body"]:
                out.append(f"          {r['body'][:220]}")
        return "\n".join(out)

    def record_work(key: str, outcome: str, summary: str, branch: str = "",
                    tests: str = "") -> str:
        """Write one worklog entry. The thing that stops a finding being redone.

        A tool rather than an instruction to append markdown, for the same
        reason `approved_findings` is a tool: the agent reported "I've
        documented this in the worklog" on a run where the worklog stayed a
        single heading line. It was not lying so much as composing — an append
        block with a key, a date and four labelled fields is a formatting task,
        and a formatting task the model believes it completed is indistinguish-
        able from one it did. Next run would have redone the same finding.

        Here there is nothing to format. The entry either exists afterwards or
        the call failed.
        """
        from datetime import date as _date

        from cortex.vaults import VaultError, read_file, write_file

        allowed = {"done", "already-fixed", "needs-a-human", "blocked"}
        outcome = (outcome or "").strip().lower()
        if outcome not in allowed:
            return f"outcome must be one of {', '.join(sorted(allowed))}"
        key = (key or "").strip()
        if "#" not in key:
            # The key is what dedupe matches on next run. A vague one silently
            # fails to match and the work repeats forever.
            return ("key must be the one approved_findings gave you, "
                    "e.g. reviews/code-2026-09-06.md#F1")
        if len((summary or "").strip()) < 10:
            return "say what actually happened, in a sentence"

        # "already-fixed" is the cheapest wrong answer available and the most
        # expensive one to be wrong about: it removes a finding from the queue
        # permanently without anybody doing the work.
        #
        # The first agent to reach this claimed a MinIO finding was already
        # fixed because "neither compose file is present in the clankergram
        # repo". Both were present, and visible to it. It had not looked.
        #
        # So that one outcome has to carry proof: a command and what it printed,
        # or a file:line somebody else can open. Not a sentence asserting the
        # check happened.
        # "done" means code changed, and the brief requires that change to be on
        # a branch. Recording done WITHOUT one is self-contradictory: if no
        # branch was made, no work was committed.
        #
        # This is not hypothetical. Two consecutive runs recorded 'done' with
        # branch "—" and prose describing a fix. No agent/ branch existed, the
        # working tree was clean, and the thing the finding was about was
        # untouched. The reply even named a branch the worklog did not have —
        # the model narrated the work rather than doing it, and the queue
        # advanced anyway.
        if outcome == "done" and not (branch or "").strip():
            return (
                "'done' needs the branch you made. The brief requires the change to live "
                "on agent/<review>-<id>, so 'done' with no branch means nothing was "
                "committed. If you could not make one, record 'blocked' or "
                "'needs-a-human' — those are honest outcomes and they get retried."
            )

        if outcome == "already-fixed":
            proof = (tests or "").strip()
            if len(proof) < 20:
                return (
                    "'already-fixed' needs evidence in `tests`: the command you ran and "
                    "what it printed, or a file:line a person can open. Claiming a thing "
                    "is gone without looking removes it from the queue for good. If you "
                    "could not check, record 'blocked' instead."
                )

        target = _writable_vault()
        path = "reviews/_worklog.md"
        try:
            existing = read_file(brain.config, target, path)[0]
            fresh = False
        except (FileNotFoundError, VaultError):
            existing = "# Worklog\n"
            fresh = True

        entry = (
            f"\n- **{key}** — {_date.today().isoformat()}\n"
            f"      **Outcome:** {outcome}\n"
            f"      **Branch:** {branch.strip() or '—'}\n"
            f"      **What changed:** {summary.strip()}\n"
            f"      **Tests:** {tests.strip() or 'none run'}\n"
        )
        try:
            write_file(brain.config, target, path,
                       existing.rstrip("\n") + "\n" + entry, create=fresh)
        except VaultError as exc:
            return f"Could not write the worklog: {exc}"
        brain.request_reindex()
        return (f"Recorded {key} as {outcome}. approved_findings will not offer "
                "it again.")

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

    def propose_identity_change(text: str, reason: str) -> str:
        from cortex import identity as identitymod

        text = text.strip()
        if not text:
            return "A proposal needs the full replacement text."
        if len(text) > identitymod.MAX_IDENTITY_CHARS:
            return (
                f"That is {len(text)} characters; identity is read into every "
                f"conversation, so keep it under {identitymod.MAX_IDENTITY_CHARS}."
            )
        if not reason.strip():
            return (
                "Say why the change is worth making — a proposal without a "
                "reason is not reviewable."
            )
        proposal_id = brain.store.add_identity_proposal(text, reason.strip())
        return (
            f"Proposed (#{proposal_id}). It changes nothing until somebody accepts it "
            "in the dashboard — tell the user it is waiting for them."
        )

    def read_identity() -> str:
        from cortex import identity as identitymod

        body = identitymod.read(brain.config).strip()
        return body or "There is no identity note yet."

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
                "kind": {
                    "type": "string",
                    "description": (
                        "person, project, preference, goal, or fact. Use person for who "
                        "someone is or how to reach them, project for something ongoing, "
                        "preference for how this household likes things done."
                    ),
                },
                "subject": {
                    "type": "string",
                    "description": "Who or what it is about, e.g. a name.",
                },
                "source": {"type": "string", "description": "Where it came from."},
            },
            required=("fact",),
            func=remember,
        )
    )
    registry.register(
        ToolPlugin(
            name="recall",
            description=(
                "What the brain remembers, grouped by kind. Filter with a query, or "
                "with kind=person/project/preference/goal/fact."
            ),
            parameters={
                "query": {"type": "string", "description": "Optional search."},
                "kind": {"type": "string", "description": "Optional kind filter."},
            },
            func=recall,
        )
    )
    registry.register(
        ToolPlugin(
            name="recall_about",
            description=(
                "Everything remembered about one person, project or thing. Use this "
                "when the user names someone — it beats searching prose."
            ),
            parameters={"subject": {"type": "string", "description": "A name or topic."}},
            required=("subject",),
            func=recall_about,
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
            name="write_note",
            description=(
                "Create or replace a whole document in a vault, e.g. apps/jinsen.md. "
                "Use this for anything structured — documentation, a reference page, a "
                "review — where capture_note's single journal line is the wrong shape. "
                "mode='append' adds to the end of an existing page instead of replacing "
                "it, which is how a document grows across several sessions."
            ),
            parameters={
                "path": {
                    "type": "string",
                    "description": "Path inside the vault, e.g. apps/jinsen.md. Markdown only.",
                },
                "text": {"type": "string", "description": "The document body, in markdown."},
                "mode": {
                    "type": "string",
                    "enum": ["replace", "append"],
                    "description": "replace (default) writes the whole file; append adds to it.",
                },
                "vault": {
                    "type": "string",
                    "description": "Vault name; defaults to the caller's own.",
                },
            },
            required=("path", "text"),
            func=write_note,
        )
    )
    registry.register(
        ToolPlugin(
            name="approved_findings",
            description=(
                "Findings a human has ticked for automated work, already parsed and "
                "with anything recorded in reviews/_worklog.md filtered out. Call this "
                "FIRST when doing review work — it is the list, so there is no search "
                "to get wrong. An empty result means nothing is approved, which is a "
                "normal outcome."
            ),
            parameters={
                "limit": {"type": "integer", "description": "Most to return (default 20)."},
            },
            required=(),
            func=approved_findings,
        )
    )
    registry.register(
        ToolPlugin(
            name="transcript_search",
            description=(
                "Search past agent coding sessions on this machine for a phrase. Use this "
                "BEFORE answering anything that starts 'did we', 'have we', 'what did we "
                "decide about', or when you are about to propose something that may have "
                "been tried already — a clean 'nothing matches' is a real answer and is "
                "the point of the tool."
            ),
            parameters={
                "query": {"type": "string", "description": "Phrase to look for."},
                "project": {"type": "string", "description": "Filter by project slug."},
                "role": {"type": "string", "description": "'user' or 'assistant'."},
                "limit": {"type": "integer", "description": "Max matches (default 20)."},
            },
            required=("query",),
            func=transcript_search,
        )
    )
    registry.register(
        ToolPlugin(
            name="transcript_sessions",
            description="List agent coding sessions on this machine, newest first.",
            parameters={
                "project": {"type": "string", "description": "Filter by project slug."},
                "limit": {"type": "integer", "description": "Max sessions (default 20)."},
            },
            required=(),
            func=transcript_sessions,
        )
    )
    registry.register(
        ToolPlugin(
            name="transcript_read",
            description=(
                "Read one past session's conversation by id, from transcript_search or "
                "transcript_sessions. Paginated — transcripts run to thousands of turns."
            ),
            parameters={
                "session": {"type": "string", "description": "Session id (a uuid)."},
                "limit": {"type": "integer", "description": "Turns to return (default 60)."},
                "offset": {"type": "integer", "description": "Turns to skip."},
            },
            required=("session",),
            func=transcript_read,
        )
    )
    registry.register(
        ToolPlugin(
            name="queue_post",
            description=(
                "Draft a social post into the queue for a human to approve. Use this when "
                "asked to write a weekly post, an update, or anything for LinkedIn, X, "
                "Clankergram, the blog or the newsletter. Build it from what the brain "
                "actually knows — a review finding, a documented app, something in the "
                "journal — and put that in `source` so the claim is traceable. It queues "
                "a draft and publishes nothing."
            ),
            parameters={
                "channel": {"type": "string", "enum": list(__import__(
                    "cortex.social", fromlist=["CHANNELS"]).CHANNELS),
                    "description": "Where it would go."},
                "title": {"type": "string", "description": "Recognisable in a list."},
                "body": {"type": "string", "description": "The post itself, as published."},
                "when": {"type": "string", "description": "YYYY-MM-DD; today if omitted."},
                "hook": {"type": "string", "description": "The opening line."},
                "source": {"type": "string",
                           "description": "Vault path or finding key it came from."},
                "vault": {"type": "string",
                          "description": "Vault name; the caller's own by default."},
            },
            required=("channel", "title", "body"),
            func=queue_post,
        )
    )
    registry.register(
        ToolPlugin(
            name="post_queue",
            description=(
                "What is in the post queue. `only`: 'approved' for what a human has "
                "ticked, 'pending' for what is still waiting, blank for everything."
            ),
            parameters={"only": {"type": "string", "enum": ["", "approved", "pending"],
                                 "description": "Filter."}},
            required=(),
            func=post_queue,
        )
    )
    registry.register(
        ToolPlugin(
            name="record_work",
            description=(
                "Record what you did about an approved finding. Call this at the END of "
                "every review-work run, whatever the outcome — including 'blocked' and "
                "'needs-a-human'. It is what stops the next run redoing the same finding, "
                "so a run that changed nothing still has to call it."
            ),
            parameters={
                "key": {"type": "string",
                        "description": "Exactly the key approved_findings gave you."},
                "outcome": {"type": "string",
                            "enum": ["done", "already-fixed", "needs-a-human", "blocked"],
                            "description": "What happened."},
                "summary": {"type": "string", "description": "What changed, in a sentence."},
                "branch": {"type": "string", "description": "Branch name, if you made one."},
                "tests": {"type": "string", "description": "What you ran and what it said."},
            },
            required=("key", "outcome", "summary"),
            func=record_work,
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
            name="read_identity",
            description=(
                "The brain's identity note — who it is for and how they like things "
                "done. Read it before proposing a change to it."
            ),
            func=read_identity,
        )
    )
    registry.register(
        ToolPlugin(
            name="propose_identity_change",
            description=(
                "Propose a rewrite of the identity note for a human to accept or "
                "discard. You cannot change it yourself. Use this when the user tells "
                "you something that should always be true, not just today. Pass the "
                "COMPLETE new text, not a fragment."
            ),
            parameters={
                "text": {"type": "string", "description": "The complete replacement."},
                "reason": {"type": "string", "description": "Why it is worth changing."},
            },
            required=("text", "reason"),
            func=propose_identity_change,
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


_TICKED = re.compile(r"^(\s*[-*]\s*)\[[xX]\]", re.MULTILINE)


def _untick(body: str) -> tuple[str, int]:
    """Reset every ticked checkbox, returning the body and how many changed."""
    out, count = _TICKED.subn(r"\1[ ]", body)
    return out, count


def _ripgrep(pattern: str, roots: list[str]) -> str:
    try:
        proc = subprocess.run(
            # `-e pattern` and a `--` before the roots, both load-bearing.
            #
            # Passed positionally, a pattern that STARTS WITH A DASH is parsed
            # as a flag: searching for "- [x]" — a ticked markdown checkbox —
            # came back "rg: unrecognized flag -", which the caller then read as
            # "no matches". That is how an agent asked to find approved findings
            # reported "nothing approved" while fifteen sat ticked on disk. -e
            # says the next argument is the pattern; -- says the rest are paths,
            # so a file named "-foo.md" cannot do the same thing.
            ["rg", "--fixed-strings", "-i", "--max-count", "3", "-n", "--max-filesize", "1M",
             "-g", "!.git", "-g", "!.cortex", "-e", pattern, "--", *roots],
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
