import pytest

from cortex import clip
from cortex.brain import Brain
from cortex.vaults import VaultError

PAGE = """<html><head><title>Sourdough — a guide</title></head><body>
<nav>home about contact archive</nav>
<script>var tracker = 1;</script>
<style>.x{color:red}</style>
<h1>Sourdough</h1>
<p>A paragraph about starters that is comfortably longer than the forty character floor.</p>
<ul><li>Feed it daily</li><li>Use warm water</li></ul>
<footer>copyright 2026 someone</footer>
</body></html>"""


def test_extract_keeps_content_and_drops_chrome():
    got = clip.extract(PAGE, "https://example.com/sourdough")
    assert got.title == "Sourdough — a guide"
    assert "# Sourdough" in got.text
    assert "starters" in got.text
    assert "- Feed it daily" in got.text
    for junk in ("tracker", "color:red", "copyright", "home about contact"):
        assert junk not in got.text


def test_extract_survives_broken_markup():
    got = clip.extract("<html><p>unclosed paragraph that is long enough to be kept", "u")
    assert "unclosed paragraph" in got.text


def test_slugify():
    assert clip.slugify("Sourdough — a Guide!") == "sourdough-a-guide"
    assert clip.slugify("???") == "clip"


def test_save_writes_frontmatter_with_the_source(brain: Brain):
    got = clip.extract(PAGE, "https://example.com/sourdough")
    rel = clip.save(brain.config, "shared", got)
    assert rel.startswith("clips/") and rel.endswith("-sourdough-a-guide.md")
    body = (brain.config.shared_vault / rel).read_text()
    assert "source: https://example.com/sourdough" in body
    assert "clipped:" in body
    assert "# Sourdough — a guide" in body


def test_save_refuses_an_empty_page(brain: Brain):
    empty = clip.Clip(url="https://example.com", title="App", text="")
    with pytest.raises(VaultError, match="no readable text"):
        clip.save(brain.config, "shared", empty)


def test_fetch_refuses_non_http_schemes():
    for bad in ("file:///etc/passwd", "ftp://example.com", "javascript:alert(1)"):
        with pytest.raises(VaultError, match="http and https"):
            clip.fetch(bad)
