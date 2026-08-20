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


def test_users_lifecycle(tmp_path):
    store = make_store(tmp_path)
    store.add_user("erwin", "hash", "salt", "admin")
    row = store.get_user("erwin")
    assert row["role"] == "admin"
    assert store.count_users() == 1
    assert store.delete_user("erwin") and not store.delete_user("ghost")


def test_channels_and_messages(tmp_path):
    store = make_store(tmp_path)
    cid = store.ensure_channel("general", "cortex")
    assert store.ensure_channel("general", "someone") == cid  # idempotent
    for i in range(3):
        store.add_channel_message(cid, "erwin", f"m{i}")
    rows = store.channel_messages(cid, limit=2)
    assert [r["body"] for r in rows] == ["m1", "m2"]
    older = store.channel_messages(cid, before=rows[0]["id"], limit=10)
    assert [r["body"] for r in older] == ["m0"]
    assert store.channel_exists(cid) and not store.channel_exists(999)


def test_threads_ownership_and_titles(tmp_path):
    store = make_store(tmp_path)
    store.touch_thread("t1", "erwin", title_candidate="what about the rosemary plant")
    store.touch_thread("t1", "erwin")  # second touch keeps the title
    (row,) = store.list_threads("erwin")
    assert row["title"].startswith("what about")
    assert store.thread_owner("t1") == "erwin"
    assert store.thread_owner("ghost") is None
    assert store.list_threads("sam") == []


def test_prefix_scope_in_fts(tmp_path):
    store = make_store(tmp_path)
    store.replace_file("shared/a.md", "1:1", 1.0, [chunk("secret rosemary shared")], None)
    store.replace_file("erwin/b.md", "1:1", 1.0, [chunk("secret rosemary private")], None)
    everything = store.fts_search("rosemary", prefixes=None)
    assert len(everything) == 2
    shared_only = store.fts_search("rosemary", prefixes=("shared/",))
    assert [r["path"] for r in shared_only] == ["shared/a.md"]
    nothing = store.fts_search("rosemary", prefixes=())
    assert nothing == []  # empty scope grants nothing, never everything


def test_prefix_scope_in_vectors(tmp_path):
    store = make_store(tmp_path)
    store.replace_file("shared/a.md", "1:1", 1.0, [chunk("v")], [[1.0, 0.0]])
    store.replace_file("erwin/b.md", "1:1", 1.0, [chunk("w")], [[0.0, 1.0]])
    assert len(store.all_vectors(None)) == 2
    assert len(store.all_vectors(("erwin/",))) == 1
    assert store.all_vectors(()) == []
