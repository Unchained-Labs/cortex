from cortex.plugins import ToolRegistry
from cortex.plugins.skills import (
    load_skills,
    parse_skill,
    register_skill_tool,
    render_skill,
    skills_prompt,
)

SKILL_MD = """---
name: weekly-review
description: How to run the weekly review
---

1. Open last week's note.
2. Carry over unfinished tasks.
"""


def test_parse_and_render_roundtrip():
    skill = parse_skill(SKILL_MD)
    assert skill is not None
    assert skill.name == "weekly-review"
    assert "Carry over" in skill.instructions
    again = parse_skill(render_skill(skill))
    assert again == skill


def test_parse_rejects_missing_frontmatter():
    assert parse_skill("just a file") is None
    assert parse_skill("---\ndescription: no name\n---\nbody") is None


def test_load_skills_from_directories(tmp_path):
    (tmp_path / "weekly-review").mkdir()
    (tmp_path / "weekly-review" / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    skills = load_skills(tmp_path)
    assert [s.name for s in skills] == ["weekly-review"]


def test_skills_prompt_lists_and_instructs():
    skills = [parse_skill(SKILL_MD)]
    prompt = skills_prompt(skills)
    assert "weekly-review" in prompt and "use_skill" in prompt
    assert skills_prompt([]) == ""


def test_use_skill_tool():
    reg = ToolRegistry()
    register_skill_tool(reg, [parse_skill(SKILL_MD)])
    assert "Carry over" in reg.invoke("use_skill", {"name": "weekly-review"}).text
    unknown = reg.invoke("use_skill", {"name": "nope"}).text
    assert "Unknown skill" in unknown and "weekly-review" in unknown
