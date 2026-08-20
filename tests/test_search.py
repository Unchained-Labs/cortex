import time

from cortex.memory.chunking import Chunk
from cortex.memory.search import format_result, hybrid_search
from cortex.memory.store import Store


def seeded_store(tmp_path, with_vectors=False):
    store = Store(tmp_path / "index.db")
    vec_a = [1.0, 0.0] if with_vectors else None
    vec_b = [0.0, 1.0] if with_vectors else None
    store.replace_file(
        "notes/telemetry.md",
        "1:1",
        time.time(),
        [Chunk(text="we retry telemetry twice then drop", heading="Decision", start_line=3)],
        [vec_a] if with_vectors else None,
    )
    store.replace_file(
        "notes/lunch.md",
        "1:1",
        time.time() - 400 * 86400,
        [Chunk(text="thursday lunch is tacos", heading="", start_line=1)],
        [vec_b] if with_vectors else None,
    )
    return store


def test_fts_only_search_flags_itself(tmp_path):
    store = seeded_store(tmp_path)
    result = hybrid_search(store, "telemetry retry", None)
    assert not result.used_vectors
    assert result.hits[0].path == "notes/telemetry.md"
    text = format_result(result, "telemetry retry")
    assert "full-text only" in text


def test_vector_and_fts_fusion(tmp_path):
    store = seeded_store(tmp_path, with_vectors=True)
    # query vector points at telemetry's vector; fts also matches telemetry
    result = hybrid_search(store, "telemetry", [1.0, 0.0])
    assert result.used_vectors
    assert result.hits[0].path == "notes/telemetry.md"
    # fused score: vector rank 1 base + lexical addition
    assert result.hits[0].score > 1.0 / 61.0


def test_recency_nudges_newer_files(tmp_path):
    store = Store(tmp_path / "index.db")
    now = time.time()
    for name, age_days in (("old", 900), ("new", 0)):
        store.replace_file(
            f"notes/{name}.md",
            "1:1",
            now - age_days * 86400,
            [Chunk(text="identical anchovy content", heading="", start_line=1)],
            None,
        )
    result = hybrid_search(store, "anchovy", None, now=now)
    assert [h.path for h in result.hits][0] == "notes/new.md"


def test_empty_result_message(tmp_path):
    store = Store(tmp_path / "i.db")
    result = hybrid_search(store, "anything", None)
    assert format_result(result, "anything").startswith("Nothing in the brain")
