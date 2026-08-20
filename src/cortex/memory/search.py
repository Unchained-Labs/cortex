"""Hybrid retrieval: FTS5 + vector similarity fused with RRF, plus recency.

Vector scoring is exact cosine over every stored vector in Python. That is
honest about its ceiling: it is built for personal- and team-sized brains,
not for millions of chunks. When no vectors exist the fusion degrades to
full-text alone and says so in the result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from cortex.memory.store import Store, unpack_vector

RRF_K = 60
RECENCY_WEIGHT = 0.01
RECENCY_HALF_LIFE_DAYS = 180.0
MAX_PASSAGES_PER_FILE = 3


@dataclass
class Passage:
    heading: str
    text: str
    start_line: int


@dataclass
class Hit:
    path: str
    score: float
    passages: list[Passage] = field(default_factory=list)


@dataclass
class SearchResult:
    hits: list[Hit]
    used_vectors: bool


def _cosine(a: tuple[float, ...], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def vector_rank(store: Store, query_vector: list[float], k: int = 40) -> list[int]:
    scored: list[tuple[float, int]] = []
    for row in store.all_vectors():
        if row["dim"] != len(query_vector):
            continue
        vec = unpack_vector(row["v"], row["dim"])
        scored.append((_cosine(vec, query_vector), row["chunk_id"]))
    scored.sort(reverse=True)
    return [chunk_id for _, chunk_id in scored[:k]]


def hybrid_search(
    store: Store,
    query: str,
    query_vector: list[float] | None,
    k_files: int = 8,
    now: float | None = None,
) -> SearchResult:
    fts_rows = store.fts_search(query)
    fts_ids = [r["id"] for r in fts_rows]
    vec_ids = vector_rank(store, query_vector) if query_vector else []

    # Reciprocal rank fusion: vector sets the base score, lexical adds to it.
    scores: dict[int, float] = {}
    for rank, cid in enumerate(vec_ids):
        scores[cid] = max(scores.get(cid, 0.0), 1.0 / (RRF_K + rank + 1))
    for rank, cid in enumerate(fts_ids):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)

    rows = store.chunks_by_ids(list(scores))

    if now is not None:
        for cid, row in rows.items():
            age_days = max(0.0, (now - row["mtime"]) / 86400.0)
            decay = math.exp(-age_days * math.log(2) / RECENCY_HALF_LIFE_DAYS)
            scores[cid] += RECENCY_WEIGHT * decay

    # Group chunks into files, best chunks first.
    by_file: dict[str, Hit] = {}
    for cid in sorted(scores, key=lambda c: scores[c], reverse=True):
        row = rows.get(cid)
        if row is None:
            continue
        hit = by_file.setdefault(row["path"], Hit(path=row["path"], score=0.0))
        hit.score = max(hit.score, scores[cid])
        if len(hit.passages) < MAX_PASSAGES_PER_FILE:
            hit.passages.append(
                Passage(heading=row["heading"], text=row["body"], start_line=row["start_line"])
            )

    hits = sorted(by_file.values(), key=lambda h: h.score, reverse=True)[:k_files]
    return SearchResult(hits=hits, used_vectors=bool(vec_ids))


def format_result(result: SearchResult, query: str) -> str:
    if not result.hits:
        return f"Nothing in the brain matches {query!r}."
    lines: list[str] = []
    if not result.used_vectors:
        lines.append("(full-text only: no embeddings are configured or indexed)")
    for hit in result.hits:
        lines.append(f"--- {hit.path} (score: {hit.score:.4f}) ---")
        for p in hit.passages:
            if p.heading:
                lines.append(f"[{p.heading}] (line {p.start_line})")
            text = p.text if len(p.text) <= 1200 else p.text[:1200] + " …"
            lines.append(text)
        lines.append("")
    return "\n".join(lines).strip()
