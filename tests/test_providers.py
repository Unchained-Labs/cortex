import json

import httpx
import pytest

from cortex.config import ProviderProfile
from cortex.providers.anthropic import AnthropicProvider, convert_messages, convert_tools
from cortex.providers.base import ProviderError
from cortex.providers.openai_compat import OpenAICompatProvider, _parse_arguments, _usage


def openai_profile(**kw):
    defaults = dict(
        name="local", kind="openai", base_url="http://model.test/v1", chat_model="m",
        embed_model="e",
    )
    defaults.update(kw)
    return ProviderProfile(**defaults)


# -- shared helpers --------------------------------------------------------


def test_usage_absent_stays_absent():
    assert _usage(None) == {}
    assert _usage({}) == {}
    got = _usage({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    assert got == {"prompt_tokens": 10, "completion_tokens": 5}


def test_parse_arguments_rejects_garbage():
    assert _parse_arguments("") == {}
    assert _parse_arguments('{"a": 1}') == {"a": 1}
    with pytest.raises(ProviderError):
        _parse_arguments("{not json")
    with pytest.raises(ProviderError):
        _parse_arguments("[1,2]")


# -- openai-compatible -----------------------------------------------------


def mock_provider(monkeypatch, handler) -> OpenAICompatProvider:
    provider = OpenAICompatProvider(openai_profile())
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    class Patched(original):
        def __init__(self, **kwargs):
            kwargs["transport"] = transport
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Patched)
    return provider


async def test_openai_blocking_chat_with_tool_calls(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "m"
        assert payload["messages"][0]["role"] == "system"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "search_brain",
                                        "arguments": '{"query": "tacos"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
        )

    provider = mock_provider(monkeypatch, handler)
    result = await provider.chat(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    )
    assert result.tool_calls[0].name == "search_brain"
    assert result.tool_calls[0].arguments == {"query": "tacos"}
    assert result.usage == {"prompt_tokens": 7, "completion_tokens": 3}


async def test_openai_streaming_assembles_text_and_tool_calls(monkeypatch):
    frames = [
        {"choices": [{"delta": {"content": "Hel"}}]},
        {"choices": [{"delta": {"content": "lo"}}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": "c1", "function": {"name": "grep_", "arguments": ""}}
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"name": "exact", "arguments": '{"pattern"'}}
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "delta": {
                        "tool_calls": [{"index": 0, "function": {"arguments": ': "x"}'}}]
                    },
                }
            ]
        },
        {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 9}},
    ]
    body = "".join(f"data: {json.dumps(f)}\n\n" for f in frames) + "data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}
        )

    seen: list[str] = []

    async def sink(piece: str) -> None:
        seen.append(piece)

    provider = mock_provider(monkeypatch, handler)
    result = await provider.chat([{"role": "user", "content": "u"}], on_token=sink)
    assert "".join(seen) == "Hello"
    assert result.text == "Hello"
    (call,) = result.tool_calls
    assert call.name == "grep_exact"
    assert call.arguments == {"pattern": "x"}
    assert result.usage == {"prompt_tokens": 5, "completion_tokens": 9}
    assert result.stop_reason == "tool_calls"


async def test_openai_embeddings_reassemble_by_index(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.2]},
                    {"index": 0, "embedding": [0.1]},
                ]
            },
        )

    provider = mock_provider(monkeypatch, handler)
    vectors = await provider.embed(["a", "b"])
    assert vectors == [[0.1], [0.2]]


async def test_openai_http_error_is_a_provider_error(monkeypatch):
    provider = mock_provider(monkeypatch, lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(ProviderError):
        await provider.chat([{"role": "user", "content": "u"}])


# -- anthropic -------------------------------------------------------------


def test_convert_messages_splits_system_and_tool_results():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "recall", "arguments": '{"query": "x"}'}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "recall", "content": "nothing"},
        {"role": "tool", "tool_call_id": "c2", "name": "recall", "content": "more"},
    ]
    system, converted = convert_messages(messages)
    assert system == "sys"
    assert converted[0] == {"role": "user", "content": "hi"}
    tool_use = converted[1]["content"][0]
    assert tool_use["type"] == "tool_use" and tool_use["input"] == {"query": "x"}
    # both tool results join one user turn
    results = converted[2]["content"]
    assert [b["type"] for b in results] == ["tool_result", "tool_result"]


def test_convert_tools_shape():
    (tool,) = convert_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "recall",
                    "description": "d",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
    )
    assert tool["name"] == "recall"
    assert "input_schema" in tool


def test_anthropic_requires_a_key():
    provider = AnthropicProvider(
        ProviderProfile(name="claude", kind="anthropic", chat_model="claude-sonnet-5")
    )
    with pytest.raises(ProviderError):
        provider._headers()


async def test_anthropic_embed_refuses_plainly():
    provider = AnthropicProvider(
        ProviderProfile(name="claude", kind="anthropic", api_key="k", chat_model="m")
    )
    with pytest.raises(ProviderError, match="no embeddings"):
        await provider.embed(["x"])
