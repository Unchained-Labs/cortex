"""Save a web page into the brain as markdown.

"Keep this for later" is a daily action, and a link in a note is worth
much less than the page's text: a link cannot be searched, and it rots.
So a clip stores the readable text, with the URL in frontmatter.

The extractor is deliberately small — stdlib ``html.parser``, no
readability dependency. It keeps headings, paragraphs and list items,
drops script/style/nav/header/footer/aside, and says plainly when a page
yields nothing useful rather than saving an empty note. Pages that render
their text with JavaScript will produce little; that is a real limit, not
a bug to hide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from cortex.config import BrainConfig
from cortex.vaults import VaultError, vault_path

CLIPS_DIR = "clips"
MAX_BYTES = 4_000_000
TIMEOUT = 20.0
DROP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript"}
BLOCK_TAGS = {"p", "div", "section", "article", "br", "tr"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.blocks: list[str] = []
        self._buf: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._pending_prefix = ""

    def _flush(self) -> None:
        text = " ".join("".join(self._buf).split())
        self._buf.clear()
        if text:
            self.blocks.append(f"{self._pending_prefix}{text}")
        self._pending_prefix = ""

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in DROP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = True
        elif tag in HEADING_TAGS:
            self._flush()
            self._pending_prefix = "#" * int(tag[1]) + " "
        elif tag == "li":
            self._flush()
            self._pending_prefix = "- "
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in DROP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = False
            self.title = " ".join("".join(self._buf).split())
            self._buf.clear()
        elif tag in HEADING_TAGS or tag == "li" or tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._buf.append(data)

    def close(self) -> None:  # noqa: D102
        super().close()
        self._flush()


@dataclass
class Clip:
    url: str
    title: str
    text: str


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:70] or "clip"


def extract(html: str, url: str) -> Clip:
    parser = _Extractor()
    parser.feed(html)
    parser.close()
    # Drop the navigational scraps a real article never consists of.
    blocks = [
        b for b in parser.blocks
        if b.startswith("#") or b.startswith("- ") or len(b) > 40
    ]
    title = parser.title or urlparse(url).path.rsplit("/", 1)[-1] or url
    return Clip(url=url, title=unescape(title).strip(), text="\n\n".join(blocks).strip())


def fetch(url: str) -> Clip:
    if urlparse(url).scheme not in ("http", "https"):
        raise VaultError("only http and https URLs can be clipped")
    try:
        res = httpx.get(
            url,
            timeout=TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "cortex-clip/1.0 (+https://github.com/Unchained-Labs/cortex)"},
        )
    except httpx.HTTPError as exc:
        raise VaultError(f"could not fetch {url}: {exc}") from exc
    if res.status_code != 200:
        raise VaultError(f"{url} returned HTTP {res.status_code}")
    if "html" not in res.headers.get("content-type", "").lower():
        raise VaultError(f"{url} is not an HTML page")
    return extract(res.text[:MAX_BYTES], url)


def save(config: BrainConfig, vault: str, clip: Clip, when: datetime | None = None) -> str:
    if not clip.text:
        raise VaultError(
            "no readable text found on that page (it may render its content with "
            "JavaScript, which the clipper cannot run)"
        )
    stamp = when or datetime.now()
    rel = f"{CLIPS_DIR}/{stamp.date().isoformat()}-{slugify(clip.title)}.md"
    target = vault_path(config, vault, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "---\n"
        f"source: {clip.url}\n"
        f"clipped: {stamp.isoformat(timespec='seconds')}\n"
        "---\n\n"
        f"# {clip.title}\n\n"
        f"{clip.text}\n"
    )
    target.write_text(body, encoding="utf-8")
    return rel
