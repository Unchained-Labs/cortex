"""Repositories and reviews: parsing, git plumbing against a local repo, the
review note, and the loader for .env.

The git tests use a repository created in tmp_path and cloned by path — no
network, no token — so every code path except the auth header runs for real.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cortex import code, env, jobs
from cortex.brain import Brain
from cortex.config import BrainConfig


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    ).stdout.strip()


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """A real repository with one commit, to clone from by path."""
    root = tmp_path / "origin"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    (root / "README.md").write_text("# demo\n\nA demo repo.\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "first")
    return root


def local_repo(origin: Path, name: str = "demo") -> code.Repo:
    repo = code.Repo(name=name, provider="github", slug="x/demo", host="github.com",
                     branch="main")
    repo.clone_url = lambda: f"file://{origin}"  # type: ignore[method-assign]
    return repo


# -- parsing ------------------------------------------------------------------


def test_parse_accepts_slug_url_and_ssh_forms():
    a = code.parse_repo({"provider": "github", "slug": "Unchained-Labs/cortex"})
    assert (a.name, a.host, a.slug) == ("cortex", "github.com", "Unchained-Labs/cortex")
    assert a.clone_url() == "https://github.com/Unchained-Labs/cortex.git"
    assert a.token_env == "GITHUB_TOKEN"

    b = code.parse_repo({"repo": "https://gitlab.example.com/group/sub/thing.git",
                         "provider": "gitlab"})
    assert (b.name, b.host, b.slug) == ("thing", "gitlab.example.com", "group/sub/thing")
    assert b.token_env == "GITLAB_TOKEN"

    c = code.parse_repo({"repo": "git@github.com:foo/bar.git"})
    assert (c.provider, c.name, c.clone_url()) == (
        "github", "bar", "https://github.com/foo/bar.git"
    )
    # a URL on an unknown host is a GitLab, which is the self-hosted one
    d = code.parse_repo({"url": "https://git.example.org/team/app"})
    assert d.provider == "gitlab"


def test_parse_refuses_bad_shapes():
    with pytest.raises(code.RepoError, match="which repository"):
        code.parse_repo({"provider": "github"})
    with pytest.raises(code.RepoError, match="owner/name"):
        code.parse_repo({"provider": "github", "slug": "just-a-name"})
    with pytest.raises(code.RepoError, match="owner/name"):
        code.parse_repo({"provider": "github", "slug": "../etc/passwd"})
    with pytest.raises(code.RepoError, match="provider"):
        code.parse_repo({"provider": "bitbucket", "slug": "a/b"})
    with pytest.raises(code.RepoError, match="branch"):
        code.parse_repo({"provider": "github", "slug": "a/b", "branch": "-rf"})
    with pytest.raises(code.RepoError, match="NAME of an environment variable"):
        code.parse_repo({"provider": "github", "slug": "a/b", "token_env": "ghp_secret"})
    with pytest.raises(code.RepoError, match="http"):
        code.parse_repo({"provider": "github", "slug": "ssh://git@github.com/a/b"})


def test_spec_never_carries_a_token_or_sync_state(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    repo = code.parse_repo({"provider": "github", "slug": "a/b"})
    assert "ghp_secret" not in str(repo.spec())
    assert "ghp_secret" not in str(repo.as_dict())
    assert repo.as_dict()["token_present"] is True
    monkeypatch.delenv("GITHUB_TOKEN")
    assert repo.as_dict()["token_present"] is False


def test_git_env_puts_the_token_in_a_scoped_header_not_argv(monkeypatch):
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-abc")
    repo = code.parse_repo({"provider": "gitlab", "slug": "g/p"})
    # a shell that already carries git config (a proxy, a CI runner) keeps it
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.abbrev")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "12")
    env_ = code.git_env(repo)
    assert env_["GIT_CONFIG_COUNT"] == "2" and env_["GIT_CONFIG_KEY_0"] == "core.abbrev"
    assert env_["GIT_CONFIG_KEY_1"] == "http.https://gitlab.com/.extraheader"
    assert env_["GIT_CONFIG_VALUE_1"].startswith("AUTHORIZATION: basic ")
    assert "glpat-abc" not in env_["GIT_CONFIG_VALUE_1"]  # base64, not plaintext
    assert env_["GIT_TERMINAL_PROMPT"] == "0"
    monkeypatch.delenv("GITLAB_TOKEN")
    assert code.git_env(repo)["GIT_CONFIG_COUNT"] == "1"  # public repo, no header added


# -- git --------------------------------------------------------------------


def test_sync_clones_then_fast_forwards_and_indexes(brain: Brain, origin: Path):
    repo = local_repo(origin)
    first = code.sync(brain.config, repo)
    assert first.fresh and first.changed and first.subject == "first"
    assert (repo.clone_dir(brain.config) / "app.py").is_file()

    again = code.sync(brain.config, repo)
    assert not again.fresh and not again.changed and again.head == first.head

    (origin / "lib.py").write_text("x = 1\n", encoding="utf-8")
    git(origin, "add", ".")
    git(origin, "commit", "-qm", "second")
    third = code.sync(brain.config, repo)
    assert third.changed and third.previous == first.head and third.subject == "second"

    target = repo.clone_dir(brain.config)
    assert code.commit_count(target, first.head) == 1
    assert "second" in code.log(target, since=first.head)
    stat, patch, truncated = code.diff(target, first.head)
    assert "lib.py" in stat and "+x = 1" in patch and not truncated
    assert code.has_commit(target, first.head) and not code.has_commit(target, "0" * 40)
    assert "app.py" in code.tree(target)

    # once the brain knows about it, the clone is an indexed root
    brain.store.upsert_repo(repo.name, __import__("json").dumps(repo.spec()), True)
    brain.refresh_code_roots()
    assert ("code/demo", target) in brain.config.root_pairs()
    assert brain.config.resolve_key("code/demo/app.py") == target / "app.py"
    assert brain.config.resolve_key("code/demo/../secret") is None


def test_tree_stays_inside_the_clone(brain: Brain, origin: Path):
    repo = local_repo(origin)
    code.sync(brain.config, repo)
    target = repo.clone_dir(brain.config)
    # a sibling whose name merely starts with the clone's name is outside it
    sibling = target.parent / (target.name + "-other")
    sibling.mkdir()
    (sibling / "secret.txt").write_text("x", encoding="utf-8")
    assert "No such directory" in code.tree(target, "../demo-other")
    assert "No such directory" in code.tree(target, "..")
    assert "app.py" in code.tree(target, "")


def test_sync_explains_a_missing_repo(brain: Brain, tmp_path: Path):
    repo = code.Repo(name="gone", provider="github", slug="x/gone", host="github.com")
    repo.clone_url = lambda: f"file://{tmp_path / 'nowhere'}"  # type: ignore[method-assign]
    with pytest.raises(code.RepoError):
        code.sync(brain.config, repo)


def test_diff_is_cut_but_the_stat_is_not(brain: Brain, origin: Path):
    repo = local_repo(origin)
    base = code.sync(brain.config, repo).head
    (origin / "big.py").write_text("".join(f"line_{i} = {i}\n" for i in range(3000)))
    git(origin, "add", ".")
    git(origin, "commit", "-qm", "big")
    code.sync(brain.config, repo)
    stat, patch, truncated = code.diff(repo.clone_dir(brain.config), base, max_chars=2000)
    assert truncated and len(patch) <= 2000 and "big.py" in stat


# -- reviews ----------------------------------------------------------------


ANSWER = """# Review of demo

Looks mostly fine.

## Findings
- [x] **F1 · unchecked return** — high · `app.py:2` — main() ignores the result.
- [ ] **F2 · no tests** — low · `README.md:1` — nothing exercises main().

## Worth knowing
- The README is a stub.
"""


def test_write_review_unticks_and_records_frontmatter(brain: Brain):
    repo = code.parse_repo({"provider": "github", "slug": "x/demo"})
    rel = code.write_review(brain.config, repo, "nightly", "a" * 40, "b" * 40, ANSWER)
    assert rel.startswith("reviews/demo-")
    text = (brain.config.shared_vault / rel).read_text(encoding="utf-8")
    assert "- [x]" not in text, "the model may not approve its own finding"
    assert "repo: demo" in text and "job: nightly" in text
    assert "reviewed: aaaaaaaaaaaa..bbbbbbbbbbbb" in text
    assert "findings: 2" in text

    # a second review the same day is a second file, not an overwrite
    rel2 = code.write_review(brain.config, repo, "nightly", "", "c" * 40, ANSWER)
    assert rel2 != rel and rel2.endswith("-2.md")

    listed = code.list_reviews(brain.config)
    assert [r["name"] for r in listed][:2] == [Path(rel2).name, Path(rel).name] or {
        r["name"] for r in listed
    } == {Path(rel).name, Path(rel2).name}
    one = next(r for r in listed if r["name"] == Path(rel).name)
    assert one["findings"] == 2 and one["approved"] == 0 and one["repo"] == "demo"
    assert one["path"] == f"vaults/shared/{rel}"
    assert code.list_reviews(brain.config, repo="other") == []

    # the approval loop reads the same shape once a person ticks a box
    (brain.config.shared_vault / rel).write_text(
        text.replace("- [ ] **F1", "- [x] **F1"), encoding="utf-8"
    )
    assert "F1" in brain.registry.invoke("approved_findings", {}).text


def test_write_review_refuses_an_empty_answer(brain: Brain):
    repo = code.parse_repo({"provider": "github", "slug": "x/demo"})
    with pytest.raises(code.RepoError, match="empty"):
        code.write_review(brain.config, repo, "j", "", "b" * 40, "   ")


def test_review_brief_carries_the_evidence_and_the_format():
    repo = code.parse_repo({"provider": "github", "slug": "x/demo"})
    inp = code.ReviewInput(repo=repo, base="a" * 40, head="b" * 40, mode="changes",
                           focus="security", log="abc first", stat="app.py | 1 +",
                           patch="+x = 1", truncated=True, commits=1, prior=["demo-1.md"])
    brief = code.review_brief(inp)
    for needle in ("code/demo/<path>", "Focus on: security", "+x = 1", "app.py | 1 +",
                   "cut for length", "- [ ] **F1", "Leave every checkbox unticked",
                   "reviews/_worklog.md", "demo-1.md"):
        assert needle in brief, needle
    full = code.review_brief(code.ReviewInput(repo=repo, base="", head="b" * 40,
                                              mode="full", focus="x", tree="src/\n  a.py"))
    assert "first look" in full and "src/" in full


def test_code_review_job_settings_are_checked():
    job = jobs.parse_job({"name": "r", "kind": "code_review", "settings": {"repo": "Demo"}})
    assert job.settings == {"repo": "demo", "mode": "changes",
                            "focus": code.DEFAULT_FOCUS, "channel": ""}
    assert job.describe() == "review new commits in demo daily"
    full = jobs.parse_job({"name": "r", "kind": "code_review", "interval_hours": 168,
                           "settings": {"repo": "demo", "mode": "full", "channel": "#Dev"}})
    assert full.describe() == "review the whole of demo weekly"
    assert full.settings["channel"] == "dev"
    with pytest.raises(jobs.JobError, match="which repository"):
        jobs.parse_job({"name": "r", "kind": "code_review"})
    with pytest.raises(jobs.JobError, match="mode"):
        jobs.parse_job({"name": "r", "kind": "code_review",
                        "settings": {"repo": "demo", "mode": "vibes"}})


# -- .env -------------------------------------------------------------------


def test_env_file_is_parsed_and_never_overrides_the_shell(tmp_path: Path, monkeypatch):
    text = (
        "# tokens\n"
        "GITHUB_TOKEN=ghp_one\n"
        "export GITLAB_TOKEN='glpat two'\n"
        'QUOTED="a # not a comment"\n'
        "UNQUOTED=value # a comment\n"
        "ALREADY=from-file\n"
        "not a line\n"
    )
    assert env.parse_env(text) == {
        "GITHUB_TOKEN": "ghp_one", "GITLAB_TOKEN": "glpat two",
        "QUOTED": "a # not a comment", "UNQUOTED": "value", "ALREADY": "from-file",
    }
    for key in ("GITHUB_TOKEN", "GITLAB_TOKEN", "QUOTED", "UNQUOTED"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ALREADY", "from-shell")
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    added = env.load_env_file(path)
    assert "ALREADY" not in added and os.environ["ALREADY"] == "from-shell"
    assert os.environ["GITHUB_TOKEN"] == "ghp_one"
    assert env.load_env_file(tmp_path / "missing") == []


def test_brain_loads_its_env_file(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CORTEX_TEST_FROM_ENV", raising=False)
    root = tmp_path / "b"
    root.mkdir()
    (root / "cortex.yaml").write_text("name: x\n", encoding="utf-8")
    (root / ".env").write_text("CORTEX_TEST_FROM_ENV=yes\n", encoding="utf-8")
    from cortex.config import load_config

    cfg: BrainConfig = load_config(root)
    assert cfg.env_path == root / ".env"
    assert os.environ["CORTEX_TEST_FROM_ENV"] == "yes"
    monkeypatch.delenv("CORTEX_TEST_FROM_ENV")
