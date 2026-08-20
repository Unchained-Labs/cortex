import pytest

from cortex import extensions
from cortex.brain import Brain

GOOD_PLUGIN = """\
from cortex.plugins import ToolPlugin


def register(registry):
    registry.register(
        ToolPlugin(name="dice", description="roll", func=lambda: "4")
    )
"""

GOOD_CONNECTOR = """\
def sync(out_dir, settings):
    (out_dir / "note.md").write_text("# from a connector\\n", encoding="utf-8")
"""


def test_validate_name():
    assert extensions.validate_name(" Weather ") == "weather"
    for bad in ("", "-x", "has space", "Dots.py", "x" * 60):
        with pytest.raises(extensions.ExtensionError):
            extensions.validate_name(bad)


def test_write_plugin_reports_its_tools(brain: Brain):
    tools = extensions.write_plugin(brain.config, "dice", GOOD_PLUGIN)
    assert tools == ["dice"]
    assert (brain.config.plugins_dir / "dice.py").is_file()
    # the registry picks it up on reload, without a restart
    brain.load_extensions()
    assert brain.registry.get("dice") is not None


def test_broken_plugin_is_refused_before_it_is_saved(brain: Brain):
    with pytest.raises(extensions.ExtensionError, match="failed to load"):
        extensions.write_plugin(brain.config, "bad", "this is not python")
    with pytest.raises(extensions.ExtensionError, match="no register"):
        extensions.write_plugin(brain.config, "bad", "x = 1")
    with pytest.raises(extensions.ExtensionError, match="registered no tools"):
        extensions.write_plugin(brain.config, "bad", "def register(registry):\n    pass\n")
    assert not (brain.config.plugins_dir / "bad.py").exists()


def test_disable_hides_a_plugin_without_touching_the_file(brain: Brain):
    extensions.write_plugin(brain.config, "dice", GOOD_PLUGIN)
    extensions.set_enabled(brain.config, brain.store, "plugin", "dice", False)
    brain.load_extensions()
    assert brain.registry.get("dice") is None
    assert (brain.config.plugins_dir / "dice.py").is_file()  # source survives

    extensions.set_enabled(brain.config, brain.store, "plugin", "dice", True)
    brain.load_extensions()
    assert brain.registry.get("dice") is not None


def test_skill_roundtrip_and_shelf(brain: Brain):
    extensions.write_skill(brain.config, "weekly-review", "Sunday routine", "1. Open notes.")
    source = extensions.read_source(brain.config, "skill", "weekly-review")
    assert source["description"] == "Sunday routine"
    assert "Open notes" in source["instructions"]
    brain.load_extensions()
    assert [s.name for s in brain.skills] == ["weekly-review"]
    assert brain.registry.get("use_skill") is not None

    extensions.set_enabled(brain.config, brain.store, "skill", "weekly-review", False)
    brain.load_extensions()
    assert brain.skills == []


def test_connector_write_settings_and_run(brain: Brain):
    from cortex.connectors import run_connectors

    extensions.write_connector(brain.config, "demo", GOOD_CONNECTOR)
    extensions.set_connector_settings(brain.store, "demo", {"enabled": True})
    settings = extensions.effective_connectors(brain.config, brain.store)
    assert settings["demo"] == {"enabled": True}

    results = run_connectors(brain.config, settings, "demo")
    assert results == {"demo": "ok"}
    assert (brain.config.sources_dir / "demo" / "note.md").is_file()

    # disabling drops it from the effective set, so it never runs
    extensions.set_enabled(brain.config, brain.store, "connector", "demo", False)
    assert "demo" not in extensions.effective_connectors(brain.config, brain.store)


def test_broken_connector_is_refused(brain: Brain):
    with pytest.raises(extensions.ExtensionError, match="no sync"):
        extensions.write_connector(brain.config, "nope", "x = 1")


def test_mcp_server_crud_and_secret_hiding(brain: Brain):
    extensions.save_mcp_server(
        brain.config,
        brain.store,
        {
            "name": "home-assistant",
            "transport": "http",
            "url": "http://ha.local/mcp",
            "headers": {"Authorization": "Bearer sekrit"},
            "exclude": ["delete_everything"],
        },
    )
    servers = brain.mcp_servers()
    assert [s.name for s in servers] == ["home-assistant"]
    assert servers[0].headers["Authorization"] == "Bearer sekrit"

    listed = extensions.list_all(brain.config, brain.store)["mcp_servers"]
    # header values never leave the server; only their names do
    assert listed[0]["detail"]["header_keys"] == ["Authorization"]
    assert "sekrit" not in str(listed)

    extensions.set_enabled(brain.config, brain.store, "mcp", "home-assistant", False)
    assert brain.mcp_servers()[0].enabled is False
    extensions.delete_extension(brain.config, brain.store, "mcp", "home-assistant")
    assert brain.mcp_servers() == []


def test_mcp_requires_transport_fields(brain: Brain):
    with pytest.raises(extensions.ExtensionError, match="needs a command"):
        extensions.save_mcp_server(
            brain.config, brain.store, {"name": "x", "transport": "stdio"}
        )
    with pytest.raises(extensions.ExtensionError, match="needs a url"):
        extensions.save_mcp_server(
            brain.config, brain.store, {"name": "x", "transport": "http"}
        )
    with pytest.raises(extensions.ExtensionError, match="stdio or http"):
        extensions.save_mcp_server(
            brain.config, brain.store, {"name": "x", "transport": "carrier-pigeon"}
        )


def test_file_defined_mcp_is_read_only(brain_dir):
    (brain_dir / "cortex.yaml").write_text(
        "name: t\nmcp_servers:\n  files:\n    transport: stdio\n    command: npx\n",
        encoding="utf-8",
    )
    brain = Brain(brain_dir)
    try:
        listed = extensions.list_all(brain.config, brain.store)["mcp_servers"]
        assert listed[0]["source"] == "file"
        for call in (
            lambda: extensions.save_mcp_server(
                brain.config, brain.store,
                {"name": "files", "transport": "stdio", "command": "x"},
            ),
            lambda: extensions.delete_extension(brain.config, brain.store, "mcp", "files"),
            lambda: extensions.set_enabled(
                brain.config, brain.store, "mcp", "files", False
            ),
        ):
            with pytest.raises(extensions.ExtensionError, match="cortex.yaml"):
                call()
    finally:
        brain.close()


def test_listing_reports_a_broken_plugin_without_failing(brain: Brain):
    (brain.config.plugins_dir / "wrong.py").write_text("raise RuntimeError('boom')")
    listed = extensions.list_all(brain.config, brain.store)["plugins"]
    (entry,) = [p for p in listed if p["name"] == "wrong"]
    assert "boom" in entry["error"]


def test_delete_removes_file_and_state(brain: Brain):
    extensions.write_plugin(brain.config, "dice", GOOD_PLUGIN)
    extensions.set_enabled(brain.config, brain.store, "plugin", "dice", False)
    extensions.delete_extension(brain.config, brain.store, "plugin", "dice")
    assert not (brain.config.plugins_dir / "dice.py").exists()
    assert not brain.store.is_disabled("plugin", "dice")


def test_scaffolds_are_valid(brain: Brain):
    assert extensions.check_plugin(extensions.scaffold("plugin")["code"], "t") == ["hello"]
    extensions.check_connector(extensions.scaffold("connector")["code"], "t")
    assert extensions.scaffold("skill")["instructions"].strip()
