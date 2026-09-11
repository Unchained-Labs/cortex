"""Subagents: bounded, read-only, scoped, and one answer per task."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from cortex import scope
from cortex.agent import delegate as delegatemod
from cortex.agent.delegate import BLOCKED, child_registry
from cortex.brain import Brain
from cortex.plugins import ToolPlugin, ToolRegistry
from test_graph import ScriptedModel


def tool(brain: Brain, name: str):
    return next(p for p in brain.registry.plugins() if p.name == name).func


def test_children_get_reading_tools_only(brain: Brain):
    parent = {p.name for p in brain.registry.plugins()}
    child = {p.name for p in child_registry(brain.registry).plugins()}
    assert "delegate" in parent and "save_skill" in parent
    assert child == parent - BLOCKED
    assert {"search_brain", "read_file", "web_search", "related"} <= child


def test_delegate_runs_each_task_and_keeps_order(brain: Brain, monkeypatch):
    seen: list[str] = []

    def lookup(q: str) -> str:
        seen.append(q)
        return f"found:{q}"

    registry = ToolRegistry()
    registry.register(
        ToolPlugin(
            name="lookup", description="d", func=lookup,
            parameters={"q": {"type": "string"}}, required=("q",),
        )
    )
    registry.register(
        ToolPlugin(name="delegate", description="d", func=lambda: "", parameters={})
    )
    brain.registry = registry

    scripts = iter([
        [AIMessage(content="", tool_calls=[{"name": "lookup", "args": {"q": "tea"}, "id": "a"}]),
         AIMessage(content="Tea: found.")],
        [AIMessage(content="Coffee: nothing to look up.")],
    ])
    monkeypatch.setattr(brain, "fresh_chat_model", lambda: ScriptedModel(script=next(scripts)))

    with scope.scoped(("vaults/shared/",), "erwin"):
        out = delegatemod.delegate(brain, ["about tea", "about coffee"])
    assert out.index("## Task 1: about tea") < out.index("## Task 2: about coffee")
    assert "Tea: found." in out and "Coffee: nothing to look up." in out
    assert seen == ["tea"]


def test_delegate_reports_a_failed_child_in_its_place(brain: Brain, monkeypatch):
    def boom():
        raise RuntimeError("no model tonight")

    monkeypatch.setattr(brain, "fresh_chat_model", boom)
    out = delegatemod.delegate(brain, ["one"])
    assert "## Task 1: one" in out and "(failed: no model tonight)" in out


def test_delegate_bounds_its_input(brain: Brain):
    assert "one task per" in delegatemod.delegate(brain, [])
    assert "one task per" in delegatemod.delegate(brain, ["", "  "])
    assert "At most 4" in delegatemod.delegate(brain, ["a", "b", "c", "d", "e"])
    assert tool(brain, "delegate")(tasks=[]).startswith("Give one task")


def test_child_prompt_carries_the_context(brain: Brain, monkeypatch):
    prompts: list[str] = []
    real = delegatemod.create_react_agent

    def spy(model, tools, prompt=None, **kw):
        prompts.append(prompt)
        return real(model, tools, prompt=prompt, **kw)

    monkeypatch.setattr(delegatemod, "create_react_agent", spy)
    monkeypatch.setattr(
        brain, "fresh_chat_model", lambda: ScriptedModel(script=[AIMessage(content="ok")])
    )
    delegatemod.delegate(brain, ["t"], context="the house is in Lisbon")
    assert prompts and "the house is in Lisbon" in prompts[0]
    assert "subagent of testbrain" in prompts[0]
