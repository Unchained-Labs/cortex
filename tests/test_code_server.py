"""The Code tab's backend: repos over the API, a real sync of a local
repository, and a scheduled review run against a stub agent."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import add_user
from cortex import code
from cortex.brain import Brain
from cortex.server.app import build_app


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    ).stdout.strip()


@pytest.fixture
def origin(tmp_path: Path, monkeypatch) -> Path:
    """A local repository, and every Repo cloning from it regardless of slug."""
    root = tmp_path / "origin"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "first")
    monkeypatch.setattr(code.Repo, "clone_url", lambda self: f"file://{root}")
    return root


@pytest.fixture
def client(brain: Brain):
    add_user(brain, "erwin", role="admin")
    add_user(brain, "sam")
    with TestClient(build_app(brain)) as c:
        yield c


def signin(client: TestClient, username: str, password: str = "hunter2hunter2"):
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return res.json()


ANSWER = """# Review of demo

Fine overall.

## Findings
- [x] **F1 · unchecked return** — high · `app.py:2` — main() ignores the result.
- [ ] **F2 · no tests** — low · `app.py:1` — nothing exercises main().

## Worth knowing
- Nothing else.
"""


class StubRuntime:
    """Answers like a model would, and keeps the brief it was given."""

    def __init__(self) -> None:
        self.briefs: list[str] = []
        self.threads: list[str] = []

    async def run(self, thread: str, text: str, on_event) -> str:
        self.threads.append(thread)
        self.briefs.append(text)
        return ANSWER


def test_repos_are_visible_to_all_and_managed_by_admins(client, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    signin(client, "sam")
    listing = client.get("/api/repos").json()
    assert listing["repos"] == [] and listing["providers"] == ["github", "gitlab"]
    assert listing["token_envs"]["gitlab"] == "GITLAB_TOKEN"
    assert client.put("/api/repos", json={"repo": {"slug": "a/b"}}).status_code == 403
    client.post("/api/auth/logout")

    signin(client, "erwin")
    bad = client.put("/api/repos", json={"repo": {"provider": "github", "slug": "nope"}})
    assert bad.status_code == 422 and "owner/name" in bad.json()["detail"]

    saved = client.put("/api/repos", json={"repo": {
        "provider": "github", "slug": "Unchained-Labs/cortex", "sync_hours": 12,
    }})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["name"] == "cortex" and body["token_present"] is False
    assert body["cloned"] is False and body["prefix"] == "code/cortex"
    assert "GITHUB_TOKEN" == body["token_env"]

    assert [r["name"] for r in client.get("/api/repos").json()["repos"]] == ["cortex"]
    assert client.get("/api/jobs").json()["repos"] == ["cortex"]
    assert client.get("/api/jobs").json()["review_modes"] == ["changes", "full"]

    assert client.delete("/api/repos/nope").status_code == 404
    assert client.delete("/api/repos/cortex").json() == {"ok": True, "jobs_removed": []}
    assert client.get("/api/repos").json()["repos"] == []


def test_sync_clones_and_makes_the_code_readable(client, brain, origin):
    signin(client, "erwin")
    client.put("/api/repos", json={"repo": {"provider": "github", "slug": "x/demo"}})
    synced = client.post("/api/repos/demo/sync").json()
    assert synced["status"] == "ok" and synced["changed"] is True, synced
    assert "cloned" in synced["detail"]

    row = client.get("/api/repos").json()["repos"][0]
    assert row["cloned"] is True and row["head"] == synced["head"]
    assert row["head_subject"] == "first" and row["last_status"] == "ok"

    # readable by a member, through the same endpoint a note uses
    client.post("/api/auth/logout")
    signin(client, "sam")
    page = client.get("/api/file", params={"path": "code/demo/app.py"})
    assert page.status_code == 200 and "def main" in page.json()["text"]
    assert page.json()["editable"] is False
    assert client.post("/api/repos/demo/sync").status_code == 403

    # and by the agent's tools
    assert "demo" in brain.registry.invoke("list_repos", {}).text
    assert "app.py" in brain.registry.invoke("repo_tree", {"repo": "demo"}).text
    assert "first" in brain.registry.invoke("repo_log", {"repo": "demo"}).text


def test_sync_reports_a_failure_instead_of_500ing(client, brain, monkeypatch):
    signin(client, "erwin")
    client.put("/api/repos", json={"repo": {"provider": "github", "slug": "x/gone"}})
    monkeypatch.setattr(code.Repo, "clone_url", lambda self: "file:///nowhere/at/all")
    out = client.post("/api/repos/gone/sync").json()
    assert out["status"] == "error" and out["detail"]
    assert client.get("/api/repos").json()["repos"][0]["last_status"] == "error"


def test_a_scheduled_review_writes_the_note_and_moves_on(client, brain, origin):
    signin(client, "erwin")
    client.put("/api/repos", json={"repo": {"provider": "github", "slug": "x/demo"}})
    stub = StubRuntime()
    client.app.state.agent["runtime"] = stub

    saved = client.put("/api/jobs", json={"job": {
        "name": "nightly review", "kind": "code_review", "interval_hours": 24,
        "settings": {"repo": "demo", "focus": "error handling", "channel": "dev"},
    }})
    assert saved.status_code == 200, saved.text
    assert saved.json()["describes"] == "review new commits in demo daily"

    # first run: nothing reviewed before, so it is a look at the whole repo
    ran = client.post("/api/jobs/nightly review/run").json()
    assert ran["status"] == "ok", ran
    assert "wrote vaults/shared/reviews/demo-" in ran["detail"]
    assert "2 finding(s)" in ran["detail"] and "posted into #dev" in ran["detail"]
    assert len(stub.briefs) == 1
    assert "first look" in stub.briefs[0] and "Focus on: error handling" in stub.briefs[0]
    assert stub.threads[0].startswith("review-nightly review-")

    reviews = client.get("/api/reviews").json()["reviews"]
    assert len(reviews) == 1
    assert reviews[0]["repo"] == "demo" and reviews[0]["job"] == "nightly review"
    assert reviews[0]["findings"] == 2 and reviews[0]["approved"] == 0
    note = (brain.config.root / reviews[0]["path"]).read_text(encoding="utf-8")
    assert "- [x]" not in note, "the model's own tick must not survive"

    channels = client.get("/api/channels").json()["channels"]
    dev = next(c for c in channels if c["name"] == "dev")
    posted = client.get(f"/api/channels/{dev['id']}/messages").json()["messages"]
    assert posted[-1]["author"] == "cortex" and "reviews/demo-" in posted[-1]["body"]

    # second run, nothing new: nothing written, nothing posted, model not called
    again = client.post("/api/jobs/nightly review/run").json()
    assert again["status"] == "ok" and "no new commits" in again["detail"]
    assert len(stub.briefs) == 1
    assert len(client.get("/api/reviews").json()["reviews"]) == 1

    # a new commit: the run reviews exactly that diff
    (origin / "lib.py").write_text("x = 1\n", encoding="utf-8")
    git(origin, "add", ".")
    git(origin, "commit", "-qm", "second")
    third = client.post("/api/jobs/nightly review/run").json()
    assert third["status"] == "ok" and "wrote" in third["detail"]
    assert "+x = 1" in stub.briefs[1] and "1 commit(s)" in stub.briefs[1]
    assert len(client.get("/api/reviews").json()["reviews"]) == 2
    assert client.get("/api/reviews", params={"repo": "other"}).json()["reviews"] == []

    # deleting the repo takes its review job with it, and says so
    gone = client.delete("/api/repos/demo").json()
    assert gone["jobs_removed"] == ["nightly review"]
    assert client.get("/api/jobs").json()["jobs"] == []
    assert not (brain.config.repos_dir / "demo").exists()


def test_a_review_of_an_unknown_repo_is_an_error_not_a_crash(client):
    signin(client, "erwin")
    client.put("/api/jobs", json={"job": {
        "name": "r", "kind": "code_review", "settings": {"repo": "missing"},
    }})
    ran = client.post("/api/jobs/r/run").json()
    assert ran["status"] == "error" and "missing" in ran["detail"]


def test_a_review_without_a_model_says_so(client, origin):
    signin(client, "erwin")
    client.put("/api/repos", json={"repo": {"provider": "github", "slug": "x/demo"}})
    client.put("/api/jobs", json={"job": {
        "name": "r", "kind": "code_review", "settings": {"repo": "demo"},
    }})
    ran = client.post("/api/jobs/r/run").json()
    assert ran["status"] == "error" and "provider" in ran["detail"].lower()
