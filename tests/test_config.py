import pytest

from cortex.config import ConfigError, find_brain, load_config

FULL = """\
name: home
persona: "Be warm."
providers:
  local:
    kind: openai
    base_url: "http://localhost:11434/v1"
    chat_model: qwen3
    embed_model: nomic-embed-text
  claude:
    kind: anthropic
    api_key_env: ANTHROPIC_API_KEY
    chat_model: claude-sonnet-5
roles:
  chat: claude
  embed: local
mcp_servers:
  files:
    transport: stdio
    command: npx
    args: ["-y", "server-filesystem"]
    exclude: ["delete_file"]
server:
  auth: key
"""


def write(tmp_path, text):
    (tmp_path / "cortex.yaml").write_text(text, encoding="utf-8")
    return tmp_path


def test_full_config_parses(tmp_path):
    config = load_config(write(tmp_path, FULL))
    assert config.name == "home"
    assert config.provider_for("chat").name == "claude"
    assert config.provider_for("embed").embed_model == "nomic-embed-text"
    (server,) = config.mcp_servers
    assert server.args == ("-y", "server-filesystem")
    assert server.exclude == ("delete_file",)
    assert config.server_auth == "key"


def test_embed_falls_back_to_chat_provider_only_if_it_embeds(tmp_path):
    config = load_config(
        write(
            tmp_path,
            "providers:\n  local:\n    kind: openai\n    base_url: x\n"
            "    chat_model: m\n    embed_model: e\n",
        )
    )
    assert config.provider_for("embed").name == "local"

    config2 = load_config(
        write(
            tmp_path,
            "providers:\n  local:\n    kind: openai\n    base_url: x\n    chat_model: m\n",
        )
    )
    assert config2.provider_for("embed") is None


def test_unknown_provider_key_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="unknown keys"):
        load_config(write(tmp_path, "providers:\n  local:\n    kindd: openai\n"))


def test_role_pointing_nowhere_is_rejected(tmp_path):
    config = load_config(write(tmp_path, "providers:\n  a:\n    kind: openai\n"
                                         "roles:\n  chat: ghost\n"))
    with pytest.raises(ConfigError, match="unknown provider"):
        config.provider_for("chat")


def test_missing_config_says_how_to_fix(tmp_path):
    with pytest.raises(ConfigError, match="cortex init"):
        load_config(tmp_path)


def test_api_key_env_lookup(tmp_path, monkeypatch):
    config = load_config(
        write(tmp_path, "providers:\n  c:\n    kind: anthropic\n    api_key_env: TEST_KEY\n")
    )
    monkeypatch.setenv("TEST_KEY", "sekrit")
    assert config.provider_for("chat").key() == "sekrit"


def test_find_brain_walks_up(tmp_path, monkeypatch):
    write(tmp_path, "name: x\n")
    nested = tmp_path / "notes" / "deep"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.delenv("CORTEX_BRAIN", raising=False)
    assert find_brain() == tmp_path.resolve()
