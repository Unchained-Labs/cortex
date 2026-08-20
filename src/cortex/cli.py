"""The cortex CLI: init, index, chat, serve, mcp, connectors, keys, status."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from cortex import __version__
from cortex import keys as keymod
from cortex.brain import Brain
from cortex.config import CONFIG_NAME, ConfigError, find_brain
from cortex.events import AgentEvent

CONFIG_TEMPLATE = """\
# cortex brain configuration — https://unchained-labs.github.io/cortex/
name: {name}

# Optional extra personality, appended to the system prompt verbatim.
persona: ""

# Model endpoints. kind "openai" speaks the OpenAI-compatible API
# (Ollama, vLLM, LM Studio, OpenRouter, OpenAI); kind "anthropic" speaks
# the Anthropic Messages API. Secrets belong in env vars, not this file.
providers:
  local:
    kind: openai
    base_url: "{base_url}"
    chat_model: "{chat_model}"
    embed_model: "{embed_model}"
  # claude:
  #   kind: anthropic
  #   api_key_env: ANTHROPIC_API_KEY
  #   chat_model: claude-sonnet-5

# Which provider serves which role. Without an embed role (or embed_model),
# search degrades to full-text and says so.
roles:
  chat: local
  embed: local

# External MCP servers to consume as extra tools.
# mcp_servers:
#   home-assistant:
#     transport: http
#     url: "http://homeassistant.local:8123/mcp"
#     headers: {{Authorization: "Bearer ..."}}
#   filesystem:
#     transport: stdio
#     command: npx
#     args: ["-y", "@modelcontextprotocol/server-filesystem", "/somewhere"]

# Built-in ingestion connectors (run with `cortex connectors run`).
# connectors:
#   calendar_ics:
#     urls:
#       home: "https://calendar.example/private-abc.ics"
#     days_ahead: 30

# Extra directories to index besides notes/ and sources/.
# extra_paths:
#   - ~/projects/journal

server:
  auth: none   # "none" = loopback only; "key" = require a ctx_ Bearer key
"""

WELCOME_NOTE = """\
# Welcome to your brain

This folder is a cortex brain. Drop markdown into `notes/`, run
`cortex index`, and ask questions with `cortex chat`.

- `notes/` — yours to organize; an Obsidian vault clone works as-is
- `sources/` — connector output lands here, one folder per connector
- `skills/` — agentskills.io SKILL.md procedure folders
- `plugins/` — drop-in tool plugins (*.py exposing register(registry))
- `connectors/` — drop-in ingestion connectors (*.py exposing sync(out_dir, settings))
"""


def _brain(args: argparse.Namespace) -> Brain:
    try:
        return Brain(find_brain(getattr(args, "brain", None)))
    except ConfigError as exc:
        sys.exit(f"error: {exc}")


def cmd_init(args: argparse.Namespace) -> None:
    root = Path(args.path).expanduser().resolve()
    config_path = root / CONFIG_NAME
    if config_path.exists():
        sys.exit(f"{config_path} already exists; edit it instead")
    for sub in ("notes", "sources", "skills", "plugins", "connectors", ".cortex"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        CONFIG_TEMPLATE.format(
            name=args.name or root.name,
            base_url=args.provider_url,
            chat_model=args.chat_model,
            embed_model=args.embed_model,
        ),
        encoding="utf-8",
    )
    (root / "notes" / "welcome.md").write_text(WELCOME_NOTE, encoding="utf-8")
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(".cortex/\n", encoding="utf-8")
    print(f"Brain created at {root}")
    print(f"1. Edit {config_path} (model endpoint and names)")
    print(f"2. cortex index --brain {root}")
    print(f"3. cortex chat --brain {root}")


def cmd_index(args: argparse.Namespace) -> None:
    brain = _brain(args)
    warning = brain.warn_if_public("embed")
    if warning:
        print(f"warning: {warning}", file=sys.stderr)
    embedder = brain.provider("embed")
    if embedder is not None:
        # One probe before touching files: an unreachable embed endpoint
        # degrades the run to full-text, it does not crash it.
        from cortex.providers import ProviderError

        try:
            asyncio.run(embedder.embed(["reachability probe"]))
        except ProviderError as exc:
            print(f"embeddings disabled for this run: {exc}", file=sys.stderr)
            embedder = None

    from cortex.memory.indexer import run_index

    report = asyncio.run(run_index(brain.config, brain.store, embedder))
    if report.reset:
        print("index identity changed: re-indexed from scratch")
    mode = "with embeddings" if report.embeddings else "full-text only (no embed provider)"
    print(
        f"indexed {report.indexed}, unchanged {report.unchanged}, "
        f"removed {report.removed}, skipped {report.skipped} — {mode}"
    )
    brain.close()


def cmd_chat(args: argparse.Namespace) -> None:
    brain = _brain(args)
    warning = brain.warn_if_public("chat")
    if warning:
        print(f"warning: {warning}", file=sys.stderr)
    thread = args.thread or Brain.new_thread()

    async def sink(event: AgentEvent) -> None:
        if event.type == "token":
            print(event.data["text"], end="", flush=True)
        elif event.type == "tool_start":
            print(f"\n⚙ {event.data['name']} {event.data['arguments']}", file=sys.stderr)
        elif event.type == "tool_end":
            mark = "✓" if event.data["ok"] else "✗"
            print(f"{mark} {event.data['name']} ({event.data['latency_ms']}ms)", file=sys.stderr)
        elif event.type in ("notice", "error"):
            print(f"[{event.type}] {event.data['text']}", file=sys.stderr)

    async def one(text: str) -> None:
        await brain.chat_turn(thread, text, sink)
        print()

    if args.message:
        asyncio.run(one(args.message))
        brain.close()
        return

    print(f"{brain.config.name} — thread {thread}. Ctrl-D or /quit to leave.")
    while True:
        try:
            text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/quit", "/exit"):
            break
        asyncio.run(one(text))
    brain.close()


def cmd_serve(args: argparse.Namespace) -> None:
    brain = _brain(args)
    if brain.config.server_auth == "none" and args.host not in ("127.0.0.1", "localhost", "::1"):
        sys.exit(
            f"refusing to bind {args.host} with server.auth: none — set server.auth: key "
            "in cortex.yaml and issue a key first (cortex keys issue <name>)"
        )
    warning = brain.warn_if_public("chat")
    if warning:
        print(f"warning: {warning}", file=sys.stderr)
    try:
        import uvicorn

        from cortex.server.app import build_app
    except ImportError:
        sys.exit("the server needs extras: pip install 'cortex-brain[server]'")
    app = build_app(brain)
    auth = brain.config.server_auth
    print(f"{brain.config.name} at http://{args.host}:{args.port} (auth: {auth})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def cmd_mcp(args: argparse.Namespace) -> None:
    brain = _brain(args)
    try:
        from cortex.mcp.server import serve_stdio
    except ImportError:
        sys.exit("MCP export needs extras: pip install 'cortex-brain[mcp]'")
    try:
        import mcp  # noqa: F401
    except ImportError:
        sys.exit("MCP export needs extras: pip install 'cortex-brain[mcp]'")
    asyncio.run(serve_stdio(brain))


def cmd_connectors(args: argparse.Namespace) -> None:
    brain = _brain(args)
    from cortex.connectors import run_connectors

    results = run_connectors(brain.config)
    if not results:
        print("no connectors configured (cortex.yaml connectors: block, or connectors/*.py)")
        return
    failed = False
    for name, outcome in sorted(results.items()):
        mark = "✓" if outcome == "ok" else "✗"
        failed = failed or outcome != "ok"
        print(f"{mark} {name}: {outcome}")
    print("run `cortex index` to pick up new source files")
    if failed:
        sys.exit(1)


def cmd_keys(args: argparse.Namespace) -> None:
    brain = _brain(args)
    if args.action == "issue":
        if not args.name:
            sys.exit("usage: cortex keys issue <name>")
        key = keymod.generate_key()
        brain.store.add_key(args.name, keymod.key_prefix(key), keymod.hash_key(key))
        print(f"{args.name}: {key}")
        print("shown once; only its hash is stored")
    elif args.action == "list":
        rows = brain.store.list_keys()
        if not rows:
            print("no keys issued")
        for row in rows:
            state = "enabled" if row["enabled"] else "revoked"
            print(f"{row['name']}  {row['prefix']}…  {state}  {row['created_at']}")
    elif args.action == "revoke":
        if not args.name:
            sys.exit("usage: cortex keys revoke <name>")
        if brain.store.revoke_key(args.name):
            print(f"revoked {args.name}")
        else:
            sys.exit(f"no key named {args.name!r}")


def cmd_status(args: argparse.Namespace) -> None:
    brain = _brain(args)
    config = brain.config
    print(f"cortex {__version__} — brain {config.name!r} at {config.root}")
    stats = brain.store.stats()
    print(
        f"index: {stats['files']} files, {stats['chunks']} chunks, "
        f"{stats['vectors']} vectors, {stats['facts']} facts"
    )
    for role in ("chat", "embed"):
        profile = config.provider_for(role)
        if profile is None:
            print(f"{role}: not configured")
            continue
        model = profile.chat_model if role == "chat" else profile.embed_model
        line = f"{role}: {profile.name} ({profile.kind}) model={model or 'unset'}"
        warning = brain.warn_if_public(role)
        print(line + (f" — WARNING: {warning}" if warning else ""))
    tools = [p.name for p in brain.registry.plugins()]
    print(f"tools ({len(tools)}): {', '.join(tools)}")
    if brain.registry.load_errors:
        for err in brain.registry.load_errors:
            print(f"plugin error: {err}")
    if config.mcp_servers:
        names = ", ".join(s.name for s in config.mcp_servers if s.enabled)
        print(f"mcp servers configured: {names or 'none enabled'} (attached at chat time)")
    print(f"skills: {len(brain.skills)}")
    brain.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="cortex", description="A self-hosted brain for your home, company, or any activity."
    )
    parser.add_argument("--version", action="version", version=f"cortex {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create a new brain directory")
    p.add_argument("path")
    p.add_argument("--name", default="")
    p.add_argument("--provider-url", default="http://localhost:11434/v1")
    p.add_argument("--chat-model", default="qwen3")
    p.add_argument("--embed-model", default="nomic-embed-text")
    p.set_defaults(func=cmd_init)

    for name, func, help_text in (
        ("index", cmd_index, "index notes and sources"),
        ("status", cmd_status, "what is configured, indexed, and reachable"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--brain")
        p.set_defaults(func=func)

    p = sub.add_parser("chat", help="chat with the brain in the terminal")
    p.add_argument("--brain")
    p.add_argument("--thread", default="")
    p.add_argument("-m", "--message", default="", help="one-shot message instead of a REPL")
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("serve", help="run the web chat + HTTP API")
    p.add_argument("--brain")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8642)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("mcp", help="export the brain's tools as a stdio MCP server")
    p.add_argument("--brain")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("connectors", help="run ingestion connectors")
    p.add_argument("action", choices=["run"])
    p.add_argument("--brain")
    p.set_defaults(func=cmd_connectors)

    p = sub.add_parser("keys", help="issue and revoke ctx_ server keys")
    p.add_argument("action", choices=["issue", "list", "revoke"])
    p.add_argument("name", nargs="?", default="")
    p.add_argument("--brain")
    p.set_defaults(func=cmd_keys)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
