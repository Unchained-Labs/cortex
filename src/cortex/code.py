"""Repositories the brain can read, and the reviews it writes about them.

## What a repo is here

A GitHub or GitLab repository, cloned into ``.cortex/repos/<name>`` and
indexed like any other root under the key prefix ``code/<name>/``. Once it
is there the agent reaches it with the tools it already has — search_brain,
grep_exact, read_file — and cites ``code/cortex/src/cortex/jobs.py:41`` the
way it cites a note. Nothing about retrieval is code-specific; the point of
adding a repo is that the brain's context and the codebase are searchable
together.

The clone is a cache. Deleting ``.cortex/`` loses nothing: the next sync
clones again.

## Tokens

A repo names the environment variable holding its token (``GITHUB_TOKEN``
and ``GITLAB_TOKEN`` by default), and the variable comes from the shell or
from ``.env`` beside cortex.yaml. The token is never written to disk by this
module: it travels to git as an ``http.<host>.extraheader`` set through
``GIT_CONFIG_*`` environment variables, so it is neither in ``argv`` (where
``ps`` reads it) nor in ``.git/config`` (where a backup would carry it).
Public repositories need no token at all.

## Reviews

A scheduled review syncs the repo, takes the diff since the commit it last
looked at, and asks the agent to review it *with the brain open* — the
conventions, decisions and earlier reviews in the vault are a tool call
away. The answer lands in ``reviews/<repo>-<date>.md`` in the shared vault,
in the format the approval loop already reads: one ``- [ ] **F1 · title**``
line per finding, unticked. A person ticks what an agent may act on;
``approved_findings`` and ``record_work`` take it from there.

The file is written by this module from the model's answer, not by the
model calling write_note. A model given a formatting task it believes it
completed is indistinguishable from one that did — the worklog history in
builtin.py is three lessons on that — so the one step that has to happen is
done by code with nothing to get wrong.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cortex.config import BrainConfig

PROVIDERS = ("github", "gitlab")
DEFAULT_TOKEN_ENV = {"github": "GITHUB_TOKEN", "gitlab": "GITLAB_TOKEN"}
DEFAULT_HOST = {"github": "github.com", "gitlab": "gitlab.com"}
CLONE_DEPTH = 200
CLONE_TIMEOUT = 600
FETCH_TIMEOUT = 180
MAX_DIFF_CHARS = 48_000
MAX_TREE_LINES = 300
REVIEWS_DIR = "reviews"
REVIEW_MODES = ("changes", "full")
DEFAULT_FOCUS = (
    "correctness bugs, security issues, error handling, and anything that would "
    "surprise the next person to read it"
)

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_SLUG_RE = re.compile(r"^[\w.-]+(?:/[\w.-]+)+$")
_BRANCH_RE = re.compile(r"^[\w][\w./-]{0,199}$")
_ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "__pycache__", ".venv", "venv",
    "target", "vendor", ".cortex",
}
# The same shape approved_findings reads: a checkbox, a bold id and title.
FINDING_RE = re.compile(
    r"^\s*[-*]\s*\[(?P<tick>[ xX])\]\s*\*\*(?P<id>[A-Za-z]+\d+)\s*[·.\-]\s*(?P<title>[^*]+?)\*\*"
)
_TICKED = re.compile(r"^(\s*[-*]\s*)\[[xX]\]", re.MULTILINE)


class RepoError(ValueError):
    pass


@dataclass
class Repo:
    name: str
    provider: str
    slug: str
    host: str
    branch: str = ""
    token_env: str = ""
    sync_hours: float = 6.0
    enabled: bool = True
    last_sync: str = ""
    last_status: str = ""
    last_detail: str = ""
    head: str = ""
    head_subject: str = ""

    @property
    def prefix(self) -> str:
        return f"code/{self.name}"

    def clone_url(self) -> str:
        return f"https://{self.host}/{self.slug}.git"

    def web_url(self) -> str:
        return f"https://{self.host}/{self.slug}"

    def clone_dir(self, config: BrainConfig) -> Path:
        return config.repos_dir / self.name

    def spec(self) -> dict[str, Any]:
        """What the store keeps. Never a token, never a sync result."""
        return {
            "name": self.name,
            "provider": self.provider,
            "slug": self.slug,
            "host": self.host,
            "branch": self.branch,
            "token_env": self.token_env,
            "sync_hours": self.sync_hours,
            "enabled": self.enabled,
        }

    def as_dict(self, config: BrainConfig | None = None) -> dict[str, Any]:
        out = {
            **self.spec(),
            "url": self.web_url(),
            # whether the variable is set — never its value
            "token_present": token_present(self),
            "last_sync": self.last_sync,
            "last_status": self.last_status,
            "last_detail": self.last_detail,
            "head": self.head,
            "head_subject": self.head_subject,
            "cloned": bool(config and (self.clone_dir(config) / ".git").is_dir()),
            "prefix": self.prefix,
        }
        return out


def token_present(repo: Repo) -> bool:
    return bool(repo.token_env and os.environ.get(repo.token_env, "").strip())


def parse_repo(raw: dict) -> Repo:
    """Validate a dashboard or CLI body. Accepts ``owner/repo``, a web URL,
    or an ssh-style ``git@host:owner/repo.git`` for the repo field — all of
    them become https, because a token is the only credential this knows."""
    provider = str(raw.get("provider") or "").strip().lower()
    source = str(raw.get("slug") or raw.get("url") or raw.get("repo") or "").strip()
    host = str(raw.get("host") or "").strip().lower().rstrip("/")
    if not source:
        raise RepoError("which repository? give owner/repo or its URL")

    slug = source
    if "://" in source:
        parsed = urlparse(source)
        if parsed.scheme not in ("http", "https"):
            raise RepoError("only http(s) URLs can be cloned with a token")
        host = host or (parsed.hostname or "").lower()
        slug = parsed.path.strip("/")
    elif source.startswith("git@") and ":" in source:
        at_host, _, path = source[4:].partition(":")
        host = host or at_host.lower()
        slug = path.strip("/")
    if slug.endswith(".git"):
        slug = slug[:-4]
    slug = slug.strip("/")
    if not _SLUG_RE.match(slug) or ".." in slug:
        raise RepoError("a repository is owner/name (GitLab groups may nest: group/sub/name)")

    if not provider:
        provider = "github" if (host or "github.com").endswith("github.com") else "gitlab"
    if provider not in PROVIDERS:
        raise RepoError(f"provider is one of {', '.join(PROVIDERS)}")
    host = host or DEFAULT_HOST[provider]
    if "/" in host or " " in host or not host:
        raise RepoError("host is a bare hostname, e.g. gitlab.example.com")

    name = str(raw.get("name") or "").strip().lower() or slug.rsplit("/", 1)[-1].lower()
    name = re.sub(r"[^a-z0-9_-]+", "-", name).strip("-")[:48]
    if not _NAME_RE.match(name):
        raise RepoError("a repo needs a short name (lowercase letters, digits, - or _)")

    branch = str(raw.get("branch") or "").strip()
    if branch and (not _BRANCH_RE.match(branch) or ".." in branch):
        raise RepoError("that does not look like a branch name")

    token_env = str(raw.get("token_env") or "").strip() or DEFAULT_TOKEN_ENV[provider]
    if not _ENV_RE.match(token_env):
        raise RepoError(
            "token_env is the NAME of an environment variable, e.g. GITHUB_TOKEN — "
            "upper case, and never the token itself"
        )

    try:
        sync_hours = float(raw.get("sync_hours", 6))
    except (TypeError, ValueError) as exc:
        raise RepoError("sync_hours must be a number (0 for manual only)") from exc
    if sync_hours < 0:
        raise RepoError("sync_hours cannot be negative")

    return Repo(
        name=name,
        provider=provider,
        slug=slug,
        host=host,
        branch=branch,
        token_env=token_env,
        sync_hours=sync_hours,
        enabled=bool(raw.get("enabled", True)),
    )


def repo_from_row(row) -> Repo:
    import json

    repo = parse_repo(json.loads(row["spec"]))
    repo.enabled = bool(row["enabled"])
    repo.last_sync = row["last_sync"]
    repo.last_status = row["last_status"]
    repo.last_detail = row["last_detail"]
    repo.head = row["head"]
    repo.head_subject = row["head_subject"]
    return repo


# -- git ---------------------------------------------------------------------


def git_env(repo: Repo) -> dict[str, str]:
    """Environment for a git command against this repo's host.

    The token rides in an ``http.<host>.extraheader`` configured through
    ``GIT_CONFIG_*``: scoped to the host so a redirect elsewhere never
    carries it, and out of ``argv`` and ``.git/config`` both.
    """
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    token = os.environ.get(repo.token_env, "").strip() if repo.token_env else ""
    if not token:
        return env
    user = "x-access-token" if repo.provider == "github" else "oauth2"
    basic = base64.b64encode(f"{user}:{token}".encode()).decode()
    # Appended after whatever GIT_CONFIG_* the shell already carries (a proxy
    # or a CI runner sets some), never over the top of it.
    try:
        n = int(os.environ.get("GIT_CONFIG_COUNT", "0") or 0)
    except ValueError:
        n = 0
    env["GIT_CONFIG_COUNT"] = str(n + 1)
    env[f"GIT_CONFIG_KEY_{n}"] = f"http.https://{repo.host}/.extraheader"
    env[f"GIT_CONFIG_VALUE_{n}"] = f"AUTHORIZATION: basic {basic}"
    return env


def _git(
    args: list[str], cwd: Path | None, env: dict[str, str] | None = None, timeout: int = 60
) -> str:
    if shutil.which("git") is None:
        raise RepoError("git is not installed on this machine")
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            env=env or {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"},
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RepoError(f"git {args[0]} took longer than {timeout}s") from exc
    except OSError as exc:
        raise RepoError(f"could not run git: {exc}") from exc
    if proc.returncode != 0:
        raise RepoError(_explain(proc.stderr.strip() or f"git {args[0]} failed"))
    return proc.stdout


def _explain(stderr: str) -> str:
    """git's last line is usually the one that matters, said in our words
    where the fix is known."""
    lowered = stderr.lower()
    if "could not read username" in lowered or "authentication failed" in lowered:
        return (
            "authentication failed — the repository is private or the token is wrong. "
            "Put a token in the variable this repo names (see .env)"
        )
    if "repository not found" in lowered or "not found" in lowered and "fatal" in lowered:
        return "repository not found — check owner/name, or whether the token can see it"
    if "could not resolve host" in lowered:
        return "could not resolve the host — is the machine online, and the host right?"
    if "remote branch" in lowered and "not found" in lowered:
        return "that branch does not exist on the remote"
    lines = [ln for ln in stderr.splitlines() if ln.strip()]
    return (lines[-1] if lines else stderr)[:300]


@dataclass
class SyncResult:
    head: str
    subject: str
    previous: str = ""
    fresh: bool = False

    @property
    def changed(self) -> bool:
        return self.fresh or self.previous != self.head


def sync(config: BrainConfig, repo: Repo) -> SyncResult:
    """Clone on first sight, fast-forward afterwards. Local edits in the
    clone are discarded — nothing here is meant to write into it."""
    target = repo.clone_dir(config)
    env = git_env(repo)
    fresh = not (target / ".git").is_dir()
    previous = "" if fresh else head_of(target)[0]
    if fresh:
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        args = ["clone", "--quiet", "--depth", str(CLONE_DEPTH), "--single-branch"]
        if repo.branch:
            args += ["--branch", repo.branch]
        args += [repo.clone_url(), str(target)]
        _git(args, cwd=None, env=env, timeout=CLONE_TIMEOUT)
    else:
        branch = repo.branch or _default_branch(target)
        _git(["fetch", "--quiet", "--force", "origin", branch], target, env, FETCH_TIMEOUT)
        _git(["reset", "--quiet", "--hard", "FETCH_HEAD"], target)
    sha, subject = head_of(target)
    return SyncResult(head=sha, subject=subject, previous=previous, fresh=fresh)


def _default_branch(target: Path) -> str:
    try:
        ref = _git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], target).strip()
        if ref.startswith("origin/"):
            return ref[len("origin/"):]
    except RepoError:
        pass
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], target).strip()


def head_of(target: Path) -> tuple[str, str]:
    out = _git(["log", "-1", "--format=%H%x00%s"], target).strip()
    sha, _, subject = out.partition("\x00")
    return sha, subject


def has_commit(target: Path, sha: str) -> bool:
    if not sha:
        return False
    try:
        _git(["cat-file", "-e", f"{sha}^{{commit}}"], target)
        return True
    except RepoError:
        return False


def log(target: Path, n: int = 20, since: str = "") -> str:
    """``abc1234 2026-09-08 who  subject`` lines, newest first."""
    args = ["log", f"-{max(1, min(n, 200))}", "--format=%h %ad %an  %s", "--date=short"]
    if since:
        args.append(f"{since}..HEAD")
    return _git(args, target).strip()


def commit_count(target: Path, base: str, head: str = "HEAD") -> int:
    out = _git(["rev-list", "--count", f"{base}..{head}"], target).strip()
    return int(out or 0)


def diff(
    target: Path, base: str, head: str = "HEAD", max_chars: int = MAX_DIFF_CHARS
) -> tuple[str, str, bool]:
    """(stat, patch, truncated). The patch is cut at ``max_chars`` because
    it is read by a model; the stat is what tells it what it did not see."""
    span = f"{base}..{head}" if base else head
    stat = _git(["diff", "--stat=100", "--no-color", span], target).strip()
    patch = _git(["diff", "--no-color", "-M", "--minimal", span], target)
    truncated = len(patch) > max_chars
    if truncated:
        patch = patch[:max_chars].rsplit("\ndiff --git ", 1)[0]
    return stat, patch.strip(), truncated


def tree(target: Path, subdir: str = "", depth: int = 2, limit: int = MAX_TREE_LINES) -> str:
    """An indented listing, directories first, skipping the usual noise."""
    root = (target / subdir).resolve() if subdir else target.resolve()
    # is_relative_to, not a string prefix: "…/repos/cortex-other" starts with
    # "…/repos/cortex" and would have passed as inside it.
    if not root.is_relative_to(target.resolve()) or not root.is_dir():
        return f"No such directory: {subdir or '/'}"
    lines: list[str] = []

    def walk(directory: Path, level: int) -> None:
        if level > depth or len(lines) >= limit:
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return
        for entry in entries:
            if entry.name in _SKIP_DIRS or entry.name.startswith("."):
                continue
            if len(lines) >= limit:
                lines.append("… more not shown")
                return
            pad = "  " * level
            if entry.is_dir():
                lines.append(f"{pad}{entry.name}/")
                walk(entry, level + 1)
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                lines.append(f"{pad}{entry.name}  ({size:,} B)")

    walk(root, 0)
    return "\n".join(lines) or "(empty)"


def remove_clone(config: BrainConfig, name: str) -> None:
    target = config.repos_dir / name
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)


# -- reviews ------------------------------------------------------------------


@dataclass
class ReviewInput:
    repo: Repo
    base: str
    head: str
    mode: str
    focus: str
    log: str = ""
    stat: str = ""
    patch: str = ""
    truncated: bool = False
    tree: str = ""
    commits: int = 0
    prior: list[str] = field(default_factory=list)


def review_brief(inp: ReviewInput) -> str:
    """What the reviewing agent is told. Long on purpose: the brief carries
    the format, the discipline, and the evidence, so nothing about the
    review depends on the model remembering an earlier turn."""
    repo = inp.repo
    keys = f"code/{repo.name}/<path>"
    head = [
        f"You are reviewing the repository **{repo.name}** ({repo.web_url()}, "
        f"branch {repo.branch or 'default'}) at commit {inp.head[:10]}.",
        "",
        "The whole repository is indexed in this brain. Read any file with "
        f"read_file using keys of the form {keys}; find literals with grep_exact; "
        "search_brain finds code and notes together.",
        "",
        "Before judging, get context from the brain: call search_brain and recall for the "
        "project's conventions, architecture decisions and known problems; call "
        f"search_brain('review {repo.name}') and read earlier notes under "
        "vaults/shared/reviews/ so you do not repeat a finding that is already open, and "
        "read reviews/_worklog.md so you do not re-raise one that was fixed. Prefer what the "
        "notes say over your assumptions about how the project should work.",
        "",
        f"Focus on: {inp.focus}.",
    ]
    if inp.prior:
        head += ["", "Earlier reviews of this repository: " + ", ".join(inp.prior[:8]) + "."]

    body: list[str] = []
    if inp.mode == "changes" and inp.base:
        body += [
            "",
            f"## What changed — {inp.commits} commit(s), {inp.base[:10]}..{inp.head[:10]}",
            "",
            "```",
            inp.log or "(no commits)",
            "```",
            "",
            "### Files",
            "```",
            inp.stat or "(none)",
            "```",
            "",
            "### Diff",
            "```diff",
            inp.patch or "(empty)",
            "```",
        ]
        if inp.truncated:
            body += [
                "",
                "The diff was cut for length. The file list above is complete: read_file "
                "the files whose changes you did not see before you comment on them.",
            ]
    else:
        body += [
            "",
            "## The repository",
            "",
            "This is a first look at the whole codebase rather than a diff. Start from the "
            "layout below and the recent history, read the entry points and the code the "
            "focus names, and do not try to read everything.",
            "",
            "### Layout",
            "```",
            inp.tree or "(empty)",
            "```",
            "",
            "### Recent commits",
            "```",
            inp.log or "(none)",
            "```",
        ]

    tail = [
        "",
        "## What to write",
        "",
        "Reply with the review itself in markdown, nothing before or after it:",
        "",
        "1. A short summary: what this change (or codebase) does and your overall read, "
        "in a few sentences.",
        "2. A `## Findings` section. One line per finding, in EXACTLY this shape, most "
        "important first, ids F1, F2, … unique within this review:",
        "",
        "   - [ ] **F1 · short title** — high|medium|low · `path:line` — what is wrong, why "
        "it matters, and the fix you would make.",
        "",
        "   Leave every checkbox unticked. A tick means a human approved that finding for "
        "an agent to act on, and only a person may set one.",
        "   Cite a real file and line for every finding; read the file first if you are "
        "not sure. No finding without evidence, and no finding that is a matter of taste "
        "unless the brain's notes say the project cares about it.",
        "3. A `## Worth knowing` section: things that are fine but the next reader should "
        "know, or questions for the author. Plain bullets, no checkboxes.",
        "",
        "If there is genuinely nothing to raise, say so under `## Findings` in one line and "
        "do not invent a finding to fill the section.",
    ]
    return "\n".join(head + body + tail)


def untick(body: str) -> tuple[str, int]:
    out, count = _TICKED.subn(r"\1[ ]", body)
    return out, count


def count_findings(text: str) -> tuple[int, int]:
    """(findings, approved) in a review note."""
    total = approved = 0
    for line in text.splitlines():
        m = FINDING_RE.match(line)
        if m:
            total += 1
            if m.group("tick").lower() == "x":
                approved += 1
    return total, approved


_SEVERITY_RE = re.compile(r"\*\*\s*[—-]\s*(?P<sev>high|medium|low)\b", re.IGNORECASE)


def count_severities(text: str) -> dict[str, int]:
    """How many findings say high, medium or low — the pills the Code tab
    shows so a review's weight reads without opening it."""
    out = {"high": 0, "medium": 0, "low": 0}
    for line in text.splitlines():
        if not FINDING_RE.match(line):
            continue
        m = _SEVERITY_RE.search(line)
        if m:
            out[m.group("sev").lower()] += 1
    return out


def write_review(
    config: BrainConfig,
    repo: Repo,
    job: str,
    base: str,
    head: str,
    answer: str,
    when: datetime | None = None,
    vault: str = "shared",
) -> str:
    """Write the note and return its path inside the vault. A second review
    the same day gets a suffix rather than overwriting the first — a review
    is a record, and records are not replaced."""
    from cortex.vaults import vault_path

    text = answer.strip()
    if not text:
        raise RepoError("the model returned an empty review")
    text, _ = untick(text)
    stamp = when or datetime.now()
    day = stamp.date().isoformat()
    directory = vault_path(config, vault, REVIEWS_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    rel = f"{REVIEWS_DIR}/{repo.name}-{day}.md"
    n = 2
    while (directory / Path(rel).name).exists():
        rel = f"{REVIEWS_DIR}/{repo.name}-{day}-{n}.md"
        n += 1
    findings, _ = count_findings(text)
    if not text.lstrip().startswith("#"):
        text = f"# Review of {repo.name} — {day}\n\n{text}"
    body = (
        "---\n"
        f"repo: {repo.name}\n"
        f"job: {job}\n"
        f"reviewed: {base[:12] + '..' if base else ''}{head[:12]}\n"
        f"date: {stamp.isoformat(timespec='seconds')}\n"
        f"findings: {findings}\n"
        f"source: {repo.web_url()}\n"
        "---\n\n"
        f"{text}\n"
    )
    (directory / Path(rel).name).write_text(body, encoding="utf-8")
    return rel


def parse_review(text: str) -> dict[str, Any]:
    """Frontmatter plus live finding counts, for the Code tab's list."""
    meta: dict[str, Any] = {}
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for line in lines[1:]:
            if line.strip() == "---":
                break
            key, _, value = line.partition(":")
            if key.strip():
                meta[key.strip().lower()] = value.strip()
    title = next((ln.lstrip("# ").strip() for ln in lines if ln.startswith("# ")), "")
    findings, approved = count_findings(text)
    severities = count_severities(text)
    return {
        "repo": meta.get("repo", ""),
        "job": meta.get("job", ""),
        "reviewed": meta.get("reviewed", ""),
        "date": meta.get("date", ""),
        "title": title,
        "findings": findings,
        "approved": approved,
        **severities,
    }


def list_reviews(config: BrainConfig, vault: str = "shared", repo: str = "") -> list[dict]:
    """Every review note, newest first, as index keys the Vault view opens."""
    directory = config.vaults_dir / vault / REVIEWS_DIR
    if not directory.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(directory.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.name.startswith("_"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta = parse_review(text)
        if repo and meta["repo"] != repo:
            continue
        out.append({
            "path": f"vaults/{vault}/{REVIEWS_DIR}/{path.name}",
            "name": path.name,
            "mtime": path.stat().st_mtime,
            **meta,
        })
    return out


def parse_review_settings(settings: dict) -> dict:
    """The settings of a ``code_review`` job, checked and defaulted."""
    repo = str(settings.get("repo") or "").strip().lower()
    if not repo:
        raise RepoError("a review needs which repository to review")
    mode = str(settings.get("mode") or "changes").strip().lower()
    if mode not in REVIEW_MODES:
        raise RepoError(f"mode is one of {', '.join(REVIEW_MODES)}")
    focus = str(settings.get("focus") or "").strip() or DEFAULT_FOCUS
    channel = str(settings.get("channel") or "").strip().lstrip("#").lower()
    if channel and not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", channel):
        raise RepoError("bad channel name")
    return {"repo": repo, "mode": mode, "focus": focus[:2000], "channel": channel}
