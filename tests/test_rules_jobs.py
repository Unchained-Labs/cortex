import json
from datetime import datetime, timedelta

import pytest

from cortex import jobs, rules
from cortex.brain import Brain


def write(brain: Brain, rel: str, body: str, age_days: float = 0) -> None:
    import os

    target = brain.config.shared_vault / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    if age_days:
        when = (datetime.now() - timedelta(days=age_days)).timestamp()
        os.utime(target, (when, when))


def rule(**kw) -> rules.Rule:
    base = {
        "name": "test",
        "matches": [{"kind": "tag", "value": "recipe"}],
        "action": {"kind": "move", "value": "recipes"},
    }
    base.update(kw)
    return rules.parse_rule(base)


# -- matching --------------------------------------------------------------


def test_tags_come_from_inline_and_frontmatter():
    found = rules.tags_of("---\ntags: [Recipe, dinner]\n---\n\n# X\n\nsee #House and #a/b\n")
    assert found == {"recipe", "dinner", "house", "a/b"}


def test_each_condition_kind(brain: Brain):
    write(brain, "a.md", "---\narea: garden\n---\n\n#recipe\n\nboiler talk\n", age_days=100)
    text = (brain.config.shared_vault / "a.md").read_text()
    mtime = (brain.config.shared_vault / "a.md").stat().st_mtime
    now = datetime.now().timestamp()

    assert rules.matches(
        rule(matches=[{"kind": "tag", "value": "recipe"}]), "a.md", text, mtime, now
    )
    assert rules.matches(
        rule(matches=[{"kind": "path", "value": "*.md"}]), "a.md", text, mtime, now
    )
    assert rules.matches(
        rule(matches=[{"kind": "content", "value": "BOILER"}]), "a.md", text, mtime, now
    )
    assert rules.matches(
        rule(matches=[{"kind": "frontmatter", "value": "area: garden"}]), "a.md", text, mtime, now
    )
    assert rules.matches(
        rule(matches=[{"kind": "older_than_days", "value": "30"}]), "a.md", text, mtime, now
    )
    # and each one can fail
    assert not rules.matches(
        rule(matches=[{"kind": "tag", "value": "trip"}]), "a.md", text, mtime, now
    )
    assert not rules.matches(
        rule(matches=[{"kind": "older_than_days", "value": "365"}]), "a.md", text, mtime, now
    )


def test_conditions_are_anded(brain: Brain):
    write(brain, "a.md", "#recipe\n")
    both = rule(matches=[
        {"kind": "tag", "value": "recipe"},
        {"kind": "content", "value": "absent"},
    ])
    assert rules.plan(brain.config, [both]) == []


# -- validation ------------------------------------------------------------


def test_rules_refuse_dangerous_or_empty_shapes():
    with pytest.raises(rules.RuleError, match="no conditions"):
        rules.parse_rule({"name": "x", "action": {"kind": "tag", "value": "t"}})
    with pytest.raises(rules.RuleError, match="climb out"):
        rule(action={"kind": "move", "value": "../../etc"})
    with pytest.raises(rules.RuleError, match="unknown action"):
        rule(action={"kind": "delete", "value": "x"})
    with pytest.raises(rules.RuleError, match="destination"):
        rule(action={"kind": "move", "value": ""})
    with pytest.raises(rules.RuleError, match="short name"):
        rule(name="")


def test_there_is_no_delete_action():
    assert "delete" not in rules.ACTION_KINDS
    assert set(rules.ACTION_KINDS) == {"move", "tag", "archive"}


# -- planning and applying -------------------------------------------------


def test_plan_changes_nothing(brain: Brain):
    write(brain, "shakshuka.md", "#recipe\n\neggs\n")
    planned = rules.plan(brain.config, [rule()])
    assert [p.path for p in planned] == ["shakshuka.md"]
    assert planned[0].target == "recipes/shakshuka.md"
    assert (brain.config.shared_vault / "shakshuka.md").is_file()  # untouched


def test_apply_moves_and_is_idempotent(brain: Brain):
    write(brain, "shakshuka.md", "#recipe\n")
    done = rules.apply(brain.config, [rule()])
    assert done == [{"rule": "test", "action": "move", "path": "shakshuka.md",
                     "target": "recipes/shakshuka.md"}]
    assert (brain.config.shared_vault / "recipes" / "shakshuka.md").is_file()
    assert not (brain.config.shared_vault / "shakshuka.md").exists()
    # running again does nothing: it is already where the rule wants it
    assert rules.apply(brain.config, [rule()]) == []


def test_apply_never_overwrites_an_existing_note(brain: Brain):
    write(brain, "recipes/x.md", "the original\n")
    write(brain, "x.md", "#recipe\n\nthe new one\n")
    rules.apply(brain.config, [rule()])
    assert (brain.config.shared_vault / "recipes" / "x.md").read_text() == "the original\n"
    assert (brain.config.shared_vault / "recipes" / "x-2.md").read_text().endswith("the new one\n")


def test_tag_action_appends_once(brain: Brain):
    write(brain, "a.md", "boiler service\n")
    tagger = rule(matches=[{"kind": "content", "value": "boiler"}],
                  action={"kind": "tag", "value": "house"})
    rules.apply(brain.config, [tagger])
    assert "#house" in (brain.config.shared_vault / "a.md").read_text()
    assert rules.apply(brain.config, [tagger]) == []  # already tagged


def test_disabled_rules_do_nothing(brain: Brain):
    write(brain, "a.md", "#recipe\n")
    assert rules.plan(brain.config, [rule(enabled=False)]) == []


def test_suggested_rules_are_valid_and_off():
    for raw in rules.suggested_rules():
        parsed = rules.parse_rule(raw)
        assert parsed.enabled is False
        assert parsed.describe()


# -- jobs ------------------------------------------------------------------


def test_job_validation():
    with pytest.raises(jobs.JobError, match="unknown job kind"):
        jobs.parse_job({"name": "x", "kind": "launch_missiles"})
    with pytest.raises(jobs.JobError, match="shortest interval"):
        jobs.parse_job({"name": "x", "kind": "index", "interval_hours": 0.01})
    with pytest.raises(jobs.JobError, match="which connector"):
        jobs.parse_job({"name": "x", "kind": "connector"})
    with pytest.raises(jobs.JobError, match="short name"):
        jobs.parse_job({"name": "!!", "kind": "index"})


def test_job_due_logic():
    job = jobs.parse_job({"name": "x", "kind": "index", "interval_hours": 24})
    assert job.due()  # never run
    job.last_run = datetime.now().astimezone().isoformat()
    assert not job.due()
    job.last_run = (datetime.now().astimezone() - timedelta(hours=25)).isoformat()
    assert job.due()
    job.enabled = False
    assert not job.due()


def test_intervals_read_like_english():
    assert jobs.every(24) == "daily"
    assert jobs.every(1) == "hourly"
    assert jobs.every(168) == "weekly"
    assert jobs.every(0.5) == "every 30 minutes"
    assert jobs.every(6) == "every 6 hours"


def test_job_describes_itself():
    job = jobs.parse_job({
        "name": "tidy", "kind": "rules", "interval_hours": 24, "settings": {"dry_run": False}
    })
    assert job.describe() == "apply the tidying rules daily"


def test_suggested_jobs_are_valid_and_off():
    for raw in jobs.suggested_jobs():
        parsed = jobs.parse_job(raw)
        assert parsed.enabled is False
        assert parsed.describe()


def test_store_roundtrip(brain: Brain):
    brain.store.upsert_rule("r", json.dumps(rules.rule_to_dict(rule())))
    assert [r["name"] for r in brain.store.list_rules()] == ["r"]
    brain.store.record_rule_actions([
        {"rule": "r", "action": "move", "path": "a.md", "target": "recipes/a.md"}
    ])
    assert brain.store.rule_history()[0]["path"] == "a.md"
    assert brain.store.delete_rule("r")

    job = jobs.parse_job({"name": "j", "kind": "index"})
    brain.store.upsert_job(job.name, json.dumps(job.as_dict()), True)
    brain.store.record_job_run("j", "ok", "indexed 3")
    row = brain.store.list_jobs()[0]
    assert row["last_status"] == "ok" and row["last_detail"] == "indexed 3"
    assert brain.store.delete_job("j")
