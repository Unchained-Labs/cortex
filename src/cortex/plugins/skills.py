"""Skills: learned procedures in the agentskills.io SKILL.md format.

A skill is a directory under ``skills/`` holding a ``SKILL.md`` with YAML-ish
frontmatter (``name``, ``description``) and instructions in the body. Skills
load lazily: the system prompt carries only names and descriptions, and the
``use_skill`` tool fetches instructions on demand, so shelves of skills cost
almost nothing per turn. The envelope round-trips with Hermes, Claude Code,
and anything else that reads SKILL.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cortex.plugins import ToolPlugin, ToolRegistry


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    instructions: str


def parse_skill(text: str) -> Skill | None:
    """Minimal frontmatter parse — the envelope is two known scalar keys, not
    worth a YAML dependency."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    fields: dict[str, str] = {}
    body_start = len(lines)
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body_start = i + 1
            break
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip().lower()] = value.strip().strip("'\"")
    name = fields.get("name", "")
    if not name:
        return None
    return Skill(
        name=name,
        description=fields.get("description", ""),
        instructions="\n".join(lines[body_start:]).strip(),
    )


def render_skill(skill: Skill) -> str:
    return (
        f"---\nname: {skill.name}\ndescription: {skill.description}\n---\n\n"
        f"{skill.instructions}\n"
    )


def load_skills(directory: Path) -> list[Skill]:
    skills: list[Skill] = []
    if not directory.is_dir():
        return skills
    for skill_md in sorted(directory.glob("*/SKILL.md")):
        parsed = parse_skill(skill_md.read_text(encoding="utf-8"))
        if parsed is not None:
            skills.append(parsed)
    return skills


def skills_prompt(skills: list[Skill]) -> str:
    if not skills:
        return ""
    lines = ["You have these skills (procedures you know how to follow):"]
    lines += [f"- {s.name}: {s.description}" for s in skills]
    lines.append(
        "When a request matches one, call use_skill(name) and follow its instructions."
    )
    return "\n".join(lines)


def register_skill_tool(registry: ToolRegistry, skills: list[Skill]) -> None:
    if not skills:
        return
    index = {s.name: s.instructions for s in skills}

    def use_skill(name: str) -> str:
        instructions = index.get(name)
        if instructions is None:
            known = ", ".join(sorted(index)) or "none"
            return f"Unknown skill {name!r}. Known skills: {known}."
        return instructions

    registry.register(
        ToolPlugin(
            name="use_skill",
            description="Fetch the full instructions of a named skill.",
            parameters={"name": {"type": "string", "description": "Skill name."}},
            required=("name",),
            func=use_skill,
        )
    )
