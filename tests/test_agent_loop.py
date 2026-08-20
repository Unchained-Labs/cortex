from cortex.agent.loop import run_turn
from cortex.config import ProviderProfile
from cortex.events import AgentEvent
from cortex.plugins import ToolPlugin, ToolRegistry
from cortex.providers.base import ChatResult, ToolCall


class ScriptedProvider:
    """Returns canned results in order; records what it was asked."""

    def __init__(self, results):
        self.results = list(results)
        self.seen_messages = []
        self.profile = ProviderProfile(name="fake", kind="openai", chat_model="fake-model")

    async def chat(self, messages, tools=None, on_token=None):
        self.seen_messages.append([dict(m) for m in messages])
        result = self.results.pop(0)
        if on_token is not None and result.text:
            await on_token(result.text)
        return result

    async def embed(self, texts):
        raise NotImplementedError


def echo_registry():
    reg = ToolRegistry()
    reg.register(
        ToolPlugin(
            name="lookup",
            description="looks things up",
            parameters={"q": {"type": "string"}},
            required=("q",),
            func=lambda q: f"found:{q}",
        )
    )
    return reg


async def collect_events(provider, registry, messages):
    events: list[AgentEvent] = []

    async def sink(event: AgentEvent) -> None:
        events.append(event)

    answer = await run_turn(provider, registry, messages, sink)
    return answer, events


async def test_tool_call_then_answer():
    provider = ScriptedProvider(
        [
            ChatResult(tool_calls=[ToolCall(id="c1", name="lookup", arguments={"q": "tea"})]),
            ChatResult(text="It is tea."),
        ]
    )
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "what drink?"},
    ]
    answer, events = await collect_events(provider, echo_registry(), messages)
    assert answer == "It is tea."
    kinds = [e.type for e in events]
    assert kinds == ["start", "tool_start", "tool_end", "token", "done"]
    # second model call saw the tool result in the transcript
    second = provider.seen_messages[1]
    tool_msgs = [m for m in second if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["content"] == "found:tea"
    # transcript ends with the assistant answer
    assert messages[-1] == {"role": "assistant", "content": "It is tea."}


async def test_broken_tool_reaches_model_not_crash():
    provider = ScriptedProvider(
        [
            ChatResult(tool_calls=[ToolCall(id="c1", name="ghost", arguments={})]),
            ChatResult(text="I could not use that tool."),
        ]
    )
    answer, events = await collect_events(
        provider, echo_registry(), [{"role": "system", "content": "s"},
                                    {"role": "user", "content": "u"}]
    )
    assert answer == "I could not use that tool."
    tool_end = next(e for e in events if e.type == "tool_end")
    assert tool_end.data["ok"] is False


async def test_step_limit_stops_a_loop():
    always_call = ChatResult(
        tool_calls=[ToolCall(id="c", name="lookup", arguments={"q": "again"})]
    )
    provider = ScriptedProvider([always_call] * 32)
    answer, events = await collect_events(
        provider, echo_registry(), [{"role": "system", "content": "s"},
                                    {"role": "user", "content": "u"}]
    )
    assert "Stopped after" in answer
    assert events[-1].type == "error"
