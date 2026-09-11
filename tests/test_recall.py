"""Recall over the brain's own conversations: scoped to their owner, kept
current by triggers, built once for a database from before it existed."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from conftest import add_user
from cortex import scope
from cortex.brain import Brain
from cortex.memory.store import Store
from cortex.server.app import build_app


def seed(brain: Brain) -> None:
    brain.record_turn(
        "t-erwin-1", "erwin", "what did we decide about the boiler", "Service it in March."
    )
    brain.record_turn("t-erwin-2", "erwin", "plan the garden beds", "Compost first, then plant.")
    brain.record_turn("t-ana-1", "ana", "boiler warranty question", "It runs until 2027.")


def tool(brain: Brain, name: str):
    return next(p for p in brain.registry.plugins() if p.name == name).func


def test_search_is_scoped_to_the_owner(brain: Brain):
    seed(brain)
    mine = brain.store.search_messages("boiler", "erwin")
    assert {r["thread"] for r in mine} == {"t-erwin-1"}
    assert mine[0]["title"].startswith("what did we decide")
    everyone = brain.store.search_messages("boiler", None)
    assert {r["thread"] for r in everyone} == {"t-erwin-1", "t-ana-1"}
    assert brain.store.search_messages("", "erwin") == []
    # operators are quoted into plain words, never parsed: this asks for a line
    # holding all of "boiler", "OR", "1", and there is none
    assert brain.store.search_messages('boiler" OR 1=1 --', "erwin") == []


def test_old_conversations_are_indexed_once(tmp_path: Path):
    path = tmp_path / "index.db"
    store = Store(path)
    store.db.execute("DROP TRIGGER messages_ai")  # a database from before recall
    store.touch_thread("t1", "erwin", "boiler")
    store.append_message("t1", "user", "the boiler again")
    assert store.search_messages("boiler", "erwin") == []
    store.db.execute("DELETE FROM meta WHERE key='messages_fts_built'")
    store.db.commit()
    store.close()
    again = Store(path)
    assert [r["thread"] for r in again.search_messages("boiler", "erwin")] == ["t1"]
    again.close()


def test_recall_tool_groups_by_thread_and_reads_one(brain: Brain):
    seed(brain)
    recall = tool(brain, "recall_conversation")
    with scope.scoped(("vaults/shared/",), "erwin"):
        out = recall("boiler")
        assert "1 conversation(s) mention 'boiler'" in out
        assert "thread t-erwin-1" in out and "t-ana-1" not in out
        assert "you: what did we decide about the boiler" in out
        whole = recall(thread="t-erwin-1")
        assert "you: what did we decide about the boiler" in whole
        assert "No conversation" in recall(thread="t-ana-1")
        assert "Nothing in past conversations" in recall("submarine")
        assert "Give words" in recall("")
    with scope.scoped(None):  # the box owner sees everyone's
        assert "2 conversation(s)" in recall("boiler")
    with scope.scoped(("vaults/shared/",), ""):  # scoped but anonymous: nothing
        assert "No conversations" in recall("boiler")


def test_threads_search_endpoint(brain: Brain):
    seed(brain)
    add_user(brain, "erwin", role="admin")
    with TestClient(build_app(brain)) as client:
        client.post("/api/auth/login", json={"username": "erwin", "password": "hunter2hunter2"})
        hits = client.get("/api/threads/search", params={"q": "boiler"}).json()["hits"]
        assert [h["thread"] for h in hits] == ["t-erwin-1"]
        assert hits[0]["snippet"] and hits[0]["role"] in ("user", "assistant")
        assert client.get("/api/threads/search", params={"q": "warranty"}).json()["hits"] == []
