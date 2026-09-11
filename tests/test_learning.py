"""The learning loop: the brain writes skills, signs them, and never
rewrites a person's."""

from __future__ import annotations

from cortex import extensions
from cortex.brain import Brain
from cortex.plugins.skills import Skill, parse_skill, render_skill


def tool(brain: Brain, name: str):
    return next(p for p in brain.registry.plugins() if p.name == name).func


def test_author_survives_the_roundtrip():
    skill = Skill("x", "desc", "1. do", author="cortex")
    text = render_skill(skill)
    assert "author: cortex" in text
    assert parse_skill(text) == skill
    assert parse_skill(render_skill(Skill("y", "d", "1. do"))).author == ""


def test_save_skill_writes_a_signed_skill_and_reloads(brain: Brain):
    reloads: list[int] = []
    brain._reload_hook = lambda: reloads.append(1)
    out = tool(brain, "save_skill")(
        "release-notes", "Turn merged PRs into notes", "1. Call repo_log.\n2. Group by area."
    )
    assert out.startswith("Saved skill 'release-notes'")
    md = (brain.config.skills_dir / "release-notes" / "SKILL.md").read_text()
    assert "author: cortex" in md and "Group by area" in md
    # the shelf and the registry were rebuilt at once, the agent asked for after
    assert [s.name for s in brain.skills] == ["release-notes"]
    assert reloads == [1]
    listed = extensions.list_all(brain.config, brain.store)["skills"][0]
    assert listed["author"] == "cortex" and listed["uses"] == 0

    steps = "1. Call repo_log.\n2. Group by area.\n3. Cite."
    again = tool(brain, "save_skill")("release-notes", "Turn merged PRs into notes", steps)
    assert again.startswith("Updated skill")
    assert "3. Cite." in (brain.config.skills_dir / "release-notes" / "SKILL.md").read_text()


def test_save_skill_does_not_rewrite_a_persons_skill(brain: Brain):
    extensions.write_skill(brain.config, "weekly-review", "Sunday routine", "1. Open notes.")
    out = tool(brain, "save_skill")("weekly-review", "mine now", "1. Something else entirely.")
    assert "written by a person" in out
    assert "Open notes" in (brain.config.skills_dir / "weekly-review" / "SKILL.md").read_text()


def test_save_skill_refuses_bad_input(brain: Brain):
    save = tool(brain, "save_skill")
    assert "Cannot save that name" in save("Bad Name!", "d", "1. long enough instructions here")
    assert "too short" in save("ok", "d", "1.")
    assert "description" in save("ok", "  ", "1. long enough instructions here")


def test_using_a_skill_counts(brain: Brain):
    extensions.write_skill(brain.config, "weekly-review", "Sunday routine", "1. Open notes.")
    brain.load_extensions()
    assert "Open notes" in tool(brain, "use_skill")("weekly-review")
    tool(brain, "use_skill")("weekly-review")
    tool(brain, "use_skill")("nope")
    uses = brain.store.skill_uses()
    assert uses["weekly-review"][0] == 2 and "nope" not in uses
    listed = extensions.list_all(brain.config, brain.store)["skills"][0]
    assert listed["uses"] == 2 and listed["last_used"]
