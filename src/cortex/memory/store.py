"""SQLite persistence: chunks + FTS5, vectors, facts, conversations, keys.

One file per brain. FTS5 always works; vectors exist only when an embed
provider is configured, and their absence degrades search to full-text — it
never fakes a vector score.
"""

from __future__ import annotations

import re
import sqlite3
import struct
from datetime import UTC, datetime
from pathlib import Path

from cortex.memory.chunking import Chunk

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
    created_at TEXT NOT NULL, retired INTEGER NOT NULL DEFAULT 0
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
CREATE TABLE IF NOT EXISTS keys(
    name TEXT PRIMARY KEY, prefix TEXT NOT NULL, hash TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
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


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the HTTP server calls from a worker-thread
        # pool. CPython's sqlite3 runs in serialized threading mode, so the
        # connection itself is safe to share.
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(_SCHEMA)
        self.db.commit()

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

    def stats(self) -> dict[str, int]:
        files = self.db.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"]
        chunks = self.db.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        vectors = self.db.execute("SELECT COUNT(*) AS n FROM vectors").fetchone()["n"]
        facts = self.db.execute(
            "SELECT COUNT(*) AS n FROM facts WHERE retired=0"
        ).fetchone()["n"]
        return {"files": files, "chunks": chunks, "vectors": vectors, "facts": facts}

    # -- search primitives ------------------------------------------------
    def fts_search(self, query: str, k: int = 40) -> list[sqlite3.Row]:
        q = fts_query(query)
        if not q:
            return []
        return self.db.execute(
            "SELECT c.id, c.path, c.heading, c.body, c.start_line, c.mtime, "
            "bm25(chunks_fts) AS rank "
            "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
            "WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
            (q, k),
        ).fetchall()

    def all_vectors(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT chunk_id, dim, v FROM vectors").fetchall()

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
    def add_fact(self, body: str, source: str = "") -> int:
        with self.db:
            cur = self.db.execute(
                "INSERT INTO facts(body, source, created_at) VALUES(?,?,?)",
                (body, source, _now()),
            )
        return cur.lastrowid or 0

    def search_facts(self, query: str, k: int = 10) -> list[sqlite3.Row]:
        q = fts_query(query)
        if not q:
            return []
        return self.db.execute(
            "SELECT f.id, f.body, f.source, f.created_at, bm25(facts_fts) AS rank "
            "FROM facts_fts JOIN facts f ON f.id = facts_fts.rowid "
            "WHERE facts_fts MATCH ? AND f.retired=0 ORDER BY rank LIMIT ?",
            (q, k),
        ).fetchall()

    def recent_facts(self, k: int = 20) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT id, body, source, created_at FROM facts WHERE retired=0 "
            "ORDER BY id DESC LIMIT ?",
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

    # -- keys -------------------------------------------------------------
    def add_key(self, name: str, prefix: str, key_hash: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO keys(name, prefix, hash, created_at) VALUES(?,?,?,?)",
                (name, prefix, key_hash, _now()),
            )

    def list_keys(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT name, prefix, enabled, created_at FROM keys ORDER BY name"
        ).fetchall()

    def revoke_key(self, name: str) -> bool:
        with self.db:
            cur = self.db.execute("UPDATE keys SET enabled=0 WHERE name=?", (name,))
        return cur.rowcount > 0

    def key_valid(self, key_hash: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM keys WHERE hash=? AND enabled=1", (key_hash,)
        ).fetchone()
        return row is not None
