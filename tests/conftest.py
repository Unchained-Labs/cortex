from __future__ import annotations

from pathlib import Path

import pytest

from cortex.brain import Brain

CONFIG = """\
name: testbrain
providers: {}
roles: {}
server:
  auth: none
"""


@pytest.fixture
def brain_dir(tmp_path: Path) -> Path:
    root = tmp_path / "brain"
    for sub in ("notes", "sources", "skills", "plugins", "connectors"):
        (root / sub).mkdir(parents=True)
    (root / "cortex.yaml").write_text(CONFIG, encoding="utf-8")
    return root


@pytest.fixture
def brain(brain_dir: Path) -> Brain:
    b = Brain(brain_dir)
    yield b
    b.close()
