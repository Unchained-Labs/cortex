"""The structural graph: extraction, resolution, the build over notes and a
git repository, search expansion, the two tools, and the scoped endpoint."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import add_user
from cortex import scope
from cortex.brain import Brain
from cortex.memory import graph
from cortex.memory.chunking import Chunk
from cortex.memory.indexer import run_index
from cortex.memory.search import format_result, hybrid_search
from cortex.memory.store import Store
from cortex.server.app import build_app
from test_code import git, local_repo

# -- extraction ---------------------------------------------------------------


def test_extract_markdown_links_and_tags():
    facts = graph.extract(
        "vaults/shared/garden.md",
        "vaults/shared",
        "# Garden\n\nSee [[Compost|the heap]] and [[soil#ph]] and [notes](./water.md#top).\n"
        "#outdoors #garden/veg\n",
    )
    assert facts.links == ["Compost", "soil", "./water.md"]
    assert {"outdoors", "garden/veg"} <= facts.tags
    assert not facts.imports and not facts.symbols


def test_extract_python_and_js():
    py = graph.extract(
        "code/demo/src/app.py",
        "code/demo",
        "import os\nfrom .lib import helper\nfrom demo.core import Thing\n\n"
        "class Service:\n    pass\n\ndef run_service():\n    return helper()\n",
    )
    assert py.imports == ["os", ".lib", "demo.core"]
    assert py.symbols == ["Service", "run_service"]
    assert {"helper", "Service", "run_service"} <= py.idents

    js = graph.extract(
        "code/web/src/App.tsx",
        "code/web",
        'import React from "react";\nimport { api } from "./api";\n'
        "export function App() {}\nexport const useThing = () => 1;\n",
    )
    assert js.imports == ["react", "./api"]
    assert "App" in js.symbols and "useThing" in js.symbols


# -- the build ----------------------------------------------------------------


@pytest.fixture
def linked_brain(brain: Brain, tmp_path: Path) -> Brain:
    """Two linked notes, a tag, and a real repository whose two files were
    committed together and import each other."""
    shared = brain.config.shared_vault
    (shared / "garden.md").write_text(
        "# Garden\n\nThe beds are in [[compost]]. #outdoors\n", encoding="utf-8"
    )
    (shared / "compost.md").write_text("# Compost\n\nTurn it weekly. #outdoors\n", encoding="utf-8")
    (shared / "lunch.md").write_text("# Lunch\n\nTacos on thursday.\n", encoding="utf-8")

    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "lib.py").write_text("def helper_fn():\n    return 1\n", encoding="utf-8")
    (root / "app.py").write_text(
        "import json\nfrom lib import helper_fn\n\ndef main():\n    return helper_fn()\n",
        encoding="utf-8",
    )
    git(root, "add", ".")
    git(root, "commit", "-qm", "wire the helper")
    brain.config.code_roots = [("code/demo", root)]
    return brain


def edge_kinds(store: Store, node_id: str) -> set[tuple[str, str, str]]:
    return {(r["src"], r["dst"], r["kind"]) for r in store.graph_edges_of(node_id)}


def test_build_makes_every_kind_of_edge(linked_brain: Brain):
    report = graph.build(linked_brain.config, linked_brain.store)
    store = linked_brain.store
    assert report.files == 5 and report.nodes > 5 and report.edges > 5

    garden = edge_kinds(store, "file:vaults/shared/garden.md")
    assert ("file:vaults/shared/garden.md", "file:vaults/shared/compost.md", "links") in garden
    assert ("file:vaults/shared/garden.md", "tag:outdoors", "tagged") in garden
    assert ("dir:vaults/shared", "file:vaults/shared/garden.md", "contains") in garden

    app = edge_kinds(store, "file:code/demo/app.py")
    assert ("file:code/demo/app.py", "file:code/demo/lib.py", "imports") in app
    assert ("file:code/demo/app.py", "file:code/demo/lib.py", "uses") in app
    assert ("file:code/demo/app.py", "sym:code/demo/lib.py#helper_fn", "mentions") in app
    assert ("file:code/demo/app.py", "file:code/demo/lib.py", "cochange") in app
    assert ("file:code/demo/app.py", "sym:code/demo/app.py#main", "defines") in app
    # json is stdlib: no package node for it
    assert not any(dst == "pkg:json" for _, dst, _ in app)
    commits = [s for s, _, k in app if k == "touched"]
    assert len(commits) == 1 and store.graph_node(commits[0])["label"] == "wire the helper"


def test_related_words_read_from_either_end(linked_brain: Brain):
    graph.build(linked_brain.config, linked_brain.store)
    store = linked_brain.store
    groups = graph.grouped_neighbours(store, "vaults/shared/compost.md", lambda _p: True)
    by_rel = {g["relation"]: [i["path"] or i["label"] for i in g["items"]] for g in groups}
    assert by_rel["linked from"] == ["vaults/shared/garden.md"]
    assert by_rel["tagged"] == ["#outdoors"]

    groups = graph.grouped_neighbours(store, "code/demo/lib.py", lambda _p: True)
    by_rel = {g["relation"]: [i["path"] or i["label"] for i in g["items"]] for g in groups}
    assert by_rel["imported by"] == ["code/demo/app.py"]
    assert by_rel["used by"] == ["code/demo/app.py"]
    assert by_rel["changed together with"] == ["code/demo/app.py"]

    # scope hides what the caller may not read, even from a file they may
    groups = graph.grouped_neighbours(
        store, "code/demo/lib.py", lambda p: not p.startswith("code/demo/app")
    )
    assert not any(g["relation"] == "imported by" for g in groups)


async def test_indexer_rebuilds_the_graph_only_when_something_changed(linked_brain: Brain):
    first = await run_index(linked_brain.config, linked_brain.store, None)
    assert first.indexed == 5 and first.graph_nodes > 0 and first.graph_edges > 0
    second = await run_index(linked_brain.config, linked_brain.store, None)
    assert second.indexed == 0
    assert (second.graph_nodes, second.graph_edges) == (first.graph_nodes, first.graph_edges)
    assert linked_brain.store.stats()["graph_nodes"] == first.graph_nodes


# -- search expansion -----------------------------------------------------------


def seeded(tmp_path: Path) -> Store:
    store = Store(tmp_path / "index.db")
    now = time.time()
    store.replace_file(
        "vaults/shared/garden.md", "1:1", now,
        [Chunk(text="the beds want compost before the frost", heading="Garden", start_line=1)],
        None,
    )
    store.replace_file(
        "vaults/shared/compost.md", "1:1", now,
        [Chunk(text="turn the heap weekly", heading="Compost", start_line=1)],
        None,
    )
    store.replace_file(
        "vaults/erwin/private.md", "1:1", now,
        [Chunk(text="my own heap notes", heading="Private", start_line=1)],
        None,
    )
    store.replace_graph(
        [
            ("file:vaults/shared/garden.md", "file", "garden.md", "vaults/shared/garden.md"),
            ("file:vaults/shared/compost.md", "file", "compost.md", "vaults/shared/compost.md"),
            ("file:vaults/erwin/private.md", "file", "private.md", "vaults/erwin/private.md"),
        ],
        [
            ("file:vaults/shared/garden.md", "file:vaults/shared/compost.md", "links", 1.0),
            ("file:vaults/erwin/private.md", "file:vaults/shared/garden.md", "links", 1.0),
        ],
    )
    return store


def test_search_pulls_in_a_linked_note_below_the_hits(tmp_path):
    store = seeded(tmp_path)
    result = hybrid_search(store, "frost", None)
    paths = [h.path for h in result.hits]
    assert paths[0] == "vaults/shared/garden.md"
    assert "vaults/shared/compost.md" in paths and "vaults/erwin/private.md" in paths
    direct, linked = result.hits[0], next(h for h in result.hits if h.path.endswith("compost.md"))
    assert linked.via == "links to garden.md" and linked.score <= direct.score
    assert linked.passages and linked.passages[0].text == "turn the heap weekly"
    assert "via: links to garden.md" in format_result(result, "frost")


def test_search_expansion_respects_scope(tmp_path):
    store = seeded(tmp_path)
    result = hybrid_search(store, "frost", None, prefixes=("vaults/shared/",))
    paths = [h.path for h in result.hits]
    assert "vaults/shared/compost.md" in paths
    assert "vaults/erwin/private.md" not in paths
    assert all(not h.via for h in hybrid_search(store, "frost", None, prefixes=()).hits)


# -- tools ----------------------------------------------------------------------


def tool(brain: Brain, name: str):
    return next(p for p in brain.registry.plugins() if p.name == name).func


def test_related_and_find_symbol_tools(linked_brain: Brain):
    graph.build(linked_brain.config, linked_brain.store)
    with scope.scoped(None):
        out = tool(linked_brain, "related")("code/demo/lib.py")
        assert "imported by: code/demo/app.py" in out
        assert "changed together with: code/demo/app.py" in out
        assert "wire the helper" in out

        assert "not in the graph" in tool(linked_brain, "related")("code/demo/nope.py")

        found = tool(linked_brain, "find_symbol")("helper")
        assert "helper_fn — defined in code/demo/lib.py" in found
        assert "used in: code/demo/app.py" in found
        assert "Nothing defines" in tool(linked_brain, "find_symbol")("zzz")

    with scope.scoped(("vaults/",)):
        assert "No such file" in tool(linked_brain, "related")("code/demo/lib.py")
        assert "Nothing defines" in tool(linked_brain, "find_symbol")("helper")


# -- the endpoint ---------------------------------------------------------------


def test_neighbors_endpoint_is_scoped(linked_brain: Brain):
    (linked_brain.config.vaults_dir / "erwin").mkdir()
    (linked_brain.config.vaults_dir / "erwin" / "mine.md").write_text(
        "See [[garden]] for the beds.\n", encoding="utf-8"
    )
    (linked_brain.config.vaults_dir / "ana").mkdir()
    (linked_brain.config.vaults_dir / "ana" / "hers.md").write_text(
        "Also [[garden]].\n", encoding="utf-8"
    )
    graph.build(linked_brain.config, linked_brain.store)
    add_user(linked_brain, "erwin", role="admin")
    add_user(linked_brain, "ana")
    with TestClient(build_app(linked_brain)) as client:
        login = {"username": "erwin", "password": "hunter2hunter2"}
        assert client.post("/api/auth/login", json=login).status_code == 200
        url = "/api/graph/neighbors"
        body = client.get(url, params={"path": "vaults/shared/garden.md"}).json()
        assert body["indexed"]
        rel = {g["relation"]: [i["path"] for i in g["items"]] for g in body["groups"]}
        assert rel["links to"] == ["vaults/shared/compost.md"]
        assert rel["linked from"] == ["vaults/erwin/mine.md"]  # ana's vault stays hers
        assert client.get(url, params={"path": "vaults/ana/hers.md"}).status_code == 404
        code = client.get(url, params={"path": "code/demo/lib.py"}).json()
        assert any(g["relation"] == "imported by" for g in code["groups"])
        unknown = client.get(url, params={"path": "vaults/shared/nope.md"}).json()
        assert unknown == {"path": "vaults/shared/nope.md", "indexed": False, "groups": []}


def test_search_endpoint_carries_via(linked_brain: Brain):
    add_user(linked_brain, "erwin", role="admin")
    with TestClient(build_app(linked_brain)) as client:
        client.post("/api/auth/login", json={"username": "erwin", "password": "hunter2hunter2"})
        client.post("/api/reindex")
        deadline = time.time() + 10
        while time.time() < deadline:
            info = client.get("/api/info").json()
            if info["stats"]["files"] >= 5 and not info["indexing"]:
                break
            time.sleep(0.05)
        hits = client.get("/api/search", params={"q": "beds"}).json()["hits"]
        assert hits[0]["path"] == "vaults/shared/garden.md" and hits[0]["via"] == ""
        assert any(h["via"] == "links to garden.md" for h in hits)


def test_server_builds_a_missing_graph_at_startup(linked_brain: Brain):
    """A brain indexed by a version without the graph starts with files and
    no graph; the dashboard fills it in without anyone writing a note."""
    asyncio.run(run_index(linked_brain.config, linked_brain.store, None))
    linked_brain.store.replace_graph([], [])
    assert linked_brain.store.graph_stats()["graph_nodes"] == 0
    with TestClient(build_app(linked_brain)):  # closing the app closes the brain
        deadline = time.time() + 10
        while time.time() < deadline and not linked_brain.store.graph_stats()["graph_nodes"]:
            time.sleep(0.1)
        assert linked_brain.store.graph_stats()["graph_edges"] > 0


def test_repo_clone_is_in_the_graph_after_sync(brain: Brain, tmp_path: Path):
    from cortex import code

    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    (origin / "a.py").write_text("from b import thing_one\n", encoding="utf-8")
    (origin / "b.py").write_text("def thing_one():\n    pass\n", encoding="utf-8")
    git(origin, "add", ".")
    git(origin, "commit", "-qm", "both")
    repo = local_repo(origin)
    code.sync(brain.config, repo)
    brain.store.upsert_repo(repo.name, json.dumps(repo.spec()), True)
    brain.refresh_code_roots()
    graph.build(brain.config, brain.store)
    assert ("file:code/demo/a.py", "file:code/demo/b.py", "imports") in edge_kinds(
        brain.store, "file:code/demo/a.py"
    )
