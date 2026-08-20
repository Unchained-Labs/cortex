"""Split files into chunks that retrieve well.

Markdown splits on headings with the full heading path kept ("Onboarding ›
Day one"), code splits on column-zero definitions, everything else gets a
sliding window. Short sections are kept — "Owner: platform team" is exactly
the kind of line people look up.

Bump CHUNK_SCHEMA whenever the output of this module changes shape; the
indexer treats a mismatch as "re-index everything".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CHUNK_SCHEMA = 1
CHUNK_CHARS = 1600
OVERLAP = 200

MARKDOWN_SUFFIXES = {".md", ".mdx", ".markdown"}
CODE_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".c", ".h", ".cpp",
    ".hpp", ".java", ".kt", ".rb", ".sh", ".bash", ".zsh", ".lua", ".swift",
}

_DEF_RE = re.compile(
    r"^(?:async\s+def|def|class|function|fn|impl|struct|enum|func|pub\s+fn|"
    r"export\s+(?:async\s+)?function|export\s+class|type\s+\w+\s+struct)\b"
)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")
_FENCE_RE = re.compile(r"^(```|~~~)")


@dataclass(frozen=True)
class Chunk:
    text: str
    heading: str
    start_line: int

    def embedding_text(self) -> str:
        """The heading rides along in the embedded text; it carries the
        context a bare paragraph loses."""
        return f"{self.heading}\n\n{self.text}" if self.heading else self.text


def chunk_file(path_suffix: str, text: str) -> list[Chunk]:
    if path_suffix in MARKDOWN_SUFFIXES:
        return chunk_markdown(text)
    if path_suffix in CODE_SUFFIXES:
        return chunk_code(text)
    return _window(text, heading="", first_line=1)


def chunk_markdown(text: str) -> list[Chunk]:
    lines = text.splitlines()
    chunks: list[Chunk] = []
    stack: list[tuple[int, str]] = []  # (level, title)
    buf: list[str] = []
    buf_start = 1
    in_fence = False

    def heading_path() -> str:
        return " › ".join(title for _, title in stack)

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            chunks.extend(_split_long(body, heading_path(), buf_start))

    for lineno, line in enumerate(lines, start=1):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        match = None if in_fence else _HEADING_RE.match(line)
        if match:
            flush()
            level = len(match.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, match.group(2).strip()))
            buf = []
            buf_start = lineno + 1
        else:
            buf.append(line)
    flush()
    return chunks


def chunk_code(text: str) -> list[Chunk]:
    lines = text.splitlines()
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_start = 1
    heading = ""

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            chunks.extend(_split_long(body, heading, buf_start))

    for lineno, line in enumerate(lines, start=1):
        if _DEF_RE.match(line) and buf:
            flush()
            buf = []
            buf_start = lineno
            heading = line.strip()[:120]
        buf.append(line)
    flush()
    return chunks


def _split_long(body: str, heading: str, first_line: int) -> list[Chunk]:
    if len(body) <= CHUNK_CHARS:
        return [Chunk(text=body, heading=heading, start_line=first_line)]
    return _window(body, heading=heading, first_line=first_line)


def _window(text: str, heading: str, first_line: int) -> list[Chunk]:
    body = text.strip()
    if not body:
        return []
    chunks: list[Chunk] = []
    pos = 0
    while pos < len(body):
        end = min(pos + CHUNK_CHARS, len(body))
        if end < len(body):
            # prefer to break at a newline in the second half of the window
            cut = body.rfind("\n", pos + CHUNK_CHARS // 2, end)
            if cut != -1:
                end = cut
        piece = body[pos:end].strip()
        if piece:
            start_line = first_line + body[:pos].count("\n")
            chunks.append(Chunk(text=piece, heading=heading, start_line=start_line))
        if end >= len(body):
            break
        pos = max(end - OVERLAP, pos + 1)
    return chunks
