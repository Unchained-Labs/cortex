"""Tools that walk the brain's graph (memory/graph.py).

search_brain finds the passage that matches the words; these go from a
hit to what it is connected to — the move a person makes when they click
a backlink or a "who calls this".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cortex import scope
from cortex.memory import graph
from cortex.plugins import ToolPlugin, ToolRegistry

if TYPE_CHECKING:
    from cortex.brain import Brain


def register_graph_tools(registry: ToolRegistry, brain: Brain) -> None:
    def related(path: str) -> str:
        path = (path or "").strip().lstrip("/")
        if not path or not scope.allows_path(path):
            return f"No such file: {path}"
        if brain.store.graph_node(f"file:{path}") is None:
            return (
                f"{path} is not in the graph. Either it is not indexed yet, or the key is "
                "wrong — use the exact key search results cite, e.g. vaults/shared/garden.md."
            )
        groups = graph.grouped_neighbours(brain.store, path, scope.allows_path)
        if not groups:
            return f"{path} has no links, imports or shared history with anything indexed."
        out = [f"Connections of {path}:"]
        for g in groups:
            names = []
            for item in g["items"]:
                if item["node_kind"] == "file":
                    names.append(item["path"])
                elif item["node_kind"] == "sym":
                    names.append(f"{item['label']} ({item['path']})")
                elif item["node_kind"] == "commit":
                    names.append(f"{item['id'].rsplit(':', 1)[-1]} “{item['label']}”")
                else:
                    names.append(item["label"])
            out.append(f"- {g['relation']}: " + ", ".join(names))
        return "\n".join(out)

    def find_symbol(name: str) -> str:
        name = (name or "").strip()
        if len(name) < 2:
            return "Give a function, class or type name."
        rows = [r for r in brain.store.graph_find("sym", name) if scope.allows_path(r["path"])]
        if not rows:
            return (
                f"Nothing defines {name!r} in the indexed code. grep_exact finds literal "
                "text if it is a variable or a string rather than a definition."
            )
        out = [f"{len(rows)} definition(s) matching {name!r}:"]
        for r in rows[:10]:
            out.append(f"- {r['label']} — defined in {r['path']}")
            users = [
                n for n in graph.neighbours(brain.store, r["id"], limit=30)
                if n["kind"] == "mentions"
                and n["node_kind"] == "file"
                and scope.allows_path(n["path"])
            ]
            if users:
                out.append("    used in: " + ", ".join(u["path"] for u in users[:8]))
        if len(rows) > 10:
            out.append(f"… {len(rows) - 10} more; narrow the name.")
        return "\n".join(out)

    registry.register(
        ToolPlugin(
            name="related",
            description=(
                "What a file is connected to: notes that link to it and that it links to, "
                "its tags, code that imports or uses it, files that changed in the same "
                "commits. Use it to move from a search hit to the thing around it, and "
                "before changing code, to see what depends on it."
            ),
            parameters={
                "path": {
                    "type": "string",
                    "description": (
                        "Index key, e.g. vaults/shared/garden.md or code/cortex/src/cortex/jobs.py"
                    ),
                }
            },
            required=("path",),
            func=related,
        )
    )
    registry.register(
        ToolPlugin(
            name="find_symbol",
            description=(
                "Where a function, class or type is defined in the indexed code, and which "
                "files use it. Faster and more exact than searching prose for a name."
            ),
            parameters={
                "name": {"type": "string", "description": "The symbol's name, or part of it."}
            },
            required=("name",),
            func=find_symbol,
        )
    )
