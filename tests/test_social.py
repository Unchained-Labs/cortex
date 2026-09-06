"""The post queue: drafts in the vault, approved by a human, published by nothing.

There is deliberately no credential in this module. `queue_post` writes a file;
whether it ever leaves the building is a separate decision made by a person
ticking a box. These tests are mostly about that boundary holding, and about the
queue staying parseable after being written and read back.
"""

from __future__ import annotations

import pytest

from cortex import social
from cortex.brain import Brain


def _call(brain: Brain, name: str, **kw):
    return brain.registry.invoke(name, kw)


def test_a_queued_post_lands_unticked(brain: Brain) -> None:
    """The gate. Nothing an agent writes is approved by having been written."""
    out = _call(brain, "queue_post", channel="linkedin", title="The review loop",
                body="A note about how findings get approved before an agent touches them. " * 2,
                when="2026-09-08")
    assert out.ok, out.text
    body = (brain.config.shared_vault / "social" / "queue.md").read_text()
    assert "- [ ]" in body and "- [x]" not in body
    assert "nothing publishes until a human approves" in out.text.lower()


def test_what_is_written_can_be_read_back(brain: Brain) -> None:
    _call(brain, "queue_post", channel="x", title="Ports and drift",
          body="Every divergence between docs and reality is a future outage. " * 2,
          when="2026-09-09", hook="Two sources of truth is one too many",
          source="reviews/fragmentation-2026-09-06.md#F1")
    rows = social.parse((brain.config.shared_vault / "social" / "queue.md").read_text())
    assert len(rows) == 1
    assert rows[0]["channel"] == "x" and rows[0]["date"] == "2026-09-09"
    assert not rows[0]["approved"]
    # The source travels with it: a weekly post built from the brain should be
    # traceable to the finding it is about.
    assert "fragmentation" in rows[0]["body"]


def test_newest_is_first(brain: Brain) -> None:
    for n, when in enumerate(("2026-09-01", "2026-09-05")):
        _call(brain, "queue_post", channel="blog", title=f"Post number {n}",
              body="Body long enough to count as an actual draft rather than a stub. ",
              when=when)
    rows = social.parse((brain.config.shared_vault / "social" / "queue.md").read_text())
    assert rows[0]["date"] == "2026-09-05"


@pytest.mark.parametrize("kw,reason", [
    ({"channel": "tiktok"}, "channel must be one of"),
    ({"when": "next tuesday"}, "YYYY-MM-DD"),
    ({"title": "hi"}, "recognise"),
    ({"body": "short"}, "too short"),
])
def test_a_stub_is_refused_with_the_reason(brain: Brain, kw, reason) -> None:
    args = {"channel": "linkedin", "title": "A reasonable title here",
            "body": "A body that is comfortably long enough to be a real draft. " * 2,
            "when": "2026-09-08"}
    args.update(kw)
    out = _call(brain, "queue_post", **args)
    assert reason in out.text
    # And nothing was written — a refused post must not half-land.
    q = brain.config.shared_vault / "social" / "queue.md"
    assert not q.exists() or "- [" not in q.read_text()


def test_the_queue_separates_approved_from_pending(brain: Brain) -> None:
    _call(brain, "queue_post", channel="linkedin", title="First one out",
          body="Long enough to be a draft and not a note about writing one. " * 2,
          when="2026-09-08")
    q = brain.config.shared_vault / "social" / "queue.md"
    q.write_text(q.read_text().replace("- [ ]", "- [x]", 1), encoding="utf-8")

    assert "APPROVED" in _call(brain, "post_queue", only="approved").text
    assert "No approved" not in _call(brain, "post_queue", only="approved").text
    assert "First one out" not in _call(brain, "post_queue", only="pending").text


def test_an_empty_queue_says_so(brain: Brain) -> None:
    assert "empty" in _call(brain, "post_queue").text
