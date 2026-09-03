"""write_note: the tool that lets an agent build a document.

capture_note appends one line to today's journal. That is right for "note that
down" and useless for documentation — and the failure mode when a model is
given a goal it has no tool for is not an error, it is a confident report of
success. A worker asked to document an app announced a note at apps/jinsen.md
that was never written, because nothing could have written it. These tests
cover the tool that closes that gap, and the ways it could quietly write the
wrong thing.
"""

from __future__ import annotations

import pytest

from cortex.brain import Brain


def _call(brain: Brain, name: str, **kw):
    return brain.registry.invoke(name, kw)


def test_creates_a_document_at_a_path(brain: Brain) -> None:
    out = _call(brain, "write_note", path="apps/jinsen.md", text="# jinsen\n\nA thing.")
    assert out.ok, out.text
    written = (brain.config.vaults_dir / "shared" / "apps" / "jinsen.md")
    assert written.is_file()
    assert written.read_text().startswith("# jinsen")


def test_replace_overwrites_and_append_extends(brain: Brain) -> None:
    _call(brain, "write_note", path="a.md", text="one")
    _call(brain, "write_note", path="a.md", text="two", mode="append")
    body = (brain.config.vaults_dir / "shared" / "a.md").read_text()
    assert "one" in body and "two" in body

    _call(brain, "write_note", path="a.md", text="three", mode="replace")
    body = (brain.config.vaults_dir / "shared" / "a.md").read_text()
    assert "one" not in body and "three" in body


def test_accepts_the_index_key_the_read_tools_hand_back(brain: Brain) -> None:
    # search and read_file report paths as vaults/<name>/<rel>. A model that
    # round-trips one must not end up writing vaults/shared/vaults/shared/x.md.
    _call(brain, "write_note", path="vaults/shared/deep/x.md", text="hi")
    assert (brain.config.vaults_dir / "shared" / "deep" / "x.md").is_file()
    assert not (brain.config.vaults_dir / "shared" / "vaults").exists()


@pytest.mark.parametrize("path", ["../escape.md", "a/../../escape.md"])
def test_refuses_to_climb_out_of_the_vault(brain: Brain, path: str) -> None:
    out = _call(brain, "write_note", path=path, text="x")
    assert "climb out" in out.text or not out.ok


def test_refuses_a_non_text_extension(brain: Brain) -> None:
    out = _call(brain, "write_note", path="payload.sh", text="rm -rf /")
    assert not out.ok or "Could not write" in out.text


def test_rejects_an_unknown_mode(brain: Brain) -> None:
    out = _call(brain, "write_note", path="a.md", text="x", mode="upsert")
    assert "replace" in out.text
