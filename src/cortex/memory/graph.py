"""A graph over the brain: what links to what, imports what, changed with what.

## Why a graph, and why this one

Hybrid search finds the passage that matches the words. It does not know
that the note it found links to three others, that the function it found
is called from two files it never mentioned by name, or that the file it
found has changed in the same commit as another six times this month.
Those relations are exactly what a person uses to move from a hit to the
thing they actually wanted, and exactly what the agent lacked.

The current generation of retrieval work — GraphRAG, LightRAG, HippoRAG —
builds a graph of entities and relations by running a model over every
document at index time. That is the right idea and the wrong cost for a
brain that indexes on every keystroke on a laptop: it would put a model
call between saving a note and being able to search it. So this graph is
the **structural** half of that design, extracted deterministically:

* **notes**: ``[[wikilinks]]``, markdown links to other notes, ``#tags``
* **code**: imports (resolved inside the repository where possible),
  definitions, and which files *use* a symbol another file defines
* **history**: which files changed in the same commit (from ``git log``)
* **shape**: which directory holds what

No model, no network, milliseconds per file, and every edge has a reason
a person can check. An entity layer extracted by the model can sit on top
of this later without changing anything below.

## What it is used for

1. **Search expansion** — after hybrid search ranks files, the graph pulls
   in their strongest neighbours (linked notes, importers, co-changed
   files) with a lower score and a ``via`` that says why. One hop, decayed,
   the same move a person makes when they click a backlink.
2. **Tools** — ``related`` walks from a file; ``find_symbol`` goes from a
   name to where it is defined and used.
3. **The Vault view** — a Connections panel: backlinks, links out, tags,
   code that uses this, changed together with.

## Storage

Two tables in the same SQLite file as the index: ``graph_nodes`` and
``graph_edges``. Node ids carry their kind (``file:``, ``dir:``, ``tag:``,
``sym:``, ``pkg:``, ``commit:``) so a neighbour list is self-describing.
The graph is rebuilt whole whenever the index changes; extraction is a
handful of regexes per file, so on a brain of a few thousand files it is a
second, and the rebuild never runs while nothing changed.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from cortex.memory.indexer import scan_files
from cortex.rules import tags_of

if TYPE_CHECKING:
    from cortex.config import BrainConfig
    from cortex.memory.store import Store

MARKDOWN = {".md", ".mdx", ".markdown"}
PYTHON = {".py"}
JSLIKE = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
CODE = PYTHON | JSLIKE | {".rs", ".go"}
MAX_BYTES = 1_500_000
COMMITS = 200
MAX_FILES_PER_COMMIT = 30  # a 400-file commit says nothing about co-change
MIN_SYMBOL_LEN = 4
MAX_DEFINERS = 4  # a name defined in more files than this is not a lead

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
MDLINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s#]+\.md)(?:#[^)]*)?\)")
PY_IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import\b|import\s+([\w.]+))", re.M)
JS_IMPORT_RE = re.compile(
    r"""(?:^|[\n;])\s*(?:import|export)\b[^'"\n;]*?\bfrom\s+['"]([^'"]+)['"]"""
    r"""|\brequire\(\s*['"]([^'"]+)['"]\s*\)"""
    r"""|\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)"""
    r"""|^\s*import\s+['"]([^'"]+)['"]""",
    re.M,
)
DEF_RES = {
    "py": re.compile(r"^(?:async\s+def|def|class)\s+(\w+)", re.M),
    "js": re.compile(
        r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\*?|class)\s+(\w+)"
        r"|^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*(?::[^=\n]+)?=\s*(?:async\s*)?"
        r"(?:\([^)\n]*\)\s*(?::[^=\n]+)?=>|[\w$]+\s*=>|function\b)",
        re.M,
    ),
    "rs": re.compile(
        r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:fn|struct|enum|trait|type|mod)\s+(\w+)", re.M
    ),
    "go": re.compile(r"^func\s+(?:\([^)]*\)\s*)?(\w+)|^type\s+(\w+)\s", re.M),
}
IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b")
JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
JS_INDEXES = tuple(f"/index{ext}" for ext in JS_EXTS)
_STDLIB = set(getattr(sys, "stdlib_module_names", ()))


@dataclass
class Node:
    id: str
    kind: str
    label: str
    path: str = ""  # the index key, for file nodes and symbols


@dataclass
class Edge:
    src: str
    dst: str
    kind: str
    weight: float = 1.0


@dataclass
class FileFacts:
    key: str
    prefix: str
    suffix: str
    links: list[str] = field(default_factory=list)
    tags: set[str] = field(default_factory=set)
    imports: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    idents: set[str] = field(default_factory=set)


@dataclass
class GraphReport:
    nodes: int = 0
    edges: int = 0
    files: int = 0


# -- extraction ---------------------------------------------------------------


def extract(key: str, prefix: str, text: str) -> FileFacts:
    """Everything the graph wants from one file, with no knowledge of the
    others; resolution happens in ``build`` once every file is known."""
    suffix = Path(key).suffix.lower()
    facts = FileFacts(key=key, prefix=prefix, suffix=suffix)
    if suffix in MARKDOWN:
        facts.links = [m.strip() for m in WIKILINK_RE.findall(text) if m.strip()]
        facts.links += [m.strip() for m in MDLINK_RE.findall(text) if m.strip()]
        facts.tags = tags_of(text)
        return facts
    if suffix in PYTHON:
        facts.imports = [a or b for a, b in PY_IMPORT_RE.findall(text)]
        facts.symbols = _dedupe(DEF_RES["py"].findall(text))
    elif suffix in JSLIKE:
        facts.imports = [next(g for g in m if g) for m in JS_IMPORT_RE.findall(text)]
        facts.symbols = _dedupe(a or b for a, b in DEF_RES["js"].findall(text))
    elif suffix == ".rs":
        facts.symbols = _dedupe(DEF_RES["rs"].findall(text))
    elif suffix == ".go":
        facts.symbols = _dedupe(a or b for a, b in DEF_RES["go"].findall(text))
    if suffix in CODE:
        facts.idents = set(IDENT_RE.findall(text))
    return facts


def _dedupe(names) -> list[str]:
    seen: list[str] = []
    for n in names:
        if n and n not in seen:
            seen.append(n)
    return seen


# -- resolution ---------------------------------------------------------------


def _resolve_wikilink(target: str, key: str, by_stem: dict[str, list[str]], keys: set[str]) -> str:
    """``[[garden]]`` → the note called garden, preferring one in the same
    vault; ``[[trips/kyoto]]`` → that path. Obsidian's own rule, roughly."""
    prefix = key.split("/", 2)
    root = "/".join(prefix[:2]) if len(prefix) > 2 else prefix[0]
    clean = target.strip().strip("/")
    if not clean.endswith(".md"):
        clean_md = clean + ".md"
    else:
        clean_md = clean
    # a path relative to the vault root
    direct = f"{root}/{clean_md}"
    if direct in keys:
        return direct
    stem = Path(clean).name.lower()
    if stem.endswith(".md"):
        stem = stem[:-3]
    candidates = by_stem.get(stem, [])
    if not candidates:
        return ""
    same_root = [c for c in candidates if c.startswith(root + "/")]
    return (same_root or candidates)[0]


def _resolve_mdlink(target: str, key: str, keys: set[str]) -> str:
    base = Path(key).parent
    try:
        joined = (base / target).as_posix()
    except ValueError:
        return ""
    # normalise ../ without touching the filesystem
    parts: list[str] = []
    for part in joined.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    candidate = "/".join(parts)
    return candidate if candidate in keys else ""


def _resolve_py_import(module: str, key: str, prefix: str, keys: set[str]) -> str:
    """``cortex.memory.store`` → ``code/cortex/src/cortex/memory/store.py``
    when such a file exists under the same root, searching every directory
    depth so ``src/`` layouts and flat layouts both resolve."""
    if module.startswith("."):
        # relative: from .store import X, from ..config import Y
        dots = len(module) - len(module.lstrip("."))
        rest = module.lstrip(".")
        base = Path(key).parent
        for _ in range(dots - 1):
            base = base.parent
        tail = rest.replace(".", "/") if rest else ""
        for cand in (
            f"{base.as_posix()}/{tail}.py" if tail else "",
            f"{base.as_posix()}/{tail}/__init__.py" if tail else f"{base.as_posix()}/__init__.py",
        ):
            if cand and cand in keys:
                return cand
        return ""
    rel = module.replace(".", "/")
    suffixes = (f"/{rel}.py", f"/{rel}/__init__.py")
    best = ""
    for k in keys:
        if not k.startswith(prefix + "/"):
            continue
        for s in suffixes:
            if k.endswith(s):
                # the shallowest match wins: src/pkg beats tests/fixtures/pkg
                if not best or k.count("/") < best.count("/"):
                    best = k
    return best


def _resolve_js_import(spec: str, key: str, keys: set[str]) -> str:
    if not spec.startswith((".", "/")):
        return ""
    base = Path(key).parent
    target = _resolve_mdlink(spec, key, keys | {(base / spec).as_posix()})
    joined = target or _normalise((base / spec).as_posix())
    for cand in (joined, *[joined + ext for ext in JS_EXTS], *[joined + ix for ix in JS_INDEXES]):
        if cand in keys:
            return cand
    stripped = re.sub(r"\.(js|jsx|mjs|cjs)$", "", joined)
    for ext in JS_EXTS:
        if stripped + ext in keys:
            return stripped + ext
    return ""


def _normalise(path: str) -> str:
    parts: list[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _package_name(spec: str, kind: str) -> str:
    if kind == "py":
        top = spec.lstrip(".").split(".")[0]
        return "" if (not top or top in _STDLIB) else top
    if spec.startswith("@"):
        return "/".join(spec.split("/")[:2])
    return spec.split("/")[0]


# -- git history --------------------------------------------------------------


def commit_touches(root: Path, prefix: str, keys: set[str]) -> list[tuple[str, str, list[str]]]:
    """(sha, subject, [keys]) for the last COMMITS commits of a clone."""
    try:
        proc = subprocess.run(
            ["git", "log", f"-{COMMITS}", "--name-only", "--format=%x00%h%x00%s"],
            cwd=str(root), capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    out: list[tuple[str, str, list[str]]] = []
    sha = subject = ""
    touched: list[str] = []
    for line in proc.stdout.splitlines():
        if line.startswith("\x00"):
            if sha:
                out.append((sha, subject, touched))
            _, sha, subject = line.split("\x00", 2)
            touched = []
        elif line.strip():
            k = f"{prefix}/{line.strip()}"
            if k in keys:
                touched.append(k)
    if sha:
        out.append((sha, subject, touched))
    return out


# -- build ----------------------------------------------------------------------


def build(config: BrainConfig, store: Store) -> GraphReport:
    """Rebuild the whole graph from what is on disk."""
    pairs = config.root_pairs()
    files = scan_files(pairs)
    keys = set(files)
    prefix_of = {}
    for prefix, _root in pairs:
        for k in keys:
            if k.startswith(prefix + "/"):
                prefix_of[k] = prefix

    facts: dict[str, FileFacts] = {}
    for key, path in files.items():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        facts[key] = extract(key, prefix_of.get(key, key.split("/")[0]), text)

    nodes: dict[str, Node] = {}
    edges: dict[tuple[str, str, str], Edge] = {}

    def node(n: Node) -> None:
        nodes.setdefault(n.id, n)

    def edge(src: str, dst: str, kind: str, weight: float = 1.0) -> None:
        if src == dst:
            return
        e = edges.get((src, dst, kind))
        if e is None:
            edges[(src, dst, kind)] = Edge(src, dst, kind, weight)
        else:
            e.weight += weight

    # files and directories
    for key in keys:
        node(Node(f"file:{key}", "file", Path(key).name, key))
        parts = key.split("/")
        for depth in range(1, len(parts)):
            d = "/".join(parts[:depth])
            node(Node(f"dir:{d}", "dir", parts[depth - 1], d))
            if depth + 1 < len(parts):
                child = f"dir:{'/'.join(parts[: depth + 1])}"
            else:
                child = f"file:{key}"
            edge(f"dir:{d}", child, "contains")

    by_stem: dict[str, list[str]] = defaultdict(list)
    for key in keys:
        if Path(key).suffix.lower() in MARKDOWN:
            by_stem[Path(key).stem.lower()].append(key)

    # symbol table per root, for "uses"
    definers: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for f in facts.values():
        for name in f.symbols:
            if len(name) >= MIN_SYMBOL_LEN:
                definers[f.prefix][name].append(f.key)

    for f in facts.values():
        src = f"file:{f.key}"
        for target in f.links:
            resolved = _resolve_wikilink(target, f.key, by_stem, keys)
            if not resolved and target.endswith(".md"):
                resolved = _resolve_mdlink(target, f.key, keys)
            if resolved and resolved != f.key:
                edge(src, f"file:{resolved}", "links")
        for tag in f.tags:
            node(Node(f"tag:{tag}", "tag", f"#{tag}"))
            edge(src, f"tag:{tag}", "tagged")
        lang = "py" if f.suffix in PYTHON else "js"
        for spec in f.imports:
            if lang == "py":
                resolved = _resolve_py_import(spec, f.key, f.prefix, keys)
            else:
                resolved = _resolve_js_import(spec, f.key, keys)
            if resolved:
                edge(src, f"file:{resolved}", "imports")
            else:
                pkg = _package_name(spec, lang)
                if pkg:
                    node(Node(f"pkg:{pkg}", "pkg", pkg))
                    edge(src, f"pkg:{pkg}", "depends")
        for name in f.symbols:
            sid = f"sym:{f.key}#{name}"
            node(Node(sid, "sym", name, f.key))
            edge(src, sid, "defines")
        if f.idents:
            table = definers[f.prefix]
            for name in f.idents:
                owners = table.get(name)
                if not owners or len(owners) > MAX_DEFINERS:
                    continue
                for owner in owners:
                    if owner == f.key:
                        continue
                    edge(src, f"file:{owner}", "uses", 1.0 / len(owners))
                    edge(src, f"sym:{owner}#{name}", "mentions")

    # history: commits touch files, and files change together
    for prefix, root in config.code_roots:
        if not (root / ".git").is_dir():
            continue
        for sha, subject, touched in commit_touches(root, prefix, keys):
            cid = f"commit:{prefix}:{sha}"
            node(Node(cid, "commit", subject[:120], prefix))
            for k in touched:
                edge(cid, f"file:{k}", "touched")
            if 1 < len(touched) <= MAX_FILES_PER_COMMIT:
                for i, a in enumerate(touched):
                    for b in touched[i + 1 :]:
                        edge(f"file:{a}", f"file:{b}", "cochange")
                        edge(f"file:{b}", f"file:{a}", "cochange")

    store.replace_graph(
        [(n.id, n.kind, n.label, n.path) for n in nodes.values()],
        [(e.src, e.dst, e.kind, e.weight) for e in edges.values()],
    )
    return GraphReport(nodes=len(nodes), edges=len(edges), files=len(keys))


# -- reading ------------------------------------------------------------------

#: How each edge kind reads from either end, for tools and the Vault panel.
RELATION_WORDS = {
    ("links", "out"): "links to",
    ("links", "in"): "linked from",
    ("imports", "out"): "imports",
    ("imports", "in"): "imported by",
    ("uses", "out"): "uses code from",
    ("uses", "in"): "used by",
    ("cochange", "out"): "changed together with",
    ("tagged", "out"): "tagged",
    ("tagged", "in"): "tagged with this",
    ("defines", "out"): "defines",
    ("mentions", "in"): "mentioned in",
    ("depends", "out"): "depends on",
    ("depends", "in"): "depended on by",
    ("touched", "in"): "changed in",
    ("contains", "in"): "in directory",
    ("contains", "out"): "contains",
}
#: The neighbours that make a file relevant to a search about another.
EXPAND_KINDS = ("links", "imports", "uses", "cochange")
#: Weight of a one-hop neighbour relative to the hit it hangs off.
EXPAND_WEIGHT = 0.35
EXPAND_TOP = 5
EXPAND_PER_HIT = 4


def neighbours(store: Store, node_id: str, limit: int = 30) -> list[dict]:
    """Every edge touching a node, with the words for its direction."""
    out: list[dict] = []
    for row in store.graph_edges_of(node_id, limit=limit * 4):
        direction = "out" if row["src"] == node_id else "in"
        if row["kind"] == "cochange" and direction == "in":
            continue  # stored both ways; show once
        words = RELATION_WORDS.get((row["kind"], direction))
        if not words:
            continue
        other = row["dst"] if direction == "out" else row["src"]
        n = store.graph_node(other)
        if n is None:
            continue
        out.append({
            "relation": words,
            "kind": row["kind"],
            "direction": direction,
            "id": other,
            "node_kind": n["kind"],
            "label": n["label"],
            "path": n["path"],
            "weight": row["weight"],
        })
    out.sort(key=lambda r: (-r["weight"], r["relation"], r["label"]))
    return out[:limit]


def grouped_neighbours(store: Store, path: str, allowed) -> list[dict]:
    """Neighbours of a file grouped by relation, for the Vault panel and the
    ``related`` tool. ``allowed(path)`` is the caller's scope."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for n in neighbours(store, f"file:{path}", limit=80):
        if n["node_kind"] in ("file", "sym") and n["path"] and not allowed(n["path"]):
            continue
        if n["node_kind"] == "dir" or n["kind"] == "defines":
            continue
        groups[n["relation"]].append(n)
    order = [w for (_k, _d), w in RELATION_WORDS.items()]
    return [
        {"relation": rel, "items": items[:12]}
        for rel in order
        if (items := groups.get(rel))
    ]


def expand_hits(
    store: Store, ranked: list[tuple[str, float]], allowed
) -> list[tuple[str, float, str]]:
    """One hop out from the best hits: (path, score, via) for neighbours not
    already in the result. The caller merges them in below the direct hits."""
    seen = {p for p, _ in ranked}
    extra: dict[str, tuple[float, str]] = {}
    for path, score in ranked[:EXPAND_TOP]:
        picked = 0
        for n in neighbours(store, f"file:{path}", limit=40):
            if n["kind"] not in EXPAND_KINDS or n["node_kind"] != "file":
                continue
            other = n["path"]
            if other in seen or not allowed(other):
                continue
            boosted = score * EXPAND_WEIGHT * min(1.0, 0.5 + n["weight"] / 4)
            via = f"{n['relation']} {Path(path).name}"
            if other not in extra or extra[other][0] < boosted:
                extra[other] = (boosted, via)
            picked += 1
            if picked >= EXPAND_PER_HIT:
                break
    return [(p, s, v) for p, (s, v) in extra.items()]
