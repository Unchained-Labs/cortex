from pathlib import Path

from cortex.memory.chunking import Chunk
from cortex.memory.store import Store, fts_query, pack_vector, unpack_vector


def make_store(tmp_path: Path) -> Store:
    return Store(tmp_path / "index.db")


def chunk(text: str, heading: str = "") -> Chunk:
    return Chunk(text=text, heading=heading, start_line=1)


def test_replace_and_fts_roundtrip(tmp_path):
    store = make_store(tmp_path)
    store.replace_file(
        "notes/a.md", "1:1", 1000.0, [chunk("the quarterly telemetry decision")], None
    )
    rows = store.fts_search("telemetry")
    assert len(rows) == 1
    assert rows[0]["path"] == "notes/a.md"


def test_replace_swaps_old_chunks(tmp_path):
    store = make_store(tmp_path)
    store.replace_file("notes/a.md", "1:1", 1.0, [chunk("old topic aardvark")], None)
    store.replace_file("notes/a.md", "2:2", 2.0, [chunk("new topic zebra")], None)
    assert store.fts_search("aardvark") == []
    assert len(store.fts_search("zebra")) == 1


def test_delete_file_removes_from_fts(tmp_path):
    store = make_store(tmp_path)
    store.replace_file("notes/a.md", "1:1", 1.0, [chunk("ephemeral")], None)
    store.delete_file("notes/a.md")
    assert store.fts_search("ephemeral") == []
    assert store.known_files() == set()


def test_vector_pack_roundtrip():
    vec = [0.25, -1.5, 3.0]
    assert list(unpack_vector(pack_vector(vec), 3)) == vec


def test_vector_count_mismatch_is_an_error(tmp_path):
    store = make_store(tmp_path)
    try:
        store.replace_file("a", "1:1", 1.0, [chunk("x"), chunk("y")], [[0.1]])
    except ValueError:
        pass
    else:
        raise AssertionError("mismatched vectors must raise")


def test_fts_query_survives_hostile_input():
    q = fts_query('robert"); DROP TABLE chunks; -- OR "a NEAR b')
    assert '"robert"' in q  # quoted terms, no syntax
    store_q = fts_query("!!! ???")
    assert store_q == ""


def test_facts_roundtrip(tmp_path):
    store = make_store(tmp_path)
    fid = store.add_fact("the wifi password lives in the safe", source="chat")
    assert fid > 0
    rows = store.search_facts("wifi")
    assert rows and rows[0]["body"].startswith("the wifi")
    assert store.recent_facts()[0]["id"] == fid


def test_messages_history_order(tmp_path):
    store = make_store(tmp_path)
    for i in range(5):
        store.append_message("t1", "user", f"m{i}")
    rows = store.history("t1", limit=3)
    assert [r["body"] for r in rows] == ["m2", "m3", "m4"]


def test_keys_lifecycle(tmp_path):
    store = make_store(tmp_path)
    store.add_key("laptop", "ctx_abc", "deadbeef")
    assert store.key_valid("deadbeef")
    assert store.revoke_key("laptop")
    assert not store.key_valid("deadbeef")
    assert not store.revoke_key("ghost")
