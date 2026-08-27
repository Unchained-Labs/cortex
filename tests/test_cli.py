"""The command line, which had no tests at all.

`cli.py` was 551 statements at 0% coverage — the largest untested file in the
project and the first surface every user touches. These are smoke tests rather
than a full suite: every subcommand is reachable and documents itself, the ones
that read a brain work against a real temporary one, and bad input is refused
rather than traced. That catches a renamed subcommand, a broken flag, or a
crash on an empty brain, which is most of what goes wrong at this layer.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest

from cortex import cli

SUBCOMMANDS = [
    "setup", "init", "index", "status", "chat", "serve", "mcp", "connectors",
    "service", "note", "demo", "new", "templates", "clip", "today", "ext", "users",
]


def run(argv: list[str]) -> tuple[int, str, str]:
    """Run the CLI, returning (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            cli.main(argv)
        except SystemExit as e:  # argparse exits this way, including on --help
            code = int(e.code or 0)
    return code, out.getvalue(), err.getvalue()


def test_help_lists_every_subcommand():
    code, out, _ = run(["--help"])
    assert code == 0
    for name in SUBCOMMANDS:
        assert name in out, name


@pytest.mark.parametrize("name", SUBCOMMANDS)
def test_each_subcommand_documents_itself(name: str):
    code, out, _ = run([name, "--help"])
    assert code == 0, name
    assert out.strip(), name


def test_an_unknown_subcommand_is_refused():
    code, _, err = run(["definitely-not-a-command"])
    assert code != 0
    assert "invalid choice" in err or "error" in err.lower()


def test_no_arguments_prints_usage_rather_than_tracing():
    code, out, err = run([])
    assert code != 0
    assert "usage" in (out + err).lower()


# ---- commands that touch a real brain ---------------------------------------


def test_init_creates_a_brain(tmp_path: Path):
    # `init` takes the path positionally, unlike the commands that open an
    # existing brain with --brain.
    root = tmp_path / "fresh"
    code, _, _ = run(["init", str(root)])
    assert code == 0
    assert (root / "cortex.yaml").is_file()
    assert (root / "vaults").is_dir()


def test_status_reads_a_brain(brain_dir: Path):
    code, out, _ = run(["status", "--brain", str(brain_dir)])
    assert code == 0
    assert out.strip()


def test_today_on_an_empty_brain_says_so_rather_than_failing(brain_dir: Path):
    code, out, err = run(["today", "--brain", str(brain_dir)])
    assert code == 0
    assert (out + err).strip()


def test_index_on_an_empty_brain_reports_zero(brain_dir: Path):
    code, out, err = run(["index", "--brain", str(brain_dir)])
    assert code == 0
    assert "index" in (out + err).lower()


def test_note_writes_into_the_vault(brain_dir: Path):
    code, _, _ = run(["note", "--brain", str(brain_dir), "the boiler service is due in March"])
    assert code == 0
    written = list((brain_dir / "vaults").rglob("*.md"))
    assert written, "note wrote nothing"
    assert any("boiler" in p.read_text(encoding="utf-8") for p in written)


def test_users_lists_nobody_on_a_fresh_brain(brain_dir: Path):
    code, out, err = run(["users", "list", "--brain", str(brain_dir)])
    assert code == 0
    assert (out + err).strip()
