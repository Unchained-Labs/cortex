from cortex.brain import Brain


def test_builtin_tools_registered(brain: Brain):
    names = {p.name for p in brain.registry.plugins()}
    assert {
        "search_brain", "grep_exact", "read_file", "list_sources",
        "remember", "recall", "current_time",
    } <= names


def test_read_file_traversal_and_missing_look_identical(brain: Brain):
    (brain.config.shared_vault / "real.md").write_text("line one\nline two\n", encoding="utf-8")
    escape = brain.registry.invoke("read_file", {"path": "../../etc/passwd"}).text
    missing = brain.registry.invoke("read_file", {"path": "vaults/shared/ghost.md"}).text
    assert escape.startswith("No such file")
    # identical wording modulo the echoed path, so existence outside the
    # brain is not leaked by a different message shape
    assert escape.split(":")[0] == missing.split(":")[0]
    real = brain.registry.invoke("read_file", {"path": "vaults/shared/real.md"}).text
    assert "line two" in real and "    1 |" in real


def test_remember_and_recall(brain: Brain):
    out = brain.registry.invoke(
        "remember", {"fact": "the router password is in the safe"}
    ).text
    assert out.startswith("Remembered")
    recalled = brain.registry.invoke("recall", {"query": "router"}).text
    assert "router password" in recalled
    assert brain.registry.invoke("remember", {"fact": "   "}).text.startswith("Refusing")


def test_search_brain_reports_empty_plainly(brain: Brain):
    out = brain.registry.invoke("search_brain", {"query": "zanzibar"}).text
    assert "Nothing in the brain" in out


def test_grep_exact_finds_literals(brain: Brain):
    (brain.config.shared_vault / "log.md").write_text(
        "ERR_CONN_REFUSED happened at 3am\n", encoding="utf-8"
    )
    out = brain.registry.invoke("grep_exact", {"pattern": "ERR_CONN_REFUSED"}).text
    assert "ERR_CONN_REFUSED" in out
    assert "No exact matches" in brain.registry.invoke(
        "grep_exact", {"pattern": "zzz_never_zzz"}
    ).text


def test_list_sources_counts(brain: Brain):
    (brain.config.shared_vault / "a.md").write_text("x\n", encoding="utf-8")
    out = brain.registry.invoke("list_sources", {}).text
    assert "testbrain" in out and "vaults/shared/" in out
