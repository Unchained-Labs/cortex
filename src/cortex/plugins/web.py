"""The agent's window on the internet: search, and read a page.

Two tools, deliberately plain. ``web_search`` returns titles, URLs and
snippets; ``fetch_url`` returns a page's readable text. Everything else a
"deep research" run needs — deciding what to search next, cross-checking
claims, writing the report into the vault — is procedure, and lives in the
``deep-research`` skill where a person can read and edit it.

## Search backends

Search needs a backend, and the honest default for a self-hosted brain is
one you host. In order of preference:

* **SearXNG** — set ``CORTEX_SEARCH_URL`` to your instance (JSON format must
  be enabled in its settings). Nothing leaves your network but the queries
  SearXNG itself makes.
* **Brave Search API** — set ``BRAVE_SEARCH_API_KEY``. A real API with a free
  tier.
* **DuckDuckGo's HTML endpoint** — no key, no setup, and the fallback when
  neither variable is set. It is a scrape of a page meant for browsers, so
  it can break without notice; when it does the tool says so rather than
  returning an empty list that looks like "nothing found".

Both variables are read from ``.env`` beside cortex.yaml, like every other
secret.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from cortex.plugins import ToolPlugin, ToolRegistry

if TYPE_CHECKING:
    from cortex.brain import Brain

SEARCH_URL_ENV = "CORTEX_SEARCH_URL"
BRAVE_KEY_ENV = "BRAVE_SEARCH_API_KEY"
TIMEOUT = 20.0
MAX_RESULTS = 10
MAX_PAGE_CHARS = 12_000
USER_AGENT = "cortex/1.0 (+https://github.com/Unchained-Labs/cortex)"


@dataclass
class Hit:
    title: str
    url: str
    snippet: str


class WebError(RuntimeError):
    pass


def backend() -> str:
    if os.environ.get(SEARCH_URL_ENV, "").strip():
        return "searxng"
    if os.environ.get(BRAVE_KEY_ENV, "").strip():
        return "brave"
    return "duckduckgo"


# -- backends ----------------------------------------------------------------


def _searxng(query: str, k: int) -> list[Hit]:
    base = os.environ[SEARCH_URL_ENV].strip().rstrip("/")
    res = httpx.get(
        f"{base}/search",
        params={"q": query, "format": "json"},
        timeout=TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    )
    if res.status_code != 200:
        raise WebError(
            f"SearXNG at {base} answered HTTP {res.status_code} — is `format: json` "
            "enabled in its settings.yml?"
        )
    rows = res.json().get("results") or []
    return [
        Hit(str(r.get("title") or ""), str(r.get("url") or ""), str(r.get("content") or ""))
        for r in rows[:k]
        if r.get("url")
    ]


def _brave(query: str, k: int) -> list[Hit]:
    res = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": k},
        timeout=TIMEOUT,
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": os.environ[BRAVE_KEY_ENV].strip(),
            "User-Agent": USER_AGENT,
        },
    )
    if res.status_code != 200:
        raise WebError(f"Brave Search answered HTTP {res.status_code}")
    rows = ((res.json().get("web") or {}).get("results")) or []
    return [
        Hit(str(r.get("title") or ""), str(r.get("url") or ""), str(r.get("description") or ""))
        for r in rows[:k]
        if r.get("url")
    ]


class _DDGParser(HTMLParser):
    """Pull result links and snippets out of html.duckduckgo.com.

    The markup is stable enough to be worth twenty lines and fragile enough
    that everything is optional: a result with no snippet is still a result.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hits: list[Hit] = []
        self._in_title = False
        self._in_snippet = False
        self._buf: list[str] = []
        self._href = ""

    def handle_starttag(self, tag: str, attrs) -> None:
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "a" and "result__a" in classes:
            self._in_title = True
            self._href = a.get("href") or ""
            self._buf = []
        elif "result__snippet" in classes:
            self._in_snippet = True
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if self._in_title and tag == "a":
            self._in_title = False
            title = " ".join("".join(self._buf).split())
            url = _ddg_target(self._href)
            if url:
                self.hits.append(Hit(title, url, ""))
        elif self._in_snippet and tag in ("a", "td", "div", "span"):
            self._in_snippet = False
            if self.hits and not self.hits[-1].snippet:
                self.hits[-1].snippet = " ".join("".join(self._buf).split())

    def handle_data(self, data: str) -> None:
        if self._in_title or self._in_snippet:
            self._buf.append(data)


def _ddg_target(href: str) -> str:
    """DuckDuckGo wraps each result in a redirect; the real URL is its
    ``uddg`` parameter."""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in (parsed.hostname or "") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return target
    return href if parsed.scheme in ("http", "https") else ""


def parse_duckduckgo(html: str) -> list[Hit]:
    parser = _DDGParser()
    parser.feed(html)
    parser.close()
    return parser.hits


def _duckduckgo(query: str, k: int) -> list[Hit]:
    res = httpx.get(
        "https://html.duckduckgo.com/html/?" + urlencode({"q": query}),
        timeout=TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    if res.status_code != 200:
        raise WebError(
            f"DuckDuckGo answered HTTP {res.status_code}. For reliable search set "
            f"{SEARCH_URL_ENV} (a SearXNG instance) or {BRAVE_KEY_ENV} in .env."
        )
    hits = parse_duckduckgo(res.text)
    if not hits and "result" not in res.text:
        raise WebError(
            "DuckDuckGo returned a page with no results in it — the scrape may have "
            f"broken or been rate-limited. Set {SEARCH_URL_ENV} or {BRAVE_KEY_ENV} in .env "
            "for a real search backend."
        )
    return hits[:k]


def search(query: str, k: int = 8) -> tuple[list[Hit], str]:
    """(hits, backend name)."""
    query = " ".join((query or "").split())
    if not query:
        raise WebError("search for what?")
    k = max(1, min(int(k or 8), MAX_RESULTS))
    which = backend()
    try:
        if which == "searxng":
            return _searxng(query, k), which
        if which == "brave":
            return _brave(query, k), which
        return _duckduckgo(query, k), which
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
        raise WebError(f"search failed ({which}): {exc}") from exc


def format_hits(query: str, hits: list[Hit], which: str) -> str:
    if not hits:
        return f"No results for {query!r} (via {which})."
    out = [f"{len(hits)} result(s) for {query!r} via {which}:"]
    for i, h in enumerate(hits, start=1):
        line = f"{i}. {h.title or h.url}\n   {h.url}"
        if h.snippet:
            line += f"\n   {h.snippet[:300]}"
        out.append(line)
    out.append("Read one with fetch_url. Cite the URL for anything you take from it.")
    return "\n".join(out)


# -- pages -------------------------------------------------------------------


def fetch(url: str, max_chars: int = MAX_PAGE_CHARS) -> tuple[str, str]:
    """(title, text) of a page: readable text for HTML, the body for text
    and JSON, and a refusal for anything else."""
    from cortex.clip import MAX_BYTES, extract

    url = (url or "").strip()
    if urlparse(url).scheme not in ("http", "https"):
        raise WebError("only http and https URLs can be fetched")
    try:
        res = httpx.get(
            url, timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )
    except httpx.HTTPError as exc:
        raise WebError(f"could not fetch {url}: {exc}") from exc
    if res.status_code != 200:
        raise WebError(f"{url} returned HTTP {res.status_code}")
    ctype = res.headers.get("content-type", "").lower()
    body = res.text[:MAX_BYTES]
    if "html" in ctype or (not ctype and "<html" in body[:2000].lower()):
        clip = extract(body, url)
        title, text = clip.title, clip.text
    elif ctype.startswith("text/") or "json" in ctype or "xml" in ctype:
        title, text = urlparse(url).path.rsplit("/", 1)[-1] or url, body
    else:
        raise WebError(f"{url} is {ctype.split(';')[0] or 'binary'}, not a text page")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise WebError(
            "no readable text on that page (it may render with JavaScript, which "
            "the fetcher cannot run)"
        )
    cut = max(2000, min(int(max_chars or MAX_PAGE_CHARS), 60_000))
    if len(text) > cut:
        text = text[:cut].rstrip() + f"\n\n… cut at {cut:,} characters of {len(text):,}."
    return unescape(title).strip(), text


def register_web_tools(registry: ToolRegistry, brain: Brain) -> None:
    def web_search(query: str, k: int = 8) -> str:
        try:
            hits, which = search(query, k)
        except WebError as exc:
            return f"Search failed: {exc}"
        return format_hits(query, hits, which)

    def fetch_url(url: str, max_chars: int = MAX_PAGE_CHARS) -> str:
        try:
            title, text = fetch(url, max_chars)
        except WebError as exc:
            return f"Could not fetch that: {exc}"
        return f"# {title}\nSource: {url}\n\n{text}"

    registry.register(
        ToolPlugin(
            name="web_search",
            description=(
                "Search the web. Use it for anything the brain does not know: current "
                "events, documentation, a library's API, prices, a person or company in "
                "the news. Returns titles, URLs and snippets; read a result with "
                "fetch_url before relying on it, and cite the URL."
            ),
            parameters={
                "query": {"type": "string", "description": "What to search for."},
                "k": {"type": "integer", "description": "Results to return (default 8)."},
            },
            required=("query",),
            func=web_search,
        )
    )
    registry.register(
        ToolPlugin(
            name="fetch_url",
            description=(
                "Read a web page as text: the article without its navigation, or the "
                "body of a text/JSON URL. Use it on search results and on links the user "
                "gives you. It saves nothing — clip_url is for keeping a page."
            ),
            parameters={
                "url": {"type": "string", "description": "The http(s) URL."},
                "max_chars": {"type": "integer", "description": "Cap on the text (default 12000)."},
            },
            required=("url",),
            func=fetch_url,
        )
    )
