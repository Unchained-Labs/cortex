"""SQLite persistence: chunks + FTS5, vectors, facts, conversations, users,
channels.

One file per brain. FTS5 always works; vectors exist only when an embed
provider is configured, and their absence degrades search to full-text — it
never fakes a vector score.
"""

from __future__ import annotations

import functools
import re
import sqlite3
import struct
import threading
from datetime import UTC, datetime
from pathlib import Path

from cortex.memory.chunking import Chunk


def _serialized(cls):
    """Wrap every Store method in the instance lock.

    The connection is shared across the server's worker threads and the
    event loop. CPython's sqlite3 serializes individual C calls, but a
    ``with self.db`` transaction in one thread interleaving with reads in
    another still raises "bad parameter or other API misuse" — so access is
    serialized wholesale. At this scale the lock is never contended enough
    to matter."""
    for name, fn in list(vars(cls).items()):
        if name.startswith("__") or isinstance(fn, staticmethod) or not callable(fn):
            continue

        def wrap(inner):
            @functools.wraps(inner)
            def locked(self, *args, **kwargs):
                with self._lock:
                    return inner(self, *args, **kwargs)

            return locked

        setattr(cls, name, wrap(fn))
    return cls

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files(
    path TEXT PRIMARY KEY, sig TEXT NOT NULL, nchunks INTEGER NOT NULL,
    mtime REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks(
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL, idx INTEGER NOT NULL,
    heading TEXT NOT NULL, body TEXT NOT NULL,
    start_line INTEGER NOT NULL, mtime REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    body, heading, content='chunks', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, body, heading) VALUES (new.id, new.body, new.heading);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, body, heading)
    VALUES ('delete', old.id, old.body, old.heading);
END;
CREATE TABLE IF NOT EXISTS vectors(
    chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    dim INTEGER NOT NULL, v BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS facts(
    id INTEGER PRIMARY KEY,
    body TEXT NOT NULL, source TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, retired INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'fact', subject TEXT NOT NULL DEFAULT ''
);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    body, content='facts', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, body) VALUES (new.id, new.body);
END;
CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY,
    thread TEXT NOT NULL, role TEXT NOT NULL, body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread, id);
CREATE TABLE IF NOT EXISTS threads(
    thread TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users(
    username TEXT PRIMARY KEY, pw_hash TEXT NOT NULL, salt TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys(
    -- Bearer keys for non-browser clients: the MCP endpoint, and anything else
    -- that cannot hold a session cookie.
    --
    -- Only the HASH is stored. A key that can be read back out of the database
    -- is a key that leaks with a backup, and there is no reason to ever need
    -- the plaintext again: it is shown once at creation and then unrecoverable,
    -- which is the same bargain every other token store makes.
    token_hash TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    username TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS channels(
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
    created_by TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS channel_messages(
    id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL REFERENCES channels(id),
    author TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS channel_messages_chan ON channel_messages(channel_id, id);
CREATE TABLE IF NOT EXISTS mentions(
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
    username TEXT NOT NULL, author TEXT NOT NULL,
    created_at TEXT NOT NULL, read INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS mentions_user ON mentions(username, read);
CREATE TABLE IF NOT EXISTS identity_proposals(
    id INTEGER PRIMARY KEY, text TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
    decided_at TEXT NOT NULL DEFAULT '', decided_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rules(
    name TEXT PRIMARY KEY, spec TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rule_runs(
    id INTEGER PRIMARY KEY, ran_at TEXT NOT NULL, rule TEXT NOT NULL,
    action TEXT NOT NULL, path TEXT NOT NULL, target TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS jobs(
    name TEXT PRIMARY KEY, spec TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
    last_run TEXT NOT NULL DEFAULT '', last_status TEXT NOT NULL DEFAULT '',
    last_detail TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ext_disabled(
    kind TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY (kind, name)
);
CREATE TABLE IF NOT EXISTS ext_mcp_servers(
    name TEXT PRIMARY KEY, spec TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ext_connector_settings(
    name TEXT PRIMARY KEY, settings TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def pack_vector(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def unpack_vector(blob: bytes, dim: int) -> tuple[float, ...]:
    return struct.unpack(f"{dim}f", blob)


def fts_query(user_query: str) -> str:
    """Quote each term so raw user input can't break FTS5 query syntax."""
    terms = re.findall(r"\w+", user_query, flags=re.UNICODE)
    return " ".join(f'"{t}"' for t in terms)


@_serialized
class Store:
    def __init__(self, path: Path):
        self._lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + the class-level lock: the HTTP server
        # calls from a worker-thread pool and the event loop concurrently.
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(_SCHEMA)
        self._migrate()
        self.db.commit()

    def _migrate(self) -> None:
        """Bring an older database up to the current shape.

        Memories written before kinds existed become plain facts with no
        subject, which is exactly what they were — nothing is dropped and
        nothing is guessed at on the way through.
        """
        columns = {r["name"] for r in self.db.execute("PRAGMA table_info(facts)")}
        if "kind" not in columns:
            self.db.execute(
                "ALTER TABLE facts ADD COLUMN kind TEXT NOT NULL DEFAULT 'fact'"
            )
        if "subject" not in columns:
            self.db.execute(
                "ALTER TABLE facts ADD COLUMN subject TEXT NOT NULL DEFAULT ''"
            )
        self.db.execute("CREATE INDEX IF NOT EXISTS facts_kind ON facts(kind, retired)")

    def close(self) -> None:
        self.db.close()

    # -- meta / index identity -------------------------------------------
    def meta_get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def meta_set(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.db.commit()

    def reset_index(self) -> None:
        self.db.execute("DELETE FROM vectors")
        self.db.execute("DELETE FROM chunks")
        self.db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        self.db.execute("DELETE FROM files")
        self.db.commit()

    # -- files / chunks ---------------------------------------------------
    def file_sig(self, path: str) -> str | None:
        row = self.db.execute("SELECT sig FROM files WHERE path=?", (path,)).fetchone()
        return row["sig"] if row else None

    def known_files(self) -> set[str]:
        return {r["path"] for r in self.db.execute("SELECT path FROM files")}

    def replace_file(
        self,
        path: str,
        sig: str,
        mtime: float,
        chunks: list[Chunk],
        vectors: list[list[float]] | None,
    ) -> None:
        """Atomically swap a file's chunks (and vectors, when present)."""
        if vectors is not None and len(vectors) != len(chunks):
            raise ValueError(f"{path}: {len(chunks)} chunks but {len(vectors)} vectors")
        with self.db:
            self.db.execute("DELETE FROM chunks WHERE path=?", (path,))
            for i, chunk in enumerate(chunks):
                cur = self.db.execute(
                    "INSERT INTO chunks(path, idx, heading, body, start_line, mtime) "
                    "VALUES(?,?,?,?,?,?)",
                    (path, i, chunk.heading, chunk.text, chunk.start_line, mtime),
                )
                if vectors is not None:
                    vec = vectors[i]
                    self.db.execute(
                        "INSERT INTO vectors(chunk_id, dim, v) VALUES(?,?,?)",
                        (cur.lastrowid, len(vec), pack_vector(vec)),
                    )
            self.db.execute(
                "INSERT INTO files(path, sig, nchunks, mtime) VALUES(?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET sig=excluded.sig, "
                "nchunks=excluded.nchunks, mtime=excluded.mtime",
                (path, sig, len(chunks), mtime),
            )

    def delete_file(self, path: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM chunks WHERE path=?", (path,))
            self.db.execute("DELETE FROM files WHERE path=?", (path,))

    def recent_files(
        self, prefixes: tuple[str, ...] | None, since: float, limit: int = 8
    ) -> list[sqlite3.Row]:
        """Indexed files touched since a timestamp, newest first."""
        if prefixes is None:
            clause, params = "", []
        elif not prefixes:
            return []
        else:
            clause = " AND (" + " OR ".join("path GLOB ?" for _ in prefixes) + ")"
            params = [f"{p}*" for p in prefixes]
        return self.db.execute(
            f"SELECT path, mtime FROM files WHERE mtime >= ?{clause} "
            "ORDER BY mtime DESC LIMIT ?",
            (since, *params, limit),
        ).fetchall()

    def stats(self) -> dict[str, int]:
        files = self.db.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"]
        chunks = self.db.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        vectors = self.db.execute("SELECT COUNT(*) AS n FROM vectors").fetchone()["n"]
        facts = self.db.execute(
            "SELECT COUNT(*) AS n FROM facts WHERE retired=0"
        ).fetchone()["n"]
        return {"files": files, "chunks": chunks, "vectors": vectors, "facts": facts}

    # -- search primitives ------------------------------------------------
    # `prefixes` is the caller's scope: None = unrestricted, () = nothing.
    # The filter runs inside the query, never as a post-hoc trim.
    @staticmethod
    def _prefix_clause(prefixes: tuple[str, ...] | None) -> tuple[str, list[str]]:
        if prefixes is None:
            return "", []
        if not prefixes:
            return " AND 0", []
        clause = " AND (" + " OR ".join("c.path GLOB ?" for _ in prefixes) + ")"
        return clause, [f"{p}*" for p in prefixes]

    def fts_search(
        self, query: str, k: int = 40, prefixes: tuple[str, ...] | None = None
    ) -> list[sqlite3.Row]:
        q = fts_query(query)
        if not q:
            return []
        clause, params = self._prefix_clause(prefixes)
        return self.db.execute(
            "SELECT c.id, c.path, c.heading, c.body, c.start_line, c.mtime, "
            "bm25(chunks_fts) AS rank "
            "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
            f"WHERE chunks_fts MATCH ?{clause} ORDER BY rank LIMIT ?",
            (q, *params, k),
        ).fetchall()

    def all_vectors(self, prefixes: tuple[str, ...] | None = None) -> list[sqlite3.Row]:
        clause, params = self._prefix_clause(prefixes)
        return self.db.execute(
            "SELECT v.chunk_id, v.dim, v.v FROM vectors v "
            f"JOIN chunks c ON c.id = v.chunk_id WHERE 1{clause}",
            params,
        ).fetchall()

    def chunks_by_ids(self, ids: list[int]) -> dict[int, sqlite3.Row]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self.db.execute(
            f"SELECT id, path, heading, body, start_line, mtime FROM chunks "
            f"WHERE id IN ({marks})",
            ids,
        ).fetchall()
        return {r["id"]: r for r in rows}

    # -- facts ------------------------------------------------------------
    def add_fact(
        self, body: str, source: str = "", kind: str = "fact", subject: str = ""
    ) -> int:
        with self.db:
            cur = self.db.execute(
                "INSERT INTO facts(body, source, created_at, kind, subject) "
                "VALUES(?,?,?,?,?)",
                (body, source, _now(), kind, subject),
            )
        return cur.lastrowid or 0

    def update_fact(self, fact_id: int, body: str, kind: str, subject: str) -> bool:
        with self.db:
            cur = self.db.execute(
                "UPDATE facts SET body=?, kind=?, subject=? WHERE id=? AND retired=0",
                (body, kind, subject, fact_id),
            )
        return cur.rowcount > 0

    def retire_fact(self, fact_id: int) -> bool:
        """Forget it. Retiring rather than deleting keeps the row for the
        audit trail while removing it from every read path."""
        with self.db:
            cur = self.db.execute(
                "UPDATE facts SET retired=1 WHERE id=? AND retired=0", (fact_id,)
            )
        return cur.rowcount > 0

    # person before project before preference … alphabetical order would put
    # `fact` first, which is the least interesting kind.
    _KIND_ORDER = (
        "CASE kind WHEN 'person' THEN 0 WHEN 'project' THEN 1 "
        "WHEN 'preference' THEN 2 WHEN 'goal' THEN 3 ELSE 4 END"
    )

    def facts_by_kind(self, kind: str = "", limit: int = 200) -> list[sqlite3.Row]:
        if kind:
            return self.db.execute(
                "SELECT id, body, source, created_at, kind, subject FROM facts "
                f"WHERE retired=0 AND kind=? ORDER BY {self._KIND_ORDER}, subject, id DESC "
                "LIMIT ?",
                (kind, limit),
            ).fetchall()
        return self.db.execute(
            "SELECT id, body, source, created_at, kind, subject FROM facts "
            f"WHERE retired=0 ORDER BY {self._KIND_ORDER}, subject, id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def facts_about(self, subject: str, limit: int = 50) -> list[sqlite3.Row]:
        """Everything known about one subject, matched loosely — people are
        written down as "Priya" one day and "Priya Okonkwo" the next."""
        like = f"%{subject.strip()}%"
        return self.db.execute(
            "SELECT id, body, source, created_at, kind, subject FROM facts "
            "WHERE retired=0 AND (subject LIKE ? OR body LIKE ?) "
            "ORDER BY kind, id DESC LIMIT ?",
            (like, like, limit),
        ).fetchall()

    def search_facts(self, query: str, k: int = 10) -> list[sqlite3.Row]:
        q = fts_query(query)
        if not q:
            return []
        return self.db.execute(
            "SELECT f.id, f.body, f.source, f.created_at, f.kind, f.subject, "
            "bm25(facts_fts) AS rank "
            "FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid "
            "WHERE facts_fts MATCH ? AND f.retired=0 ORDER BY rank LIMIT ?",
            (q, k),
        ).fetchall()

    def recent_facts(self, k: int = 20) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT id, body, source, created_at, kind, subject FROM facts "
            "WHERE retired=0 ORDER BY id DESC LIMIT ?",
            (k,),
        ).fetchall()

    # -- conversation -----------------------------------------------------
    def append_message(self, thread: str, role: str, body: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO messages(thread, role, body, created_at) VALUES(?,?,?,?)",
                (thread, role, body, _now()),
            )

    def history(self, thread: str, limit: int = 40) -> list[sqlite3.Row]:
        rows = self.db.execute(
            "SELECT role, body, created_at FROM messages WHERE thread=? "
            "ORDER BY id DESC LIMIT ?",
            (thread, limit),
        ).fetchall()
        return list(reversed(rows))

    # -- users ------------------------------------------------------------
    def add_user(self, username: str, pw_hash: str, salt: str, role: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO users(username, pw_hash, salt, role, created_at) "
                "VALUES(?,?,?,?,?)",
                (username, pw_hash, salt, role, _now()),
            )

    def get_user(self, username: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT username, pw_hash, salt, role, created_at FROM users WHERE username=?",
            (username,),
        ).fetchone()

    # -- api keys ------------------------------------------------------------

    def add_api_key(self, token_hash: str, name: str, username: str, created_at: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO api_keys(token_hash, name, username, created_at) "
                "VALUES(?,?,?,?)",
                (token_hash, name, username, created_at),
            )

    def get_api_key(self, token_hash: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT token_hash, name, username, created_at, last_used_at "
            "FROM api_keys WHERE token_hash=?",
            (token_hash,),
        ).fetchone()

    def touch_api_key(self, token_hash: str, when: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE api_keys SET last_used_at=? WHERE token_hash=?", (when, token_hash)
            )

    def list_api_keys(self) -> list[sqlite3.Row]:
        # No token_hash: this feeds a UI and a CLI listing, and neither has any
        # use for the one column that is worth stealing.
        return list(self.db.execute(
            "SELECT name, username, created_at, last_used_at FROM api_keys "
            "ORDER BY created_at DESC"
        ))

    def delete_api_key(self, name: str) -> int:
        with self.db:
            cur = self.db.execute("DELETE FROM api_keys WHERE name=?", (name,))
            return cur.rowcount

    def set_password(self, username: str, pw_hash: str, salt: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE users SET pw_hash=?, salt=? WHERE username=?",
                (pw_hash, salt, username),
            )

    def list_users(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT username, role, created_at FROM users ORDER BY username"
        ).fetchall()

    def delete_user(self, username: str) -> bool:
        with self.db:
            cur = self.db.execute("DELETE FROM users WHERE username=?", (username,))
        return cur.rowcount > 0

    def count_users(self) -> int:
        return self.db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]

    # -- threads (agent conversations) ------------------------------------
    def touch_thread(self, thread: str, owner: str, title_candidate: str = "") -> None:
        with self.db:
            row = self.db.execute(
                "SELECT title FROM threads WHERE thread=?", (thread,)
            ).fetchone()
            if row is None:
                title = title_candidate.strip()[:80]
                self.db.execute(
                    "INSERT INTO threads(thread, owner, title, updated_at) VALUES(?,?,?,?)",
                    (thread, owner, title, _now()),
                )
            else:
                self.db.execute(
                    "UPDATE threads SET updated_at=? WHERE thread=?", (_now(), thread)
                )

    def list_threads(self, owner: str) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT thread, title, updated_at FROM threads WHERE owner=? "
            "ORDER BY updated_at DESC LIMIT 100",
            (owner,),
        ).fetchall()

    def thread_owner(self, thread: str) -> str | None:
        row = self.db.execute(
            "SELECT owner FROM threads WHERE thread=?", (thread,)
        ).fetchone()
        return row["owner"] if row else None

    # -- channels ----------------------------------------------------------
    def ensure_channel(self, name: str, created_by: str) -> int:
        with self.db:
            row = self.db.execute("SELECT id FROM channels WHERE name=?", (name,)).fetchone()
            if row:
                return row["id"]
            cur = self.db.execute(
                "INSERT INTO channels(name, created_by, created_at) VALUES(?,?,?)",
                (name, created_by, _now()),
            )
            return cur.lastrowid or 0

    def list_channels(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT id, name, created_by FROM channels ORDER BY id"
        ).fetchall()

    def channel_exists(self, channel_id: int) -> bool:
        return (
            self.db.execute("SELECT 1 FROM channels WHERE id=?", (channel_id,)).fetchone()
            is not None
        )

    def add_channel_message(self, channel_id: int, author: str, body: str) -> tuple[int, str]:
        at = _now()
        with self.db:
            cur = self.db.execute(
                "INSERT INTO channel_messages(channel_id, author, body, created_at) "
                "VALUES(?,?,?,?)",
                (channel_id, author, body, at),
            )
        return cur.lastrowid or 0, at

    # -- mentions ---------------------------------------------------------
    # A channel message everyone can see is ambient; one that names you is
    # addressed to you. Only the second kind should ever chase a person.
    def add_mention(
        self, message_id: int, channel_id: int, username: str, author: str
    ) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO mentions(message_id, channel_id, username, author, created_at) "
                "VALUES(?,?,?,?,?)",
                (message_id, channel_id, username, author, _now()),
            )

    def unread_mentions(self, username: str) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT m.id, m.channel_id, m.message_id, m.author, m.created_at, "
            "c.name AS channel, cm.body "
            "FROM mentions m JOIN channels c ON c.id = m.channel_id "
            "LEFT JOIN channel_messages cm ON cm.id = m.message_id "
            "WHERE m.username=? AND m.read=0 ORDER BY m.id DESC LIMIT 50",
            (username,),
        ).fetchall()

    def mark_mentions_read(self, username: str, channel_id: int | None = None) -> int:
        with self.db:
            if channel_id is None:
                cur = self.db.execute(
                    "UPDATE mentions SET read=1 WHERE username=? AND read=0", (username,)
                )
            else:
                cur = self.db.execute(
                    "UPDATE mentions SET read=1 WHERE username=? AND channel_id=? AND read=0",
                    (username, channel_id),
                )
        return cur.rowcount

    # -- identity proposals -------------------------------------------------
    # The agent proposes; a person decides. Kept as rows rather than applied
    # silently, so there is always an answer to "why does it think that".
    def add_identity_proposal(self, text: str, reason: str) -> int:
        with self.db:
            cur = self.db.execute(
                "INSERT INTO identity_proposals(text, reason, created_at) VALUES(?,?,?)",
                (text, reason, _now()),
            )
        return cur.lastrowid or 0

    def identity_proposals(self, status: str = "pending") -> list[sqlite3.Row]:
        columns = (
            "id, text, reason, created_at, status, decided_at, decided_by"
        )
        if status == "decided":
            return self.db.execute(
                f"SELECT {columns} FROM identity_proposals "
                "WHERE status!='pending' ORDER BY id DESC LIMIT 50"
            ).fetchall()
        if status:
            return self.db.execute(
                f"SELECT {columns} FROM identity_proposals "
                "WHERE status=? ORDER BY id DESC LIMIT 50",
                (status,),
            ).fetchall()
        return self.db.execute(
            f"SELECT {columns} FROM identity_proposals ORDER BY id DESC LIMIT 50"
        ).fetchall()

    def get_identity_proposal(self, proposal_id: int) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT id, text, reason, created_at, status FROM identity_proposals "
            "WHERE id=?",
            (proposal_id,),
        ).fetchone()

    def decide_identity_proposal(
        self, proposal_id: int, status: str, who: str
    ) -> bool:
        with self.db:
            cur = self.db.execute(
                "UPDATE identity_proposals SET status=?, decided_at=?, decided_by=? "
                "WHERE id=? AND status='pending'",
                (status, _now(), who, proposal_id),
            )
        return cur.rowcount > 0

    # -- rules and jobs ---------------------------------------------------
    def list_rules(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT name, spec, position FROM rules ORDER BY position, name"
        ).fetchall()

    def upsert_rule(self, name: str, spec: str, position: int = 0) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO rules(name, spec, position, updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET spec=excluded.spec, "
                "position=excluded.position, updated_at=excluded.updated_at",
                (name, spec, position, _now()),
            )

    def delete_rule(self, name: str) -> bool:
        with self.db:
            cur = self.db.execute("DELETE FROM rules WHERE name=?", (name,))
        return cur.rowcount > 0

    def record_rule_actions(self, actions: list[dict]) -> None:
        if not actions:
            return
        stamp = _now()
        with self.db:
            self.db.executemany(
                "INSERT INTO rule_runs(ran_at, rule, action, path, target) VALUES(?,?,?,?,?)",
                [
                    (stamp, a["rule"], a["action"], a["path"], a.get("target", ""))
                    for a in actions
                ],
            )

    def rule_history(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT ran_at, rule, action, path, target FROM rule_runs "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def list_jobs(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT name, spec, enabled, last_run, last_status, last_detail "
            "FROM jobs ORDER BY name"
        ).fetchall()

    def upsert_job(self, name: str, spec: str, enabled: bool) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO jobs(name, spec, enabled, updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET spec=excluded.spec, "
                "enabled=excluded.enabled, updated_at=excluded.updated_at",
                (name, spec, int(enabled), _now()),
            )

    def delete_job(self, name: str) -> bool:
        with self.db:
            cur = self.db.execute("DELETE FROM jobs WHERE name=?", (name,))
        return cur.rowcount > 0

    def record_job_run(self, name: str, status: str, detail: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE jobs SET last_run=?, last_status=?, last_detail=? WHERE name=?",
                (_now(), status, detail[:500], name),
            )

    # -- extensions -------------------------------------------------------
    def is_disabled(self, kind: str, name: str) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM ext_disabled WHERE kind=? AND name=?", (kind, name)
            ).fetchone()
            is not None
        )

    def disabled_names(self, kind: str) -> set[str]:
        return {
            r["name"]
            for r in self.db.execute("SELECT name FROM ext_disabled WHERE kind=?", (kind,))
        }

    def set_disabled(self, kind: str, name: str, disabled: bool) -> None:
        with self.db:
            if disabled:
                self.db.execute(
                    "INSERT OR IGNORE INTO ext_disabled(kind, name) VALUES(?,?)", (kind, name)
                )
            else:
                self.db.execute(
                    "DELETE FROM ext_disabled WHERE kind=? AND name=?", (kind, name)
                )

    def list_mcp_servers(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT name, spec, enabled FROM ext_mcp_servers ORDER BY name"
        ).fetchall()

    def upsert_mcp_server(self, name: str, spec: dict, enabled: bool) -> None:
        import json

        with self.db:
            self.db.execute(
                "INSERT INTO ext_mcp_servers(name, spec, enabled, updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                "spec=excluded.spec, enabled=excluded.enabled, updated_at=excluded.updated_at",
                (name, json.dumps(spec), int(enabled), _now()),
            )

    def set_mcp_enabled(self, name: str, enabled: bool) -> None:
        with self.db:
            self.db.execute(
                "UPDATE ext_mcp_servers SET enabled=?, updated_at=? WHERE name=?",
                (int(enabled), _now(), name),
            )

    def delete_mcp_server(self, name: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM ext_mcp_servers WHERE name=?", (name,))

    def connector_settings(self) -> dict[str, dict]:
        import json

        return {
            r["name"]: json.loads(r["settings"])
            for r in self.db.execute("SELECT name, settings FROM ext_connector_settings")
        }

    def set_connector_settings(self, name: str, settings: dict) -> None:
        import json

        with self.db:
            self.db.execute(
                "INSERT INTO ext_connector_settings(name, settings, updated_at) "
                "VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET "
                "settings=excluded.settings, updated_at=excluded.updated_at",
                (name, json.dumps(settings), _now()),
            )

    def delete_connector_settings(self, name: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM ext_connector_settings WHERE name=?", (name,))

    def channel_messages(
        self, channel_id: int, before: int | None = None, limit: int = 50
    ) -> list[sqlite3.Row]:
        if before is not None:
            rows = self.db.execute(
                "SELECT id, author, body, created_at FROM channel_messages "
                "WHERE channel_id=? AND id<? ORDER BY id DESC LIMIT ?",
                (channel_id, before, limit),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT id, author, body, created_at FROM channel_messages "
                "WHERE channel_id=? ORDER BY id DESC LIMIT ?",
                (channel_id, limit),
            ).fetchall()
        return list(reversed(rows))
