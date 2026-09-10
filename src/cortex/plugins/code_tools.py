"""Tools for the repositories in the Code tab.

Reading a file, grepping and searching need nothing new — a clone is a
root like any other, keyed ``code/<repo>/…``, so search_brain, grep_exact
and read_file already reach it. What a reviewer needs on top is what git
knows and the filesystem does not: the shape of the tree, the recent
history, and the diff between two points. Three thin tools, all read-only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cortex import code, scope
from cortex.plugins import ToolPlugin, ToolRegistry

if TYPE_CHECKING:
    from cortex.brain import Brain


def register_code_tools(registry: ToolRegistry, brain: Brain) -> None:
    def _find(name: str):
        name = (name or "").strip().lower()
        if not name:
            return None, "Which repository? Call list_repos to see them."
        if not scope.allows_path(f"code/{name}/"):
            return None, f"No repository named {name!r} is readable here."
        repo = next((r for r in brain.repos() if r.name == name and r.enabled), None)
        if repo is None:
            return None, f"No repository named {name!r} — list_repos shows what there is."
        target = repo.clone_dir(brain.config)
        if not (target / ".git").is_dir():
            return None, f"{name} has not been synced yet; sync it from the Code tab."
        return repo, ""

    def list_repos() -> str:
        rows = [r for r in brain.repos() if r.enabled and scope.allows_path(f"code/{r.name}/")]
        if not rows:
            return (
                "No repositories are shared with this brain. An admin adds one in the "
                "Code tab of the dashboard."
            )
        out = ["Repositories the brain can read (files are keyed code/<name>/<path>):"]
        for r in rows:
            head = f"{r.head[:10]} {r.head_subject}" if r.head else "not synced yet"
            out.append(
                f"- {r.name}: {r.web_url()} (branch {r.branch or 'default'}) — at {head}"
                f"{' · last sync ' + r.last_sync[:16] if r.last_sync else ''}"
            )
        return "\n".join(out)

    def repo_tree(repo: str, path: str = "", depth: int = 2) -> str:
        found, problem = _find(repo)
        if found is None:
            return problem
        listing = code.tree(found.clone_dir(brain.config), path.strip("/"), max(1, min(depth, 5)))
        where = f"code/{found.name}/{path.strip('/')}".rstrip("/")
        return f"{where}/\n{listing}"

    def repo_log(repo: str, n: int = 20, since: str = "") -> str:
        found, problem = _find(repo)
        if found is None:
            return problem
        try:
            text = code.log(found.clone_dir(brain.config), n, since.strip())
        except code.RepoError as exc:
            return f"git log failed: {exc}"
        return text or "No commits."

    def repo_diff(repo: str, base: str, head: str = "HEAD", max_chars: int = 20000) -> str:
        found, problem = _find(repo)
        if found is None:
            return problem
        base = base.strip()
        if not base or base.startswith("-"):
            return "Give the base commit or ref to diff from (a sha from repo_log)."
        try:
            stat, patch, truncated = code.diff(
                found.clone_dir(brain.config), base, head.strip() or "HEAD",
                max_chars=max(1000, min(max_chars, 80000)),
            )
        except code.RepoError as exc:
            return f"git diff failed: {exc}"
        if not stat:
            return f"No difference between {base} and {head or 'HEAD'}."
        tail = "\n\n… the patch was cut for length; read_file the rest." if truncated else ""
        return f"{stat}\n\n{patch}{tail}"

    registry.register(
        ToolPlugin(
            name="list_repos",
            description=(
                "The code repositories shared with this brain, with their branch and the "
                "commit they are at. Files inside them are read with read_file using "
                "keys like code/<repo>/src/main.py, and search_brain and grep_exact "
                "cover them too."
            ),
            func=list_repos,
        )
    )
    registry.register(
        ToolPlugin(
            name="repo_tree",
            description=(
                "The directory layout of a repository (or one directory in it), a few "
                "levels deep. Use it to orient before reading files."
            ),
            parameters={
                "repo": {"type": "string", "description": "Repository name from list_repos."},
                "path": {"type": "string", "description": "Subdirectory; blank for the root."},
                "depth": {"type": "integer", "description": "Levels to show (default 2)."},
            },
            required=("repo",),
            func=repo_tree,
        )
    )
    registry.register(
        ToolPlugin(
            name="repo_log",
            description=(
                "Recent commits of a repository, newest first: short sha, date, author, "
                "subject. `since` limits it to commits after that sha."
            ),
            parameters={
                "repo": {"type": "string", "description": "Repository name."},
                "n": {"type": "integer", "description": "How many (default 20)."},
                "since": {"type": "string", "description": "Only commits after this sha."},
            },
            required=("repo",),
            func=repo_log,
        )
    )
    registry.register(
        ToolPlugin(
            name="repo_diff",
            description=(
                "What changed between two commits of a repository: the file list, then "
                "the patch. `base` is a sha from repo_log; `head` defaults to the latest."
            ),
            parameters={
                "repo": {"type": "string", "description": "Repository name."},
                "base": {"type": "string", "description": "Older commit sha or ref."},
                "head": {"type": "string", "description": "Newer commit; default HEAD."},
                "max_chars": {"type": "integer", "description": "Patch cap (default 20000)."},
            },
            required=("repo", "base"),
            func=repo_diff,
        )
    )
