from __future__ import annotations

from pathlib import Path

import pytest

from cortex import auth
from cortex.brain import Brain

CONFIG = """\
name: testbrain
providers: {}
roles: {}
"""


@pytest.fixture
def brain_dir(tmp_path: Path) -> Path:
    root = tmp_path / "brain"
    for sub in ("vaults/shared", "sources", "skills", "plugins", "connectors"):
        (root / sub).mkdir(parents=True)
    (root / "cortex.yaml").write_text(CONFIG, encoding="utf-8")
    return root


@pytest.fixture
def brain(brain_dir: Path) -> Brain:
    b = Brain(brain_dir)
    yield b
    b.close()


def add_user(brain: Brain, username: str, password: str = "hunter2hunter2", role="member"):
    pw_hash, salt = auth.hash_password(password)
    brain.store.add_user(username, pw_hash, salt, role)
    (brain.config.vaults_dir / username).mkdir(parents=True, exist_ok=True)
