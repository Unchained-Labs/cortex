"""Vault operations: tree, read, write-with-conflict-check, import.

A vault is a directory under ``vaults/``. "shared" belongs to everyone;
any other vault belongs to the user with the same name. Access decisions
live in the server layer; this module only refuses path traversal.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from cortex.config import BrainConfig

# What may live in a vault. Markdown plus the attachment types Obsidian
# commonly embeds; anything else is skipped on import and refused on write.
ALLOWED_SUFFIXES = {
    ".md", ".markdown", ".txt", ".canvas",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".pdf", ".mp3", ".m4a", ".wav", ".mp4", ".webm",
}
SKIP_DIRS = {".git", ".obsidian", ".trash", "__pycache__"}
MAX_FILE_BYTES = 50_000_000


class VaultError(ValueError):
    pass


@dataclass
class ImportReport:
    imported: int = 0
    skipped: int = 0


def vault_path(config: BrainConfig, vault: str, rel: str = "") -> Path:
    """Resolve a path inside a vault, refusing traversal."""
    if "/" in vault or vault.startswith("."):
        raise VaultError("bad vault name")
    root = (config.vaults_dir / vault).resolve()
    if not root.is_dir():
        raise VaultError(f"no vault named {vault!r}")
    if not rel:
        return root
    target = (root / rel).resolve()
    if not str(target).startswith(str(root) + "/"):
        raise VaultError("path escapes the vault")
    return target


def list_tree(config: BrainConfig, vault: str) -> list[dict]:
    root = vault_path(config, vault)
    out: list[dict] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        if any(p in SKIP_DIRS or p.startswith(".") for p in rel_parts[:-1]):
            continue
        if path.name.startswith("."):
            continue
        stat = path.stat()
        out.append(
            {
                "path": str(path.relative_to(root)),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            }
        )
    return out


def read_file(config: BrainConfig, vault: str, rel: str) -> tuple[str, float]:
    target = vault_path(config, vault, rel)
    if not target.is_file():
        raise FileNotFoundError(rel)
    return target.read_text(encoding="utf-8", errors="replace"), target.stat().st_mtime


def read_raw(config: BrainConfig, vault: str, rel: str) -> bytes:
    target = vault_path(config, vault, rel)
    if not target.is_file():
        raise FileNotFoundError(rel)
    return target.read_bytes()


def write_file(
    config: BrainConfig,
    vault: str,
    rel: str,
    text: str,
    base_mtime: float | None = None,
    create: bool = False,
) -> float:
    """Write; when ``base_mtime`` is given and the file moved past it, raise
    VaultError("conflict") so the client re-loads instead of clobbering."""
    if Path(rel).suffix.lower() not in {".md", ".markdown", ".txt", ".canvas"}:
        raise VaultError("only markdown/text files can be edited here")
    target = vault_path(config, vault, rel)
    exists = target.is_file()
    if create and exists:
        raise VaultError("exists")
    if not create and not exists:
        raise FileNotFoundError(rel)
    if exists and base_mtime is not None and target.stat().st_mtime > base_mtime + 1e-6:
        raise VaultError("conflict")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target.stat().st_mtime


def delete_file(config: BrainConfig, vault: str, rel: str) -> None:
    target = vault_path(config, vault, rel)
    if not target.is_file():
        raise FileNotFoundError(rel)
    target.unlink()


def _copy_tree(src: Path, dest_root: Path) -> ImportReport:
    report = ImportReport()
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(src).parts
        if any(p in SKIP_DIRS or p.startswith(".") for p in rel_parts):
            report.skipped += 1
            continue
        if path.suffix.lower() not in ALLOWED_SUFFIXES or path.stat().st_size > MAX_FILE_BYTES:
            report.skipped += 1
            continue
        dest = dest_root / Path(*rel_parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        report.imported += 1
    return report


def import_zip(config: BrainConfig, vault: str, zip_bytes: bytes) -> ImportReport:
    root = vault_path(config, vault)
    with tempfile.TemporaryDirectory() as td:
        archive = Path(td) / "import.zip"
        archive.write_bytes(zip_bytes)
        extracted = Path(td) / "x"
        extracted.mkdir()
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                # zip entries can traverse; resolve and verify per entry
                dest = (extracted / info.filename).resolve()
                if not str(dest).startswith(str(extracted.resolve()) + "/"):
                    continue
                if info.is_dir():
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as fh:
                    dest.write_bytes(fh.read(MAX_FILE_BYTES + 1))
        # a zip of a single top-level folder imports that folder's contents
        entries = [p for p in extracted.iterdir()]
        src = entries[0] if len(entries) == 1 and entries[0].is_dir() else extracted
        return _copy_tree(src, root)


def import_git(config: BrainConfig, vault: str, git_url: str) -> ImportReport:
    root = vault_path(config, vault)
    with tempfile.TemporaryDirectory() as td:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", git_url, td + "/repo"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            raise VaultError(f"git clone failed: {proc.stderr.strip()[:300]}")
        return _copy_tree(Path(td) / "repo", root)


def import_path(config: BrainConfig, vault: str, src_path: str) -> ImportReport:
    src = Path(src_path).expanduser()
    if not src.is_dir():
        raise VaultError(f"{src_path} is not a directory on the server")
    return _copy_tree(src, vault_path(config, vault))
