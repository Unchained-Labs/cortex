from cortex.config import load_config
from cortex.memory.indexer import run_index, scan_files
from cortex.memory.store import Store


def test_scan_skips_hidden_and_binary(tmp_path):
    root = tmp_path / "notes"
    (root / ".obsidian").mkdir(parents=True)
    (root / ".obsidian" / "workspace.json").write_text("{}")
    (root / "keep.md").write_text("hello")
    (root / "image.png").write_bytes(b"\x89PNG")
    (root / "empty.md").write_text("")
    found = scan_files([("notes", root)])
    assert list(found) == ["notes/keep.md"]


async def test_incremental_index_without_embedder(brain_dir):
    config = load_config(brain_dir)
    store = Store(config.db_path)
    (config.shared_vault / "a.md").write_text("# T\n\nalpha beta\n", encoding="utf-8")

    first = await run_index(config, store, None)
    assert first.indexed == 1 and first.reset is True and first.embeddings is False

    second = await run_index(config, store, None)
    assert second.indexed == 0 and second.unchanged == 1 and second.reset is False

    (config.shared_vault / "a.md").unlink()
    third = await run_index(config, store, None)
    assert third.removed == 1
    assert store.fts_search("alpha") == []
    store.close()


async def test_identity_change_forces_reindex(brain_dir):
    config = load_config(brain_dir)
    store = Store(config.db_path)
    (config.shared_vault / "a.md").write_text("gamma\n", encoding="utf-8")
    await run_index(config, store, None)
    # simulate an embed-model change by rewriting the identity
    store.meta_set("index_identity", "schema=1;embed=other-model")
    report = await run_index(config, store, None)
    assert report.reset is True and report.indexed == 1
    store.close()
