"""web_search and fetch_url, with the network replaced by canned responses."""

from __future__ import annotations

import httpx
import pytest

from cortex.brain import Brain
from cortex.plugins import web

DDG_HTML = """
<div class="result">
  <h2 class="result__title"><a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=abc">Example
     <b>A</b></a></h2>
  <a class="result__snippet"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">A snippet about
     <b>things</b></a>
</div>
<div class="result">
  <a class="result__a" href="https://direct.example.org/">Direct</a>
  <td class="result__snippet">plain snippet</td>
</div>
"""

PAGE = """<html><head><title>Sourdough</title></head><body><nav>menu menu menu</nav>
<h1>Sourdough</h1><p>A paragraph about starters that is comfortably longer than forty chars.</p>
</body></html>"""


class FakeResponse:
    def __init__(self, status=200, text="", ctype="text/html", payload=None):
        self.status_code = status
        self.text = text
        self.headers = {"content-type": ctype}
        self._payload = payload

    def json(self):
        return self._payload


def test_duckduckgo_parser_unwraps_redirects():
    hits = web.parse_duckduckgo(DDG_HTML)
    assert [(h.title, h.url, h.snippet) for h in hits] == [
        ("Example A", "https://example.com/a", "A snippet about things"),
        ("Direct", "https://direct.example.org/", "plain snippet"),
    ]


def test_backend_is_chosen_from_the_environment(monkeypatch):
    for key in (web.SEARCH_URL_ENV, web.BRAVE_KEY_ENV):
        monkeypatch.delenv(key, raising=False)
    assert web.backend() == "duckduckgo"
    monkeypatch.setenv(web.BRAVE_KEY_ENV, "k")
    assert web.backend() == "brave"
    monkeypatch.setenv(web.SEARCH_URL_ENV, "http://searx.local")
    assert web.backend() == "searxng"


def test_searxng_backend(monkeypatch):
    monkeypatch.setenv(web.SEARCH_URL_ENV, "http://searx.local/")
    seen = {}

    def fake_get(url, **kw):
        seen["url"], seen["params"] = url, kw.get("params")
        return FakeResponse(payload={"results": [
            {"title": "T", "url": "https://t.example", "content": "c"},
            {"title": "no url"},
        ]})

    monkeypatch.setattr(web.httpx, "get", fake_get)
    hits, which = web.search("cortex brain", k=5)
    assert which == "searxng" and seen["url"] == "http://searx.local/search"
    assert seen["params"]["format"] == "json"
    assert [h.url for h in hits] == ["https://t.example"]
    text = web.format_hits("cortex brain", hits, which)
    assert "1. T" in text and "https://t.example" in text and "fetch_url" in text


def test_duckduckgo_failure_names_the_fix(monkeypatch):
    for key in (web.SEARCH_URL_ENV, web.BRAVE_KEY_ENV):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(web.httpx, "get", lambda *a, **k: FakeResponse(status=503))
    with pytest.raises(web.WebError, match=web.SEARCH_URL_ENV):
        web.search("anything")
    monkeypatch.setattr(web.httpx, "get", lambda *a, **k: FakeResponse(text="<html></html>"))
    with pytest.raises(web.WebError, match="no results"):
        web.search("anything")


def test_fetch_extracts_html_and_passes_text_through(monkeypatch):
    monkeypatch.setattr(web.httpx, "get", lambda *a, **k: FakeResponse(text=PAGE))
    title, text = web.fetch("https://example.com/s")
    assert title == "Sourdough" and "starters" in text and "menu menu" not in text

    monkeypatch.setattr(
        web.httpx, "get", lambda *a, **k: FakeResponse(text='{"a": 1}', ctype="application/json")
    )
    assert web.fetch("https://example.com/x.json")[1] == '{"a": 1}'

    monkeypatch.setattr(
        web.httpx, "get", lambda *a, **k: FakeResponse(text="x" * 5000, ctype="text/plain")
    )
    _, cut = web.fetch("https://example.com/big.txt", max_chars=2000)
    assert "cut at 2,000 characters" in cut


def test_fetch_refusals(monkeypatch):
    with pytest.raises(web.WebError, match="http"):
        web.fetch("file:///etc/passwd")
    monkeypatch.setattr(web.httpx, "get", lambda *a, **k: FakeResponse(status=404))
    with pytest.raises(web.WebError, match="404"):
        web.fetch("https://example.com/missing")
    monkeypatch.setattr(
        web.httpx, "get", lambda *a, **k: FakeResponse(text="", ctype="image/png")
    )
    with pytest.raises(web.WebError, match="not a text page"):
        web.fetch("https://example.com/pic.png")

    def boom(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(web.httpx, "get", boom)
    with pytest.raises(web.WebError, match="could not fetch"):
        web.fetch("https://example.com/")


def test_tools_are_registered_and_answer_in_words(brain: Brain, monkeypatch):
    names = {p.name for p in brain.registry.plugins()}
    assert {"web_search", "fetch_url", "list_repos", "repo_tree", "repo_log", "repo_diff"} <= names
    monkeypatch.setattr(web.httpx, "get", lambda *a, **k: FakeResponse(text=PAGE))
    out = brain.registry.invoke("fetch_url", {"url": "https://example.com/s"}).text
    assert out.startswith("# Sourdough") and "Source: https://example.com/s" in out
    assert "Search failed" in brain.registry.invoke("web_search", {"query": "   "}).text
    assert "No repositories" in brain.registry.invoke("list_repos", {}).text
    assert "list_repos" in brain.registry.invoke("repo_tree", {"repo": "nope"}).text
