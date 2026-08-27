"""The cortex CLI: setup, note, today, index, chat, serve, mcp, ext, users, status."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path

import httpx

from cortex import __version__, auth
from cortex.brain import Brain
from cortex.config import CONFIG_NAME, ConfigError, find_brain
from cortex.events import AgentEvent

CONFIG_TEMPLATE = """\
# cortex brain configuration — https://unchained-labs.github.io/cortex/
name: {name}

# Optional extra personality, appended to the system prompt verbatim.
persona: ""

# Model endpoints. Kinds: openai (any OpenAI-compatible endpoint — Ollama,
# vLLM, LM Studio), openrouter (base_url optional), litellm (your LiteLLM
# proxy), anthropic. Secrets belong in env vars, not this file.
providers:
  {provider_name}:
    kind: {kind}
    base_url: "{base_url}"
    {key_line}chat_model: "{chat_model}"
    embed_model: "{embed_model}"

# Which provider serves which role. Chat and embeddings can differ; without
# an embed role (or embed_model), search degrades to full-text and says so.
roles:
  chat: {provider_name}
  embed: {provider_name}

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

# Extra directories to index besides vaults/ and sources/.
# extra_paths:
#   - ~/projects/journal
"""

WELCOME_NOTE = """\
# Start here

This is your brain. Three things to know, then delete this note.

## 1. Capture without thinking about it

The fastest way in is one line, from anywhere:

```sh
cortex note "the boiler service is due in March"
```

It lands in `journal/` under today's date. In the dashboard, press **c**
from any tab. Do not file it — search does not care which note a line is in.

## 2. Ask, and check the citation

Ask the agent in the Chat tab, or `cortex chat`. It searches before it
answers and cites the file it used; click the citation to open it. If it
says it found nothing, believe it — it is reading only what is here.

## 3. Fill it with what you already have

An empty brain cannot help you. Bring what you have already written:

- **Import** tab: an existing Obsidian vault as a zip, a git URL, or a path
- drop markdown into `vaults/shared/` (everyone) or your own vault (private)
- connect a calendar: Extend → Connectors → calendar_ics

## Today

`cortex today` lists what is on: upcoming events, open tasks, what changed.
Tasks are ordinary markdown checkboxes anywhere in your notes:

- [ ] Import my existing notes
- [ ] Ask the brain something and check the citation
- [ ] Delete this note

"""

KIND_PRESETS = {
    "ollama": ("openai", "http://localhost:11434/v1", "qwen3", "nomic-embed-text"),
    "vllm": ("openai", "http://localhost:8000/v1", "", ""),
    "openrouter": ("openrouter", "https://openrouter.ai/api/v1", "openrouter/auto", ""),
    "litellm": ("litellm", "http://localhost:4000", "", ""),
    "anthropic": ("anthropic", "", "claude-sonnet-5", ""),
    "custom": ("openai", "", "", ""),
}


def extensions_kinds() -> tuple[str, ...]:
    from cortex.extensions import KINDS

    return KINDS


def _brain(args: argparse.Namespace) -> Brain:
    try:
        return Brain(find_brain(getattr(args, "brain", None)))
    except ConfigError as exc:
        sys.exit(f"error: {exc}")


def _scaffold(root: Path, config_text: str) -> None:
    for sub in ("vaults/shared", "sources", "skills", "plugins", "connectors", ".cortex"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / CONFIG_NAME).write_text(config_text, encoding="utf-8")
    _install_bundled_skills(root)
    _install_starter_templates(root)
    (root / "identity.md").write_text(_identity_starter(), encoding="utf-8")
    welcome = root / "vaults" / "shared" / "welcome.md"
    if not welcome.exists():
        welcome.write_text(WELCOME_NOTE, encoding="utf-8")
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(".cortex/\n", encoding="utf-8")


def _identity_starter() -> str:
    from cortex import identity as identitymod

    return identitymod.STARTER


def _install_starter_templates(root: Path) -> None:
    """A new brain gets the starter templates, so "new meeting note" works
    on day one rather than after someone discovers the feature."""
    from cortex import templates as templatesmod

    directory = root / "templates"
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in templatesmod.BUILTIN.items():
        path = directory / f"{name}.md"
        if not path.exists():
            path.write_text(text, encoding="utf-8")


def _install_bundled_skills(root: Path) -> None:
    """Copy the example skills in, so a new brain has one to read and copy.

    Nothing else in the product suggests a skill is a thing you would write;
    the only surface is an admin-only panel.
    """
    import shutil

    bundled = Path(__file__).resolve().parents[2] / "examples" / "skills"
    if not bundled.is_dir():
        return
    for skill in bundled.iterdir():
        target = root / "skills" / skill.name
        if skill.is_dir() and not target.exists():
            shutil.copytree(skill, target)


def _render_config(
    name: str, kind: str, base_url: str, chat_model: str, embed_model: str
) -> str:
    key_line = ""
    if kind == "anthropic":
        key_line = "api_key_env: ANTHROPIC_API_KEY\n    "
    elif kind == "openrouter":
        key_line = "api_key_env: OPENROUTER_API_KEY\n    "
    return CONFIG_TEMPLATE.format(
        name=name,
        provider_name="claude" if kind == "anthropic" else "local"
        if kind in ("openai",) else kind,
        kind=kind,
        base_url=base_url,
        key_line=key_line,
        chat_model=chat_model,
        embed_model=embed_model,
    )


def _ping_models(base_url: str) -> str | None:
    """Best-effort reachability check; returns an error string or None."""
    try:
        res = httpx.get(f"{base_url.rstrip('/')}/models", timeout=5)
    except httpx.HTTPError as exc:
        return str(exc)
    if res.status_code >= 500:
        return f"HTTP {res.status_code}"
    return None


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def cmd_setup(args: argparse.Namespace) -> None:
    """Interactive wizard: brain, endpoint, models, admin account, done."""
    interactive = sys.stdin.isatty() and not args.non_interactive
    root = Path(args.path or (_ask("Brain directory", str(Path.home() / "brain"))
                              if interactive else "")).expanduser().resolve()
    if not str(root) or str(root) == str(Path.home()):
        sys.exit("error: give the brain its own directory")
    if (root / CONFIG_NAME).exists():
        print(f"{root} already holds a brain; keeping its cortex.yaml")
        brain = Brain(root)
    else:
        if interactive:
            print("\nWhere does the model run?")
            for i, key in enumerate(KIND_PRESETS, 1):
                print(f"  {i}. {key}")
            pick = _ask("Endpoint kind", "1")
            kinds = list(KIND_PRESETS)
            kind_key = kinds[int(pick) - 1] if pick.isdigit() and 0 < int(pick) <= len(kinds) \
                else (pick if pick in KIND_PRESETS else "custom")
        else:
            kind_key = args.kind
        kind, default_url, default_chat, default_embed = KIND_PRESETS[kind_key]
        base_url = args.base_url or (
            _ask("Base URL", default_url) if interactive else default_url
        )
        chat_model = args.chat_model or (
            _ask("Chat model", default_chat) if interactive else default_chat
        )
        embed_model = args.embed_model if args.embed_model is not None else (
            _ask("Embedding model (empty = full-text search only)", default_embed)
            if interactive else default_embed
        )
        name = args.name or (_ask("Brain name", root.name) if interactive else root.name)
        if base_url:
            error = _ping_models(base_url)
            if error:
                print(f"warning: {base_url}/models not reachable ({error}) — continuing;"
                      " fix cortex.yaml later", file=sys.stderr)
        _scaffold(root, _render_config(name, kind, base_url, chat_model, embed_model))
        brain = Brain(root)
        warning = brain.warn_if_public("chat")
        if warning:
            print(f"warning: {warning}", file=sys.stderr)

    # Seed from what they already have. An empty brain answers nothing, and
    # someone with an existing vault can go from empty to useful in one
    # prompt — the cheapest high-value moment in the whole product.
    if interactive and not any(brain.config.shared_vault.rglob("*.md")):
        source = _ask("Import an existing vault? (path or git URL, blank to skip)", "")
        if source.strip():
            from cortex import vaults as vaultmod

            try:
                if source.startswith(("http://", "https://", "git@")):
                    imported = vaultmod.import_git(brain.config, "shared", source)
                else:
                    imported = vaultmod.import_path(brain.config, "shared", source)
                print(f"imported {imported.imported} files ({imported.skipped} skipped)")
            except vaultmod.VaultError as exc:
                print(f"import failed: {exc}", file=sys.stderr)

    if brain.store.count_users() == 0:
        print("\nCreate the first (admin) dashboard account.")
        while True:
            username = (args.admin_user or
                        (_ask("Admin username", getpass.getuser().lower())
                         if interactive else "admin")).strip().lower()
            try:
                auth.validate_username(username)
                break
            except auth.AuthError as exc:
                if not interactive:
                    sys.exit(f"error: {exc}")
                print(f"  {exc}")
                args.admin_user = None
        password = args.admin_password or (
            getpass.getpass("Admin password (8+ chars): ") if interactive else ""
        )
        if len(password) < 8:
            sys.exit("error: password must be 8+ characters")
        pw_hash, salt = auth.hash_password(password)
        brain.store.add_user(username, pw_hash, salt, "admin")
        (brain.config.vaults_dir / username).mkdir(parents=True, exist_ok=True)
        print(f"admin account {username!r} created")

    # Index now rather than printing it as homework: an unindexed brain
    # finds nothing, which looks like a broken product rather than an empty
    # one.
    print("\nIndexing…")
    embedder = brain.embedder()
    if embedder is not None:
        from cortex.providers import ProviderError

        try:
            asyncio.run(embedder.embed(["reachability probe"]))
        except ProviderError:
            embedder = None
    from cortex.memory.indexer import run_index

    report = asyncio.run(run_index(brain.config, brain.store, embedder))
    mode = "with embeddings" if embedder else "full-text only"
    print(f"indexed {report.indexed} files — {mode}")
    brain.close()

    print(f"\nBrain ready at {root}")
    print(f"  cortex serve --brain {root} --host 0.0.0.0   # dashboard on :8642")
    if report.indexed <= 1:
        print("\nNothing in it yet? Add example notes to try it out:")
        print(f"  cortex demo --brain {root}")
    print("\nThe two you will use every day:")
    print('  cortex note "something you want to remember"')
    print("  cortex today")
    print("\nKeep it running across reboots:  cortex service install")


def cmd_init(args: argparse.Namespace) -> None:
    root = Path(args.path).expanduser().resolve()
    if (root / CONFIG_NAME).exists():
        sys.exit(f"{root / CONFIG_NAME} already exists; edit it instead")
    kind, default_url, default_chat, default_embed = KIND_PRESETS["ollama"]
    _scaffold(
        root,
        _render_config(
            args.name or root.name,
            kind,
            args.provider_url or default_url,
            args.chat_model or default_chat,
            args.embed_model if args.embed_model is not None else default_embed,
        ),
    )
    print(f"Brain created at {root} — run `cortex setup {root}` to add accounts,")
    print(f"or edit {root / CONFIG_NAME} and run `cortex index --brain {root}`.")


def cmd_index(args: argparse.Namespace) -> None:
    brain = _brain(args)
    warning = brain.warn_if_public("embed")
    if warning:
        print(f"warning: {warning}", file=sys.stderr)
    embedder = brain.embedder()
    if embedder is not None:
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
    mode = "with embeddings" if embedder else "full-text only (no embed provider)"
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

    async def session() -> None:
        async with brain.runtime() as runtime:
            for err in runtime.mcp_errors:
                print(f"[mcp] {err}", file=sys.stderr)

            async def one(text: str) -> None:
                answer = await runtime.run(thread, text, sink)
                brain.record_turn(thread, "owner", text, answer)
                print()

            if args.message:
                await one(args.message)
                return
            print(f"{brain.config.name} — thread {thread}. Ctrl-D or /quit to leave.")
            while True:
                try:
                    text = await asyncio.to_thread(input, "\n> ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    return
                text = text.strip()
                if not text:
                    continue
                if text in ("/quit", "/exit"):
                    return
                await one(text)

    asyncio.run(session())
    brain.close()


def cmd_serve(args: argparse.Namespace) -> None:
    brain = _brain(args)
    if brain.store.count_users() == 0:
        sys.exit("no dashboard accounts exist yet — run `cortex setup` first")
    warning = brain.warn_if_public("chat")
    if warning:
        print(f"warning: {warning}", file=sys.stderr)
    import uvicorn

    from cortex.server.app import build_app

    app = build_app(brain)
    print(f"{brain.config.name} dashboard at http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def cmd_mcp(args: argparse.Namespace) -> None:
    brain = _brain(args)
    from cortex.mcp.server import serve_stdio

    asyncio.run(serve_stdio(brain))


def cmd_connectors(args: argparse.Namespace) -> None:
    brain = _brain(args)
    from cortex.connectors import run_connectors
    from cortex.extensions import effective_connectors

    settings = effective_connectors(brain.config, brain.store)
    results = run_connectors(brain.config, settings)
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


def cmd_note(args: argparse.Namespace) -> None:
    """Capture a thought. The fastest path into the brain:

        cortex note "the boiler service is due in March"
        echo "..." | cortex note
    """
    from cortex.capture import append_note
    from cortex.vaults import VaultError

    text = " ".join(args.text).strip()
    if not text:
        if sys.stdin.isatty():
            sys.exit('usage: cortex note "what you want to remember"')
        text = sys.stdin.read().strip()
    brain = _brain(args)
    try:
        rel, line, _ = append_note(brain.config, args.vault, text, source="via cli")
    except VaultError as exc:
        brain.close()
        sys.exit(f"error: {exc}")
    print(f"vaults/{args.vault}/{rel}")
    print(line)
    brain.close()


def cmd_demo(args: argparse.Namespace) -> None:
    """Add (or remove) example notes, for a brain that is still empty."""
    from cortex import demo

    brain = _brain(args)
    if args.remove:
        count = demo.remove(brain.config, args.vault)
        print(f"removed {count} example notes")
    else:
        written = demo.install(brain.config, args.vault)
        if not written:
            print("examples are already installed")
        else:
            for rel in written:
                print(f"vaults/{args.vault}/{rel}")
            print(f"\n{len(written)} example notes added. Remove them with "
                  "`cortex demo --remove`.")
    print("run `cortex index` to make them searchable")
    brain.close()


def cmd_new(args: argparse.Namespace) -> None:
    """Start a note from a template."""
    from cortex import templates as templatesmod

    brain = _brain(args)
    if not args.template:
        found = templatesmod.list_templates(brain.config)
        if not found:
            print("no templates yet — `cortex templates install` adds the starter set")
        for template in found:
            print(f"{template.name:<16} {template.title}  → {template.target}")
        brain.close()
        return
    template = templatesmod.get(brain.config, args.template)
    if template is None:
        brain.close()
        sys.exit(f"no template named {args.template!r}")
    try:
        rel, _ = templatesmod.create_note(
            brain.config, template, args.vault, " ".join(args.title)
        )
    except templatesmod.TemplateError as exc:
        brain.close()
        sys.exit(f"error: {exc}")
    print(f"vaults/{args.vault}/{rel}")
    brain.close()


def cmd_templates(args: argparse.Namespace) -> None:
    from cortex import templates as templatesmod

    brain = _brain(args)
    written = templatesmod.install_builtin(brain.config)
    if written:
        print("added: " + ", ".join(written))
    else:
        print("the starter templates are already there")
    print(f"they live in {brain.config.templates_dir} — edit them freely")
    brain.close()


def cmd_clip(args: argparse.Namespace) -> None:
    """Save a web page into the brain as markdown."""
    from cortex import clip as clipper
    from cortex.vaults import VaultError

    brain = _brain(args)
    try:
        clip = clipper.fetch(args.url)
        rel = clipper.save(brain.config, args.vault, clip)
    except VaultError as exc:
        brain.close()
        sys.exit(f"error: {exc}")
    words = len(clip.text.split())
    print(f"vaults/{args.vault}/{rel}")
    print(f"{clip.title} — {words} words")
    brain.close()


def cmd_today(args: argparse.Namespace) -> None:
    """What is on today, computed without asking a model."""
    from cortex.digest import build_digest, format_digest

    brain = _brain(args)
    print(format_digest(build_digest(brain.config, brain.store, vault=args.vault)))
    brain.close()


def cmd_service(args: argparse.Namespace) -> None:
    """Generate (and optionally install) a systemd user unit."""
    from cortex import service

    brain = _brain(args)
    text = service.unit_text(
        brain.config.root, args.host, args.port, brain.config.name,
        env={"CORTEX_BRAIN": str(brain.config.root)},
    )
    brain.close()
    if args.action == "print":
        print(text, end="")
        return
    if not service.systemd_available():
        print(
            "systemd --user is not available here. The unit text follows; adapt it "
            "for your init system, or just run `cortex serve` under whatever you use.\n",
            file=sys.stderr,
        )
        print(text, end="")
        return
    path = service.install(text)
    print(f"wrote {path}")
    print("\nStart it, and have it come back after a reboot:")
    print("  systemctl --user daemon-reload")
    print("  systemctl --user enable --now cortex")
    print("\nIf the brain should run without you being logged in:")
    print(f"  sudo loginctl enable-linger {os.environ.get('USER', 'you')}")


def cmd_ext(args: argparse.Namespace) -> None:
    """List, enable, disable, or delete extensions from the terminal.

    Creating plugins is deliberately not here: writing code belongs in an
    editor or the dashboard panel, not in shell arguments."""
    from cortex import extensions

    brain = _brain(args)
    if args.action == "list":
        listing = extensions.list_all(brain.config, brain.store)
        for section in ("plugins", "skills", "connectors", "mcp_servers"):
            rows = listing[section]
            print(f"\n{section} ({len(rows)}):")
            for row in rows:
                mark = " " if row["enabled"] else "-"
                extra = ", ".join(row["tools"]) or row["description"] or ""
                where = "" if row["source"] == "dashboard" else f" [{row['source']}]"
                print(f"  {mark} {row['name']}{where}{'  ' + extra if extra else ''}")
                if row["error"]:
                    print(f"      error: {row['error']}")
        for err in brain.registry.load_errors:
            print(f"\nplugin load error: {err}")
    else:
        if not args.kind or not args.name:
            sys.exit(f"usage: cortex ext {args.action} <kind> <name>")
        try:
            if args.action == "delete":
                extensions.delete_extension(brain.config, brain.store, args.kind, args.name)
                print(f"deleted {args.kind} {args.name}")
            else:
                enabled = args.action == "enable"
                extensions.set_enabled(
                    brain.config, brain.store, args.kind, args.name, enabled
                )
                print(f"{args.action}d {args.kind} {args.name}")
        except extensions.ExtensionError as exc:
            sys.exit(f"error: {exc}")
    brain.close()


def _read_password(args: argparse.Namespace, prompt: str) -> str:
    """A password, from stdin when asked or from the terminal otherwise.

    `--password-stdin` exists because `getpass` needs a terminal, which made
    every form of automated provisioning impossible — a container's first run, a
    CI fixture, a setup script. Reading stdin rather than taking a `--password`
    flag keeps it out of the process list and out of shell history, which is the
    same reason `docker login` does it this way.
    """
    if getattr(args, "password_stdin", False):
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass(prompt)
    if len(password) < 8:
        sys.exit("error: password must be 8+ characters")
    return password


def cmd_users(args: argparse.Namespace) -> None:
    brain = _brain(args)
    if args.action == "add":
        if not args.name:
            sys.exit("usage: cortex users add <name> [--role admin|member]")
        try:
            username = auth.validate_username(args.name.strip().lower())
        except auth.AuthError as exc:
            sys.exit(f"error: {exc}")
        if brain.store.get_user(username):
            sys.exit(f"user {username!r} exists")
        password = _read_password(args, "Password (8+ chars): ")
        pw_hash, salt = auth.hash_password(password)
        brain.store.add_user(username, pw_hash, salt, args.role)
        (brain.config.vaults_dir / username).mkdir(parents=True, exist_ok=True)
        print(f"added {username} ({args.role})")
    elif args.action == "list":
        rows = brain.store.list_users()
        if not rows:
            print("no users — run `cortex setup`")
        for row in rows:
            print(f"{row['username']}  {row['role']}  {row['created_at']}")
    elif args.action == "passwd":
        if not args.name:
            sys.exit("usage: cortex users passwd <name>")
        if brain.store.get_user(args.name) is None:
            sys.exit(f"no user named {args.name!r}")
        password = _read_password(args, "New password (8+ chars): ")
        pw_hash, salt = auth.hash_password(password)
        brain.store.set_password(args.name, pw_hash, salt)
        print(f"password changed for {args.name}")
    elif args.action == "remove":
        if not args.name:
            sys.exit("usage: cortex users remove <name>")
        if brain.store.delete_user(args.name):
            print(f"removed {args.name}")
            print(f"their vault is kept at {brain.config.vaults_dir / args.name}")
        else:
            sys.exit(f"no user named {args.name!r}")
    brain.close()


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
    for err in brain.registry.load_errors:
        print(f"plugin error: {err}")
    if config.mcp_servers:
        names = ", ".join(s.name for s in config.mcp_servers if s.enabled)
        print(f"mcp servers configured: {names or 'none enabled'} (attached at agent start)")
    print(f"skills: {len(brain.skills)}")
    print(f"users: {brain.store.count_users()}")
    brain.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="cortex", description="A self-hosted brain for your home, company, or any activity."
    )
    parser.add_argument("--version", action="version", version=f"cortex {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup", help="interactive wizard: brain, endpoint, admin account")
    p.add_argument("path", nargs="?", default="")
    p.add_argument("--non-interactive", action="store_true")
    p.add_argument("--kind", choices=list(KIND_PRESETS), default="ollama")
    p.add_argument("--name", default="")
    p.add_argument("--base-url", default="")
    p.add_argument("--chat-model", default="")
    p.add_argument("--embed-model", default=None)
    p.add_argument("--admin-user", default="")
    p.add_argument("--admin-password", default="")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("init", help="scaffold a brain directory without prompts")
    p.add_argument("path")
    p.add_argument("--name", default="")
    p.add_argument("--provider-url", default="")
    p.add_argument("--chat-model", default="")
    p.add_argument("--embed-model", default=None)
    p.set_defaults(func=cmd_init)

    for name, func, help_text in (
        ("index", cmd_index, "index vaults and sources"),
        ("status", cmd_status, "what is configured, indexed, and reachable"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--brain")
        p.set_defaults(func=func)

    p = sub.add_parser("chat", help="chat with the brain in the terminal (box owner scope)")
    p.add_argument("--brain")
    p.add_argument("--thread", default="")
    p.add_argument("-m", "--message", default="", help="one-shot message instead of a REPL")
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("serve", help="run the dashboard")
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

    p = sub.add_parser("service", help="run the dashboard as a systemd user service")
    p.add_argument("action", choices=["print", "install"])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8642)
    p.add_argument("--brain")
    p.set_defaults(func=cmd_service)

    p = sub.add_parser("note", help="capture a line into today's daily note")
    p.add_argument("text", nargs="*", help="the thought; omit to read stdin")
    p.add_argument("--vault", default="shared")
    p.add_argument("--brain")
    p.set_defaults(func=cmd_note)

    p = sub.add_parser("demo", help="add example notes so an empty brain has something to find")
    p.add_argument("--remove", action="store_true", help="delete the examples again")
    p.add_argument("--vault", default="shared")
    p.add_argument("--brain")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("new", help="start a note from a template")
    p.add_argument("template", nargs="?", default="", help="omit to list templates")
    p.add_argument("title", nargs="*")
    p.add_argument("--vault", default="shared")
    p.add_argument("--brain")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("templates", help="install the starter note templates")
    p.add_argument("action", choices=["install"])
    p.add_argument("--brain")
    p.set_defaults(func=cmd_templates)

    p = sub.add_parser("clip", help="save a web page into the brain as markdown")
    p.add_argument("url")
    p.add_argument("--vault", default="shared")
    p.add_argument("--brain")
    p.set_defaults(func=cmd_clip)

    p = sub.add_parser("today", help="what is on today: events, tasks, recent changes")
    p.add_argument("--vault", default="shared")
    p.add_argument("--brain")
    p.set_defaults(func=cmd_today)

    p = sub.add_parser("ext", help="list, enable, disable or delete extensions")
    p.add_argument("action", choices=["list", "enable", "disable", "delete"])
    p.add_argument("kind", nargs="?", default="", choices=["", *extensions_kinds()])
    p.add_argument("name", nargs="?", default="")
    p.add_argument("--brain")
    p.set_defaults(func=cmd_ext)

    p = sub.add_parser("users", help="manage dashboard accounts")
    p.add_argument("action", choices=["add", "list", "passwd", "remove"])
    p.add_argument("name", nargs="?", default="")
    p.add_argument("--role", choices=["admin", "member"], default="member")
    p.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from stdin instead of prompting, for scripts and containers",
    )
    p.add_argument("--brain")
    p.set_defaults(func=cmd_users)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
