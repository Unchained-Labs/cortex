"""The learning loop: the brain writes its own skills.

Hermes Agent's argument, which this borrows, is that an agent which worked
something out once should not have to work it out again — the procedure
belongs on the shelf as a skill, and the agent is the one who knows it.
So ``save_skill`` lets the agent write ``skills/<name>/SKILL.md`` in the
same envelope a person would, and the prompt asks it to do so after a
multi-step job and to fix a skill whose instructions let it down.

Two lines the brain does not cross:

* **It signs its work.** A skill it wrote carries ``author: cortex`` and
  the Extend page says so; nobody has to wonder who wrote a procedure.
* **It does not rewrite what a person wrote.** A skill without that mark
  is someone's own instructions; the agent may say what should change,
  and the person edits it. That is the identity rule (propose, never
  make) applied to procedures, and it keeps every skill either clearly a
  person's or clearly the brain's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cortex.plugins import ToolPlugin, ToolRegistry
from cortex.plugins.skills import load_skills

if TYPE_CHECKING:
    from cortex.brain import Brain

AUTHOR = "cortex"
MIN_INSTRUCTIONS = 20


def register_learning_tools(registry: ToolRegistry, brain: Brain) -> None:
    def save_skill(name: str, description: str, instructions: str) -> str:
        from cortex import extensions

        try:
            name = extensions.validate_name(name or "")
        except extensions.ExtensionError as exc:
            return f"Cannot save that name: {exc}"
        description = " ".join((description or "").split())[:120]
        instructions = (instructions or "").strip()
        if not description:
            return "Give a one-line description that says when the skill applies."
        if len(instructions) < MIN_INSTRUCTIONS:
            return "Those instructions are too short to be a procedure; write the numbered steps."
        existing = next((s for s in load_skills(brain.config.skills_dir) if s.name == name), None)
        if existing is not None and existing.author != AUTHOR:
            return (
                f"{name} was written by a person, and the brain does not rewrite what people "
                "wrote. Say in your answer what should change and they can edit it under "
                "Settings › Extend, or save your version under another name."
            )
        extensions.write_skill(brain.config, name, description, instructions, author=AUTHOR)
        brain.request_reload()
        verb = "Updated" if existing else "Saved"
        return (
            f"{verb} skill {name!r}. It is on the shelf from the next conversation; anyone "
            "can read, edit or switch it off under Settings › Extend."
        )

    registry.register(
        ToolPlugin(
            name="save_skill",
            description=(
                "Save a procedure you have just worked out as a skill, so next time it is one "
                "call: a short name, a one-line description of when to use it, and numbered "
                "steps that name the tools. Also how you fix a skill the brain wrote whose "
                "instructions let you down. A skill a person wrote cannot be overwritten."
            ),
            parameters={
                "name": {
                    "type": "string",
                    "description": "Lowercase, hyphens allowed, e.g. release-notes",
                },
                "description": {
                    "type": "string",
                    "description": "One line: when to use it, what it produces.",
                },
                "instructions": {
                    "type": "string",
                    "description": "Numbered steps, each naming the tool to call.",
                },
            },
            required=("name", "description", "instructions"),
            func=save_skill,
        )
    )
