"""Incremental indexing of the brain's files.

Change detection is a per-file ``mtime_ns:size`` signature. The index
identity string couples the chunk schema *and* the embedding model, so
changing either forces a clean re-index instead of silently mixing vector
spaces. Signatures are written only after chunks (and vectors) land, so a
crash mid-run retries the file next cycle.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from cortex.config import BrainConfig
from cortex.memory.chunking import CHUNK_SCHEMA, chunk_file
from cortex.memory.store import Store
from cortex.providers.base import Provider

TEXT_SUFFIXES = {
    ".md", ".mdx", ".markdown", ".txt", ".rst", ".org",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".c", ".h", ".cpp",
    ".hpp", ".java", ".kt", ".rb", ".sh", ".bash", ".zsh", ".lua", ".swift",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env.example",
    ".html", ".css", ".sql", ".csv",
}
TEXT_NAMES = {"dockerfile", "makefile", "justfile", "readme", "license"}
SKIP_DIRS = {
    ".git", ".cortex", ".obsidian", "node_modules", "dist", "build",
    "__pycache__", ".venv", "venv", ".trash",
}
MAX_BYTES = 1_500_000
EMBED_BATCH = 16


@dataclass
class IndexReport:
    indexed: int = 0
    removed: int = 0
    unchanged: int = 0
    skipped: int = 0
    reset: bool = False
    embeddings: bool = False


def _is_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name.lower() in TEXT_NAMES


def scan_files(roots: list[Path]) -> dict[str, Path]:
    """Map of index key ("<root-name>/relative/path") to absolute path."""
    found: dict[str, Path] = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or not _is_text(path):
                continue
            parts = path.relative_to(root).parts
            if any(p in SKIP_DIRS or p.startswith(".") for p in parts[:-1]):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size == 0 or size > MAX_BYTES:
                continue
            found[f"{root.name}/{path.relative_to(root)}"] = path
    return found


def _index_identity(embed_model: str) -> str:
    return f"schema={CHUNK_SCHEMA};embed={embed_model or 'none'}"


async def run_index(
    config: BrainConfig, store: Store, embedder: Provider | None
) -> IndexReport:
    report = IndexReport(embeddings=embedder is not None)
    embed_model = embedder.profile.embed_model if embedder else ""
    identity = _index_identity(embed_model)
    if store.meta_get("index_identity") != identity:
        store.reset_index()
        store.meta_set("index_identity", identity)
        report.reset = True

    files = scan_files(config.indexed_roots())

    for gone in store.known_files() - set(files):
        store.delete_file(gone)
        report.removed += 1

    for key, path in files.items():
        stat = path.stat()
        sig = f"{stat.st_mtime_ns}:{stat.st_size}"
        if store.file_sig(key) == sig:
            report.unchanged += 1
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            report.skipped += 1
            continue
        chunks = chunk_file(path.suffix.lower(), text)
        vectors = None
        if embedder is not None and chunks:
            vectors = await _embed_ordered(embedder, [c.embedding_text() for c in chunks])
        store.replace_file(key, sig, stat.st_mtime, chunks, vectors)
        report.indexed += 1

    return report


async def _embed_ordered(embedder: Provider, texts: list[str]) -> list[list[float]]:
    """Batched embedding, reassembled by offset so a fast batch can never
    shift a slow one's vectors."""
    out: list[list[float] | None] = [None] * len(texts)

    async def one(offset: int, batch: list[str]) -> None:
        vectors = await embedder.embed(batch)
        out[offset : offset + len(vectors)] = vectors

    await asyncio.gather(
        *(one(i, texts[i : i + EMBED_BATCH]) for i in range(0, len(texts), EMBED_BATCH))
    )
    if any(v is None for v in out):
        raise RuntimeError("embedding batches left gaps")
    return out  # type: ignore[return-value]
