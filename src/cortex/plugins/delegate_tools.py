"""The ``delegate`` tool: hand independent parts of a job to subagents."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cortex.agent import delegate as delegatemod
from cortex.plugins import ToolPlugin, ToolRegistry

if TYPE_CHECKING:
    from cortex.brain import Brain


def register_delegate_tools(registry: ToolRegistry, brain: Brain) -> None:
    def delegate(tasks: list, context: str = "") -> str:
        return delegatemod.delegate(brain, tasks, context or "")

    registry.register(
        ToolPlugin(
            name="delegate",
            description=(
                "Hand independent parts of a job to subagents that run at the same time "
                "and report back — several sources to read, several files to review, "
                "several questions to research. Each subagent has your reading and "
                "searching tools, the same scope, and no memory of this conversation, so "
                "put everything a task needs into its text. Up to 4 tasks per call; you "
                "get one answer per task and decide what to keep."
            ),
            parameters={
                "tasks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "One self-contained task per subagent, in plain words.",
                },
                "context": {
                    "type": "string",
                    "description": "What every subagent should know first (optional).",
                },
            },
            required=("tasks",),
            func=delegate,
        )
    )
