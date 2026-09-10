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


# ------------------------------------------------- the approval gate

def test_an_agent_cannot_tick_a_box_under_reviews(brain: Brain) -> None:
    """The gate, enforced where it cannot be reasoned around.

    A ticked checkbox under reviews/ means "a human authorised an agent to act
    on this". The reviewing worker is told to leave every box empty. It wrote
    nine of them ticked anyway, with an empty worklog proving nobody had
    approved anything — so the rule cannot live in a brief.
    """
    out = _call(brain, "write_note", path="reviews/code-2026-09-04.md",
                text="# code review\n\n- [x] **F1 · already approved by me**\n"
                     "- [ ] **F2 · honest**")
    body = (brain.config.vaults_dir / "shared" / "reviews" / "code-2026-09-04.md").read_text()
    assert "- [x]" not in body, "an agent must not be able to approve its own finding"
    assert body.count("- [ ]") == 2
    # Said out loud, so the agent learns the rule rather than believing it
    # wrote something it did not.
    assert "reset to unticked" in out.text


def test_ticks_outside_reviews_are_left_alone(brain: Brain) -> None:
    # Elsewhere a checkbox is an ordinary to-do; ticking one is not a claim of
    # authority and must keep working.
    _call(brain, "write_note", path="lists/shopping.md", text="- [x] milk\n- [ ] bread")
    body = (brain.config.vaults_dir / "shared" / "lists" / "shopping.md").read_text()
    assert "- [x] milk" in body


def test_the_normaliser_handles_the_shapes_markdown_actually_uses(brain: Brain) -> None:
    from cortex.plugins.builtin import _untick

    body, n = _untick("- [x] a\n  * [X] b\n    - [ ] c\nnot a box [x]\n")
    assert n == 2
    # Only the CHECKBOX lines are normalised — a list marker, optional indent,
    # then the box. Both bullet styles and either case.
    boxes = [ln for ln in body.splitlines() if ln.lstrip().startswith(("-", "*"))]
    assert all("[x]" not in ln and "[X]" not in ln for ln in boxes)
    # A bracket in prose is not a checkbox and must survive untouched.
    assert "not a box [x]" in body


# ------------------------------------------------- grep_exact and dashes

def test_grep_finds_a_pattern_that_starts_with_a_dash(brain: Brain) -> None:
    """The bug that silently broke the approval loop.

    Passed positionally, ripgrep parses a leading dash as a flag: searching for
    "- [x]" — a ticked markdown checkbox — returned "rg: unrecognized flag -",
    which the caller read as "no matches". An agent asked to find approved
    findings therefore reported "nothing approved" while fifteen sat ticked on
    disk, four times a day, for days.
    """
    (brain.config.shared_vault / "reviews").mkdir(parents=True, exist_ok=True)
    (brain.config.shared_vault / "reviews" / "r.md").write_text(
        "# r\n\n- [x] **F1 · approved**\n- [ ] **F2 · not**\n", encoding="utf-8")
    brain.request_reindex()

    out = brain.registry.invoke("grep_exact", {"pattern": "- [x]"}).text
    assert "unrecognized flag" not in out
    assert "F1" in out
    # And it must not also return the unticked one — this is the whole signal.
    assert "F2" not in out


def test_grep_still_matches_ordinary_patterns(brain: Brain) -> None:
    (brain.config.shared_vault / "plain.md").write_text("hello world\n", encoding="utf-8")
    brain.request_reindex()
    assert "hello" in brain.registry.invoke("grep_exact", {"pattern": "hello"}).text


# ------------------------------------------------- approved_findings

def _review(brain: Brain, name: str, body: str) -> None:
    d = brain.config.shared_vault / "reviews"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body, encoding="utf-8")


def test_approved_findings_returns_only_ticked_ones(brain: Brain) -> None:
    _review(brain, "code-2026-09-06.md",
            "# code\n\n- [x] **F1 · ticked** — severity: high\n- [ ] **F2 · not ticked**\n")
    out = brain.registry.invoke("approved_findings", {}).text
    assert "F1" in out and "ticked" in out
    assert "F2" not in out


def test_approved_findings_skips_what_the_worklog_records(brain: Brain) -> None:
    """The worklog is the record of what was DONE.

    The tick is the human's approval and is never removed, so an item can be
    both approved and finished. Without this the agent redoes the same finding
    every run, forever.
    """
    _review(brain, "code-2026-09-06.md",
            "# code\n\n- [x] **F1 · already done**\n- [x] **F2 · fresh**\n")
    _review(brain, "_worklog.md", "# Worklog\n\n- **reviews/code-2026-09-06.md#F1** — done\n")
    out = brain.registry.invoke("approved_findings", {}).text
    assert "F2" in out
    assert "#F1" not in out


def test_approved_findings_says_so_when_there_are_none(brain: Brain) -> None:
    _review(brain, "code-2026-09-06.md", "# code\n\n- [ ] **F1 · unticked**\n")
    out = brain.registry.invoke("approved_findings", {}).text
    assert "No approved findings" in out


def test_approved_findings_survives_no_reviews_directory(brain: Brain) -> None:
    assert "nothing has been reviewed" in brain.registry.invoke(
        "approved_findings", {}).text.lower()


def test_approved_findings_keys_are_stable_and_addressable(brain: Brain) -> None:
    # The key is what the worklog records and what dedupe matches on, so it has
    # to identify one finding in one review unambiguously.
    _review(brain, "fragmentation-2026-09-06.md", "# f\n\n- [x] **F3 · a thing**\n")
    out = brain.registry.invoke("approved_findings", {}).text
    assert "reviews/fragmentation-2026-09-06.md#F3" in out


# ------------------------------------------------- record_work

def test_record_work_writes_an_entry_approved_findings_then_skips(brain: Brain) -> None:
    """The pair that closes the loop.

    The agent reported "I've documented this in the worklog" on a run where the
    worklog stayed one heading line, so the next run would have redone the same
    finding. Composing an append block is a formatting task, and one the model
    believes it completed is indistinguishable from one it did.
    """
    d = brain.config.shared_vault / "reviews"
    d.mkdir(parents=True, exist_ok=True)
    (d / "code-2026-09-06.md").write_text(
        "# code\n\n- [x] **F1 · a thing**\n- [x] **F2 · another**\n", encoding="utf-8")

    assert "#F1" in brain.registry.invoke("approved_findings", {}).text

    out = _call(brain, "record_work", key="reviews/code-2026-09-06.md#F1",
                outcome="done", summary="Fixed the thing on a branch.",
                branch="agent/code-F1", tests="42 passed")
    assert out.ok, out.text
    body = (d / "_worklog.md").read_text()
    assert "code-2026-09-06.md#F1" in body and "done" in body

    after = brain.registry.invoke("approved_findings", {}).text
    assert "#F1" not in after, "a recorded finding must not be offered again"
    assert "#F2" in after, "the others are still waiting"


def test_record_work_insists_on_a_usable_key(brain: Brain) -> None:
    # A vague key silently fails to match next run, and the work repeats forever.
    out = _call(brain, "record_work", key="the minio one", outcome="done",
                summary="Did the thing that was needed.")
    assert "approved_findings gave you" in out.text


@pytest.mark.parametrize("outcome", ["fixed", "", "DONE-ish"])
def test_record_work_rejects_an_outcome_it_cannot_read(brain: Brain, outcome: str) -> None:
    out = _call(brain, "record_work", key="reviews/x.md#F1", outcome=outcome,
                summary="Something happened here.")
    assert "outcome must be one of" in out.text


def test_a_blocked_run_is_still_recorded(brain: Brain) -> None:
    # A run that changed nothing still has to record, or it is retried forever.
    out = _call(brain, "record_work", key="reviews/x.md#F9", outcome="blocked",
                summary="Needs a credential rotation nobody but a human can do.")
    assert out.ok
    assert "blocked" in (brain.config.shared_vault / "reviews" / "_worklog.md").read_text()


def test_already_fixed_demands_evidence(brain: Brain) -> None:
    """The cheapest wrong answer, made expensive.

    An agent closed a MinIO finding as already-fixed because "neither compose
    file is present in the clankergram repo". Both were present and visible to
    it; it had not looked. That outcome removes a finding from the queue for
    good, so it is the one that has to carry proof.
    """
    out = _call(brain, "record_work", key="reviews/x.md#F1", outcome="already-fixed",
                summary="The files are not there any more.")
    assert not out.ok or "needs evidence" in out.text
    assert not (brain.config.shared_vault / "reviews" / "_worklog.md").exists()

    ok = _call(brain, "record_work", key="reviews/x.md#F1", outcome="already-fixed",
               summary="Compose files were consolidated in an earlier commit.",
               tests="ls ~/apps/clankergram/docker-compose*.yml -> no such file or directory")
    assert ok.ok, ok.text


def test_other_outcomes_do_not_need_evidence(brain: Brain) -> None:
    # 'blocked' is the honest answer when you could not check, and making it
    # expensive would push the agent back towards the cheap wrong one.
    out = _call(brain, "record_work", key="reviews/x.md#F2", outcome="blocked",
                summary="Needs a credential rotation only a human can do.")
    assert out.ok, out.text


def test_done_requires_the_branch_it_claims(brain: Brain) -> None:
    """Two consecutive runs recorded 'done' with branch "—".

    No agent/ branch existed, the working tree was clean, and the thing the
    finding was about was untouched — one reply even named a branch the worklog
    did not have. The model narrated the work instead of doing it, and the queue
    advanced anyway. 'done' means code changed; the brief requires that change
    to be on a branch, so 'done' without one is self-contradictory.
    """
    out = _call(brain, "record_work", key="reviews/x.md#F1", outcome="done",
                summary="Consolidated the service definition and removed the duplicate.")
    assert "needs the branch" in out.text
    assert not (brain.config.shared_vault / "reviews" / "_worklog.md").exists()

    ok = _call(brain, "record_work", key="reviews/x.md#F1", outcome="done",
               summary="Consolidated the service definition and removed the duplicate.",
               branch="agent/fragmentation-F1", tests="pytest -q -> 42 passed")
    assert ok.ok, ok.text


def test_blocked_still_needs_no_branch(brain: Brain) -> None:
    # The honest outcomes must stay cheap, or the agent is pushed back towards
    # the fabricated one.
    out = _call(brain, "record_work", key="reviews/x.md#F5", outcome="needs-a-human",
                summary="Requires rotating a credential nobody but Erwin can rotate.")
    assert out.ok, out.text
