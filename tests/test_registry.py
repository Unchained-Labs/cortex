import pytest

from cortex.plugins import ToolPlugin, ToolRegistry


def plugin(name="echo", func=None, required=()):
    return ToolPlugin(
        name=name,
        description="echoes",
        parameters={"text": {"type": "string"}},
        required=required,
        func=func or (lambda **kw: kw.get("text", "")),
    )


def test_register_and_invoke():
    reg = ToolRegistry()
    reg.register(plugin())
    outcome = reg.invoke("echo", {"text": "hi"})
    assert outcome.ok and outcome.text == "hi"


def test_duplicate_registration_is_an_error():
    reg = ToolRegistry()
    reg.register(plugin())
    with pytest.raises(ValueError):
        reg.register(plugin())


def test_broken_tool_is_isolated():
    def boom(**kw):
        raise RuntimeError("kaput")

    reg = ToolRegistry()
    reg.register(plugin(func=boom))
    outcome = reg.invoke("echo", {"text": "x"})
    assert not outcome.ok
    assert "kaput" in outcome.text


def test_unknown_tool():
    reg = ToolRegistry()
    outcome = reg.invoke("ghost", {})
    assert not outcome.ok and "Unknown tool" in outcome.text


def test_missing_required_argument():
    reg = ToolRegistry()
    reg.register(plugin(required=("text",)))
    outcome = reg.invoke("echo", {})
    assert not outcome.ok and "missing required" in outcome.text


def test_allowlist_none_is_everything_empty_is_nothing():
    reg = ToolRegistry()
    reg.register(plugin())
    assert len(reg.plugins(None)) == 1
    assert reg.plugins(set()) == []  # empty set must never mean "everything"
    assert len(reg.plugins({"echo"})) == 1


def test_openai_schema_shape():
    reg = ToolRegistry()
    reg.register(plugin(required=("text",)))
    (schema,) = reg.openai_tools()
    fn = schema["function"]
    assert schema["type"] == "function"
    assert fn["name"] == "echo"
    assert fn["parameters"]["required"] == ["text"]
    assert fn["parameters"]["additionalProperties"] is False


def test_directory_discovery_and_isolation(tmp_path):
    good = tmp_path / "greet.py"
    good.write_text(
        "from cortex.plugins import ToolPlugin\n"
        "def register(registry):\n"
        "    registry.register(ToolPlugin(name='greet', description='hi',\n"
        "        func=lambda **kw: 'hello'))\n",
        encoding="utf-8",
    )
    (tmp_path / "broken.py").write_text("raise RuntimeError('bad plugin')", encoding="utf-8")
    (tmp_path / "_shared.py").write_text("raise RuntimeError('must be ignored')", encoding="utf-8")

    reg = ToolRegistry()
    reg.load_directory(tmp_path)
    assert reg.get("greet") is not None
    assert len(reg.load_errors) == 1
    assert "broken.py" in reg.load_errors[0]
