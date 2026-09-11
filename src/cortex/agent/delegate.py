"""Subagents: a bounded fan-out for work that splits.

Five sources to read, four files to review, three questions to research:
one agent doing them in sequence fills its context with the first before
it reaches the last. Hermes Agent's answer is ``delegate_task``: spawn a
child with a fresh conversation, the parent's tools, and one focused
goal, and let the parent see only the summary. This is that, kept to the
shape a private brain needs:

* **Children read; the parent writes.** A subagent gets every reading and
  searching tool and none that writes to the vault, the memory, the
  identity or the shelf. What gets kept is the parent's decision, made in
  front of the person.
* **Bounded.** At most four tasks per call, three running at once, and a
  shorter loop than the parent's — a runaway child cannot spend the
  evening.
* **Same scope.** The caller's read scope travels into the children through
  the same ContextVars; a subagent sees exactly what its parent may see.
* **No memory.** A child starts from its task text alone, which is why the
  prompt asks the parent to put everything the task needs into it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from langgraph.prebuilt import create_react_agent

from cortex.agent.graph import adapt_registry
from cortex.plugins import ToolRegistry

if TYPE_CHECKING:
    from cortex.brain import Brain

MAX_TASKS = 4
MAX_CONCURRENT = 3
CHILD_RECURSION_LIMIT = 24  # ~10 model/tool rounds
MAX_ANSWER_CHARS = 3000
#: Tools that change something, or spawn more agents: the parent's alone.
BLOCKED = frozenset({
    "delegate",
    "save_skill",
    "write_note",
    "capture_note",
    "complete_task",
    "remember",
    "record_work",
    "queue_post",
    "propose_identity_change",
    "clip_url",
})

CHILD_PROMPT = """\
You are a subagent of {name}, a private self-hosted brain. The main agent gave \
you one focused task; nobody else is listening and nobody can answer a question \
back, so do not ask one. Use your tools to establish what the task asks, then \
answer with what you found: the facts, each with the index key or URL it came \
from, and what you could not establish. Cite evidence by index key (e.g. \
vaults/shared/garden.md) or URL. No preamble. Under 400 words."""


def child_registry(parent: ToolRegistry) -> ToolRegistry:
    """The parent's tools minus the ones that write or delegate."""
    registry = ToolRegistry()
    for plugin in parent.plugins():
        if plugin.name not in BLOCKED:
            registry.register(plugin)
    return registry


def _final_text(result: dict) -> str:
    for message in reversed(result.get("messages", [])):
        if getattr(message, "type", "") == "ai" and not getattr(message, "tool_calls", None):
            content = message.content
            if isinstance(content, list):
                content = " ".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            return str(content).strip()
    return ""


async def run_children(brain: Brain, tasks: list[str], context: str = "") -> list[str]:
    """Run every task as its own subagent, at most MAX_CONCURRENT at once;
    one answer per task, in order, a failure reported in its place."""
    tools = adapt_registry(child_registry(brain.registry))
    prompt = CHILD_PROMPT.format(name=brain.config.name)
    if context.strip():
        prompt += f"\n\nContext from the main agent:\n{context.strip()}"
    gate = asyncio.Semaphore(MAX_CONCURRENT)

    async def one(task: str) -> str:
        async with gate:
            try:
                # A fresh model per child: a client shared with the parent's
                # loop cannot be used from the thread this runs in.
                agent = create_react_agent(brain.fresh_chat_model(), tools, prompt=prompt)
                result = await agent.ainvoke(
                    {"messages": [("user", task)]},
                    config={"recursion_limit": CHILD_RECURSION_LIMIT},
                )
            except Exception as exc:  # noqa: BLE001 - one child's failure is a line, not a crash
                return f"(failed: {exc})"
        text = _final_text(result) or "(the subagent returned nothing)"
        if len(text) > MAX_ANSWER_CHARS:
            text = text[:MAX_ANSWER_CHARS] + " …"
        return text

    return list(await asyncio.gather(*(one(t) for t in tasks)))


def delegate(brain: Brain, tasks: list[str], context: str = "") -> str:
    """The tool body: runs on a worker thread, so it owns its own loop."""
    tasks = [t.strip() for t in (tasks or []) if isinstance(t, str) and t.strip()]
    if not tasks:
        return "Give one task per independent part of the work, as a list of strings."
    if len(tasks) > MAX_TASKS:
        return f"At most {MAX_TASKS} tasks per call; split the rest into a second call."
    answers = asyncio.run(run_children(brain, tasks, context))
    out = []
    for i, (task, answer) in enumerate(zip(tasks, answers, strict=True), start=1):
        out.append(f"## Task {i}: {task}\n{answer}")
    return "\n\n".join(out)
