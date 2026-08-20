from cortex.memory.chunking import CHUNK_CHARS, chunk_code, chunk_file, chunk_markdown


def test_markdown_heading_stack():
    text = "# Onboarding\n\nintro\n\n## Day one\n\nbadge and laptop\n"
    chunks = chunk_markdown(text)
    assert [c.heading for c in chunks] == ["Onboarding", "Onboarding › Day one"]
    assert chunks[1].text == "badge and laptop"


def test_markdown_sibling_headings_replace_not_nest():
    text = "# A\n\none\n\n## B\n\ntwo\n\n## C\n\nthree\n"
    chunks = chunk_markdown(text)
    assert [c.heading for c in chunks] == ["A", "A › B", "A › C"]


def test_markdown_hash_inside_fence_is_not_a_heading():
    text = "# Real\n\n```bash\n# a comment, not a heading\necho hi\n```\n"
    chunks = chunk_markdown(text)
    assert len(chunks) == 1
    assert "# a comment" in chunks[0].text


def test_short_sections_are_kept():
    chunks = chunk_markdown("# Ownership\n\nOwner: platform team\n")
    assert chunks and chunks[0].text == "Owner: platform team"


def test_code_splits_on_column_zero_defs():
    text = "import os\n\ndef alpha():\n    return 1\n\nclass Beta:\n    pass\n"
    chunks = chunk_code(text)
    assert len(chunks) == 3
    assert chunks[1].heading == "def alpha():"
    assert chunks[2].heading == "class Beta:"


def test_window_fallback_has_overlap_and_covers_everything():
    text = "x" * (CHUNK_CHARS * 2 + 100)
    chunks = chunk_file(".txt", text)
    assert len(chunks) >= 2
    assert sum(len(c.text) for c in chunks) >= len(text)  # overlap duplicates some


def test_embedding_text_carries_heading():
    chunks = chunk_markdown("# Topic\n\nbody\n")
    assert chunks[0].embedding_text() == "Topic\n\nbody"
