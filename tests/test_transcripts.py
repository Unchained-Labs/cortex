"""Reading the agent transcripts already on this machine.

The shape of a Claude Code transcript has changed across versions and will
change again, so every test here is about surviving that: a line that does not
parse, a content block that is a list instead of a string, a turn that is a tool
result rather than something anybody said.
"""

from __future__ import annotations

import json

import pytest

from cortex import transcripts


@pytest.fixture()
def config(tmp_path):
    proj = tmp_path / "projects" / "-home-jarvis-dev-chezmoi"
    proj.mkdir(parents=True)
    lines = [
        {"type": "user", "message": {"role": "user", "content": "how do we cap the docker logs"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Set max-size in daemon.json."},
            {"type": "thinking", "text": "should not be returned"},
        ]}},
        {"type": "tool_result", "message": {"role": "user", "content": "irrelevant noise"}},
        {"type": "summary", "summary": "not a turn"},
    ]
    body = "\n".join(json.dumps(o) for o in lines)
    # A half-written line: a session still running is appended to while read.
    body += '\n{"type": "user", "message": {"role": "user", "cont'
    (proj / "aaaa-1111.jsonl").write_text(body + "\n", encoding="utf-8")
    return tmp_path


def test_sessions_are_listed_newest_first(config) -> None:
    rows = transcripts.sessions(str(config))
    assert len(rows) == 1
    assert rows[0].session_id == "aaaa-1111"
    assert rows[0].project.endswith("chezmoi")


def test_search_finds_a_phrase_and_says_who_said_it(config) -> None:
    hits = transcripts.search("docker logs", str(config))
    assert len(hits) == 1
    assert hits[0]["role"] == "user"
    assert "docker logs" in hits[0]["text"]


def test_search_reads_text_out_of_block_lists(config) -> None:
    hits = transcripts.search("daemon.json", str(config))
    assert hits and hits[0]["role"] == "assistant"


def test_thinking_blocks_are_not_returned(config) -> None:
    # A model's private reasoning is not something anybody said, and surfacing
    # it as a past conversation would be quoting it back as a decision.
    assert transcripts.search("should not be returned", str(config)) == []


def test_tool_results_are_not_turns(config) -> None:
    assert transcripts.search("irrelevant noise", str(config)) == []


def test_a_malformed_line_does_not_cost_the_file(config) -> None:
    """One bad line in a 90 MB transcript must not lose the other forty thousand."""
    assert transcripts.search("docker logs", str(config)), "the good lines still read"


def test_no_match_is_an_empty_list_not_an_error(config) -> None:
    # The clean "no" is the useful half: it is what lets the agent say "we have
    # not covered this" instead of guessing.
    assert transcripts.search("kubernetes", str(config)) == []


def test_reading_one_session_returns_only_real_turns(config) -> None:
    out = transcripts.transcript("aaaa-1111", str(config))
    assert out["total_turns"] == 2
    assert [t["role"] for t in out["turns"]] == ["user", "assistant"]


def test_reading_an_unknown_session_says_so(config) -> None:
    assert "no session" in transcripts.transcript("nope", str(config))["error"]


def test_pagination_reports_whether_more_remains(config) -> None:
    out = transcripts.transcript("aaaa-1111", str(config), limit=1)
    assert out["truncated"] is True
    assert len(out["turns"]) == 1


def test_an_absent_config_directory_is_not_a_crash(tmp_path) -> None:
    assert transcripts.sessions(str(tmp_path / "nowhere")) == []
    assert transcripts.search("anything", str(tmp_path / "nowhere")) == []
