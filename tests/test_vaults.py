import io
import zipfile

import pytest

from cortex import vaults
from cortex.config import load_config


@pytest.fixture
def config(brain_dir):
    (brain_dir / "vaults" / "erwin").mkdir()
    return load_config(brain_dir)


def make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_tree_skips_hidden_and_obsidian(config):
    root = config.shared_vault
    (root / "a.md").write_text("x")
    (root / ".obsidian").mkdir()
    (root / ".obsidian" / "workspace.json").write_text("{}")
    (root / "sub").mkdir()
    (root / "sub" / "b.md").write_text("y")
    files = vaults.list_tree(config, "shared")
    assert [f["path"] for f in files] == ["a.md", "sub/b.md"]


def test_write_create_read_delete(config):
    mtime = vaults.write_file(config, "shared", "note.md", "hello", create=True)
    text, read_mtime = vaults.read_file(config, "shared", "note.md")
    assert text == "hello" and read_mtime == mtime
    with pytest.raises(vaults.VaultError, match="exists"):
        vaults.write_file(config, "shared", "note.md", "again", create=True)
    vaults.delete_file(config, "shared", "note.md")
    with pytest.raises(FileNotFoundError):
        vaults.read_file(config, "shared", "note.md")


def test_conflict_detection(config):
    import os

    vaults.write_file(config, "shared", "n.md", "v1", create=True)
    _, mtime = vaults.read_file(config, "shared", "n.md")
    # someone else saves later
    target = config.shared_vault / "n.md"
    os.utime(target, (mtime + 10, mtime + 10))
    with pytest.raises(vaults.VaultError, match="conflict"):
        vaults.write_file(config, "shared", "n.md", "v2", base_mtime=mtime)
    # without base_mtime the write goes through (explicit last-writer-wins)
    vaults.write_file(config, "shared", "n.md", "v2")


def test_traversal_refused(config):
    with pytest.raises(vaults.VaultError):
        vaults.vault_path(config, "shared", "../../cortex.yaml")
    with pytest.raises(vaults.VaultError):
        vaults.vault_path(config, "../sources", "x.md")
    with pytest.raises(vaults.VaultError, match="no vault"):
        vaults.vault_path(config, "ghost")


def test_only_text_is_editable(config):
    with pytest.raises(vaults.VaultError, match="markdown"):
        vaults.write_file(config, "shared", "evil.py", "code", create=True)


def test_import_zip_filters_and_flattens(config):
    payload = make_zip(
        {
            "MyVault/note.md": b"# hi",
            "MyVault/img.png": b"\x89PNG",
            "MyVault/.obsidian/workspace.json": b"{}",
            "MyVault/malware.exe": b"MZ",
            "../escape.md": b"nope",
        }
    )
    report = vaults.import_zip(config, "erwin", payload)
    assert report.imported == 2  # note.md + img.png
    assert (config.vaults_dir / "erwin" / "note.md").read_text() == "# hi"
    assert not (config.vaults_dir / "erwin" / "malware.exe").exists()
    assert not (config.root / "escape.md").exists()


def test_import_path(config, tmp_path):
    src = tmp_path / "obsidian-vault"
    (src / "deep").mkdir(parents=True)
    (src / "deep" / "x.md").write_text("content")
    (src / ".git").mkdir()
    (src / ".git" / "HEAD").write_text("ref")
    report = vaults.import_path(config, "shared", str(src))
    assert report.imported == 1
    assert (config.shared_vault / "deep" / "x.md").read_text() == "content"
