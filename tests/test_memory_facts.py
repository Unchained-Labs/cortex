import sqlite3

import pytest

from cortex.brain import Brain
from cortex.memory import facts
from cortex.memory.store import Store

# -- the vocabulary --------------------------------------------------------


def test_kind_normalisation():
    assert facts.normalise_kind("") == "fact"
    assert facts.normalise_kind(" Person ") == "person"
    with pytest.raises(facts.MemoryError, match="unknown kind"):
        facts.normalise_kind("enemy")


def test_subject_is_tidied_and_capped():
    assert facts.normalise_subject("  Priya   Okonkwo \n") == "Priya Okonkwo"
    assert len(facts.normalise_subject("x" * 200)) == facts.MAX_SUBJECT


def test_subject_guessing_is_conservative():
    assert facts.guess_subject("Priya Okonkwo is allergic to shellfish") == "Priya Okonkwo"
    # an article is not a name, and a wrong subject is worse than none
    assert facts.guess_subject("The boiler is serviced every March") == ""
    assert facts.guess_subject("we water the rosemary on Fridays") == ""


def test_format_groups_by_kind():
    memories = [
        facts.Memory(1, "person", "Priya", "allergic to shellfish", "", "now"),
        facts.Memory(2, "preference", "", "we eat at 7", "", "now"),
    ]
    out = facts.format_memories(memories)
    assert "person:" in out and "preference:" in out
    assert "Priya" in out
    assert facts.format_memories([], "x").startswith("Nothing remembered matches")


# -- storage ---------------------------------------------------------------


def test_typed_storage_and_reads(brain: Brain):
    store = brain.store
    store.add_fact("allergic to shellfish", kind="person", subject="Priya")
    store.add_fact("Meridian Heating, number inside the casing", kind="person",
                   subject="boiler engineer")
    store.add_fact("we eat at seven", kind="preference")

    people = store.facts_by_kind("person")
    assert len(people) == 2
    assert {r["subject"] for r in people} == {"Priya", "boiler engineer"}
    assert len(store.facts_by_kind()) == 3

    about = store.facts_about("priya")
    assert len(about) == 1 and about[0]["body"].startswith("allergic")
    # matching is loose: bodies count too, because names get written down
    # inconsistently
    assert len(store.facts_about("Meridian")) == 1


def test_edit_and_forget(brain: Brain):
    store = brain.store
    memory_id = store.add_fact("allergic to peanuts", kind="person", subject="Priya")
    assert store.update_fact(memory_id, "allergic to shellfish", "person", "Priya")
    assert store.facts_by_kind("person")[0]["body"] == "allergic to shellfish"
    assert store.retire_fact(memory_id)
    assert store.facts_by_kind("person") == []
    assert not store.retire_fact(memory_id)  # already gone
    assert not store.update_fact(9999, "x", "fact", "")


def test_an_old_database_is_migrated_without_loss(tmp_path):
    """Memories written before kinds existed must survive the upgrade."""
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE facts(id INTEGER PRIMARY KEY, body TEXT NOT NULL, "
        "source TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, "
        "retired INTEGER NOT NULL DEFAULT 0);"
    )
    old.execute(
        "INSERT INTO facts(body, source, created_at) VALUES(?,?,?)",
        ("the wifi password is in the safe", "chat:erwin", "2026-01-01T00:00:00+00:00"),
    )
    old.commit()
    old.close()

    store = Store(path)
    try:
        rows = store.facts_by_kind()
        assert len(rows) == 1
        assert rows[0]["body"] == "the wifi password is in the safe"
        assert rows[0]["kind"] == "fact"      # untyped memories become facts
        assert rows[0]["subject"] == ""       # and nothing is guessed at
        assert rows[0]["source"] == "chat:erwin"
    finally:
        store.close()


# -- the tools -------------------------------------------------------------


def test_remember_still_works_untyped(brain: Brain):
    """The old call signature keeps working — this is the compatibility test."""
    out = brain.registry.invoke("remember", {"fact": "the bins go out on Tuesday"}).text
    assert out.startswith("Remembered as a fact")
    assert "bins" in brain.registry.invoke("recall", {}).text


def test_remember_typed_and_recall_about(brain: Brain):
    brain.registry.invoke("remember", {
        "fact": "allergic to shellfish", "kind": "person", "subject": "Priya",
    })
    brain.registry.invoke("remember", {
        "fact": "serviced every March by Meridian Heating",
        "kind": "project", "subject": "boiler",
    })
    people = brain.registry.invoke("recall", {"kind": "person"}).text
    assert "Priya" in people and "boiler" not in people

    about = brain.registry.invoke("recall_about", {"subject": "Priya"}).text
    assert "shellfish" in about
    assert brain.registry.invoke("recall_about", {"subject": ""}).text.startswith("Which")

    grouped = brain.registry.invoke("recall", {}).text
    assert "person:" in grouped and "project:" in grouped


def test_remember_rejects_an_unknown_kind(brain: Brain):
    out = brain.registry.invoke("remember", {"fact": "x", "kind": "enemy"}).text
    assert "unknown kind" in out


def test_person_memories_get_a_subject_for_free(brain: Brain):
    brain.registry.invoke("remember", {
        "fact": "Sam Okonkwo runs the allotment committee", "kind": "person",
    })
    assert "Sam Okonkwo" in brain.registry.invoke("recall", {"kind": "person"}).text
