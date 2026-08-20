"""The LangGraph runtime, exercised with a scripted tool-calling model."""

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from cortex.agent.graph import AgentRuntime, _args_model, adapt_registry
from cortex.events import AgentEvent
from cortex.plugins import ToolPlugin, ToolRegistry


class ScriptedModel(BaseChatModel):
    """Yields canned AIMessages in order; ignores the prompt content."""

    script: list[AIMessage]
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedModel":
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=message)])


def lookup_registry():
    registry = ToolRegistry()
    registry.register(
        ToolPlugin(
            name="lookup",
            description="looks things up",
            parameters={"q": {"type": "string", "description": "query"}},
            required=("q",),
            func=lambda q: f"found:{q}",
        )
    )
    return registry


def test_args_model_required_vs_optional():
    plugin = ToolPlugin(
        name="t",
        description="d",
        parameters={"a": {"type": "string"}, "b": {"type": "integer"}},
        required=("a",),
        func=lambda **kw: "",
    )
    model = _args_model(plugin)
    fields = model.model_fields
    assert fields["a"].is_required()
    assert not fields["b"].is_required()
    parsed = model(a="x")
    assert parsed.b is None


async def test_adapted_tool_runs_registry_isolation():
    registry = lookup_registry()
    (tool,) = adapt_registry(registry)
    assert tool.name == "lookup"
    out = await tool.ainvoke({"q": "tea"})
    assert out == "found:tea"


async def test_agent_turn_with_tool_call(brain, monkeypatch):
    model = ScriptedModel(
        script=[
            AIMessage(
                content="",
                tool_calls=[{"name": "lookup", "args": {"q": "tea"}, "id": "c1"}],
            ),
            AIMessage(content="It is tea."),
        ]
    )
    monkeypatch.setattr(brain, "chat_model", lambda: model)
    monkeypatch.setattr(brain, "chat_model_name", lambda: "scripted")
    brain.registry = lookup_registry()

    events: list[AgentEvent] = []

    async def sink(event: AgentEvent) -> None:
        events.append(event)

    async with AgentRuntime(brain) as runtime:
        answer = await runtime.run("t1", "what drink?", sink)

    assert answer == "It is tea."
    kinds = [e.type for e in events]
    assert kinds[0] == "start" and kinds[-1] == "done"
    assert "tool_start" in kinds and "tool_end" in kinds
    tool_end = next(e for e in events if e.type == "tool_end")
    assert tool_end.data["ok"] is True
    assert "found:tea" in tool_end.data["preview"]


async def test_checkpointer_remembers_across_turns(brain, monkeypatch):
    model = ScriptedModel(script=[AIMessage(content="noted."), AIMessage(content="again.")])
    monkeypatch.setattr(brain, "chat_model", lambda: model)
    monkeypatch.setattr(brain, "chat_model_name", lambda: "scripted")
    brain.registry = lookup_registry()

    async def sink(event: AgentEvent) -> None:
        pass

    async with AgentRuntime(brain) as runtime:
        await runtime.run("t1", "first", sink)
        await runtime.run("t1", "second", sink)
        state = await runtime.agent.aget_state({"configurable": {"thread_id": "t1"}})
    contents = [m.content for m in state.values["messages"]]
    assert "first" in contents and "second" in contents
