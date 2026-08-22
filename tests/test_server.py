import pytest
from fastapi.testclient import TestClient

from conftest import add_user
from cortex.brain import Brain
from cortex.memory.chunking import Chunk
from cortex.server.app import build_app


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


def test_health_public_everything_else_locked(client):
    assert client.get("/health").status_code == 200
    assert client.get("/api/info").status_code == 401
    assert client.get("/api/vaults").status_code == 401


def test_login_logout(client):
    body = signin(client, "erwin")
    assert body == {"username": "erwin", "role": "admin"}
    assert client.get("/api/me").json()["username"] == "erwin"
    client.post("/api/auth/logout")
    assert client.get("/api/me").status_code == 401
    bad = client.post("/api/auth/login", json={"username": "erwin", "password": "nope"})
    assert bad.status_code == 401


def test_vault_visibility_and_crud(client):
    signin(client, "erwin")
    names = {v["name"] for v in client.get("/api/vaults").json()["vaults"]}
    assert names == {"shared", "erwin"}

    create = client.post(
        "/api/vault/file", json={"vault": "erwin", "path": "diary.md", "text": "private"}
    )
    assert create.status_code == 200
    mtime = create.json()["mtime"]

    saved = client.put(
        "/api/vault/file",
        json={"vault": "erwin", "path": "diary.md", "text": "v2", "base_mtime": mtime},
    )
    assert saved.status_code == 200

    stale = client.put(
        "/api/vault/file",
        json={"vault": "erwin", "path": "diary.md", "text": "v3", "base_mtime": mtime - 100},
    )
    assert stale.status_code == 409

    # sam cannot see or touch erwin's vault
    client.post("/api/auth/logout")
    signin(client, "sam")
    assert client.get("/api/vault/tree", params={"vault": "erwin"}).status_code == 404
    assert (
        client.get("/api/vault/file", params={"vault": "erwin", "path": "diary.md"}).status_code
        == 404
    )
    shared = client.post(
        "/api/vault/file", json={"vault": "shared", "path": "plan.md", "text": "ours"}
    )
    assert shared.status_code == 200


def test_search_is_scoped_per_user(client, brain):
    brain.store.replace_file(
        "vaults/erwin/secret.md", "1:1", 0.0,
        [Chunk(text="the anchovy stash location", heading="", start_line=1)], None,
    )
    brain.store.replace_file(
        "vaults/shared/menu.md", "1:1", 0.0,
        [Chunk(text="anchovy pizza friday", heading="", start_line=1)], None,
    )
    signin(client, "sam")
    hits = client.get("/api/search", params={"q": "anchovy"}).json()["hits"]
    assert [h["path"] for h in hits] == ["vaults/shared/menu.md"]
    client.post("/api/auth/logout")
    signin(client, "erwin")
    hits = client.get("/api/search", params={"q": "anchovy"}).json()["hits"]
    assert {h["path"] for h in hits} == {"vaults/erwin/secret.md", "vaults/shared/menu.md"}


def test_channels_flow(client):
    signin(client, "erwin")
    channels = client.get("/api/channels").json()["channels"]
    assert channels[0]["name"] == "general"
    cid = channels[0]["id"]

    posted = client.post(f"/api/channels/{cid}/messages", json={"body": "hello team"})
    assert posted.status_code == 200
    history = client.get(f"/api/channels/{cid}/messages").json()["messages"]
    assert history[-1]["body"] == "hello team"
    assert history[-1]["author"] == "erwin"

    made = client.post("/api/channels", json={"name": "#Garden"})
    assert made.status_code == 200 and made.json()["name"] == "garden"
    bad = client.post("/api/channels", json={"name": "no spaces!"})
    assert bad.status_code == 422


def test_thread_privacy(client, brain):
    brain.store.touch_thread("t-erwin", "erwin", "private thoughts")
    signin(client, "sam")
    assert client.get("/api/history", params={"thread": "t-erwin"}).status_code == 404
    assert client.get("/api/threads").json()["threads"] == []


def test_admin_gate_and_user_management(client):
    signin(client, "sam")
    assert client.get("/api/admin/users").status_code == 403
    client.post("/api/auth/logout")

    signin(client, "erwin")
    created = client.post(
        "/api/admin/users",
        json={"username": "priya", "password": "longenough", "role": "member"},
    )
    assert created.status_code == 201
    assert client.post(
        "/api/admin/users",
        json={"username": "shared", "password": "longenough", "role": "member"},
    ).status_code == 422  # reserved name
    assert client.delete("/api/admin/users/erwin").status_code == 422  # not yourself
    assert client.delete("/api/admin/users/priya").json()["ok"] is True


def test_import_zip(client):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("vault/note.md", "# imported")
        zf.writestr("vault/.obsidian/app.json", "{}")
    signin(client, "erwin")
    res = client.post(
        "/api/vault/import",
        data={"vault": "erwin"},
        files={"file": ("v.zip", buf.getvalue(), "application/zip")},
    )
    assert res.status_code == 200
    assert res.json()["imported"] == 1
    got = client.get("/api/vault/file", params={"vault": "erwin", "path": "note.md"})
    assert got.json()["text"] == "# imported"


PLUGIN_CODE = """\
from cortex.plugins import ToolPlugin


def register(registry):
    registry.register(ToolPlugin(name="dice", description="roll", func=lambda: "4"))
"""


def test_extensions_are_admin_only(client):
    signin(client, "sam")
    assert client.get("/api/extensions").status_code == 403
    assert client.put(
        "/api/extensions/plugin", json={"name": "dice", "code": PLUGIN_CODE}
    ).status_code == 403


def test_plugin_save_lists_and_goes_live(client, brain):
    signin(client, "erwin")
    saved = client.put("/api/extensions/plugin", json={"name": "dice", "code": PLUGIN_CODE})
    assert saved.status_code == 200
    assert saved.json()["tools"] == ["dice"]

    listed = client.get("/api/extensions").json()
    assert [p["name"] for p in listed["plugins"]] == ["dice"]
    # live in the registry without a restart
    assert "dice" in client.get("/api/info").json()["tools"]

    off = client.post("/api/extensions/plugin/dice/enabled", json={"enabled": False})
    assert off.status_code == 200
    assert "dice" not in client.get("/api/info").json()["tools"]

    assert client.delete("/api/extensions/plugin/dice").status_code == 200
    assert client.get("/api/extensions").json()["plugins"] == []


def test_broken_plugin_is_rejected_with_the_loader_message(client):
    signin(client, "erwin")
    res = client.put("/api/extensions/plugin", json={"name": "bad", "code": "not python"})
    assert res.status_code == 422
    assert "failed to load" in res.json()["detail"]


def test_skill_and_mcp_through_the_api(client):
    signin(client, "erwin")
    assert client.put(
        "/api/extensions/skill",
        json={"name": "review", "description": "weekly", "instructions": "1. Look."},
    ).status_code == 200
    source = client.get("/api/extensions/source", params={"kind": "skill", "name": "review"})
    assert source.json()["instructions"].startswith("1. Look")

    assert client.put(
        "/api/extensions/mcp",
        json={"spec": {"name": "ha", "transport": "http", "url": "http://ha.local/mcp"}},
    ).status_code == 200
    listed = client.get("/api/extensions").json()["mcp_servers"]
    assert [m["name"] for m in listed] == ["ha"]

    bad = client.put(
        "/api/extensions/mcp", json={"spec": {"name": "nope", "transport": "stdio"}}
    )
    assert bad.status_code == 422


def test_connector_settings_and_run(client, brain):
    signin(client, "erwin")
    code = "def sync(out_dir, settings):\n    (out_dir / 'n.md').write_text('hi')\n"
    assert client.put(
        "/api/extensions/connector",
        json={"name": "demo", "code": code, "settings": {"enabled": True}},
    ).status_code == 200
    run = client.post("/api/extensions/connector/demo/run")
    assert run.json() == {"name": "demo", "result": "ok"}
    assert (brain.config.sources_dir / "demo" / "n.md").is_file()


def test_scaffold_endpoint(client):
    signin(client, "erwin")
    assert "register" in client.get(
        "/api/extensions/scaffold", params={"kind": "plugin"}
    ).json()["code"]


def test_capture_endpoint_writes_to_your_own_vault(client, brain):
    signin(client, "sam")
    res = client.post("/api/capture", json={"text": "call the plumber"})
    assert res.status_code == 200
    body = res.json()
    assert body["vault"] == "sam" and body["path"].startswith("journal/")
    note = (brain.config.vaults_dir / "sam" / body["path"]).read_text()
    assert "call the plumber" in note


def test_capture_refuses_someone_elses_vault(client):
    signin(client, "sam")
    res = client.post("/api/capture", json={"text": "x", "vault": "erwin"})
    assert res.status_code == 404
    assert client.post("/api/capture", json={"text": "   "}).status_code == 422


def test_digest_endpoint_is_scoped(client, brain):
    (brain.config.vaults_dir / "erwin").mkdir(parents=True, exist_ok=True)
    (brain.config.shared_vault / "s.md").write_text("- [ ] shared task\n", encoding="utf-8")
    (brain.config.vaults_dir / "erwin" / "p.md").write_text(
        "- [ ] erwin secret task\n", encoding="utf-8"
    )
    signin(client, "sam")
    tasks = client.get("/api/digest").json()["tasks"]
    assert [t["text"] for t in tasks] == ["shared task"]
    client.post("/api/auth/logout")
    signin(client, "erwin")
    tasks = client.get("/api/digest").json()["tasks"]
    assert {t["text"] for t in tasks} == {"shared task", "erwin secret task"}


def test_password_change_and_admin_reset(client, brain):
    signin(client, "sam")
    assert client.post(
        "/api/me/password", json={"current_password": "wrong", "new_password": "newpassword"}
    ).status_code == 403
    assert client.post(
        "/api/me/password",
        json={"current_password": "hunter2hunter2", "new_password": "short"},
    ).status_code == 422
    ok = client.post(
        "/api/me/password",
        json={"current_password": "hunter2hunter2", "new_password": "a-longer-password"},
    )
    assert ok.status_code == 200
    client.post("/api/auth/logout")
    signin(client, "sam", "a-longer-password")  # the new one works

    client.post("/api/auth/logout")
    signin(client, "erwin")
    reset = client.post("/api/admin/users/sam/password", json={"new_password": "reset-by-admin"})
    assert reset.status_code == 200
    client.post("/api/auth/logout")
    signin(client, "sam", "reset-by-admin")


def test_member_cannot_reset_anyone_elses_password(client):
    signin(client, "sam")
    assert client.post(
        "/api/admin/users/erwin/password", json={"new_password": "hijacked-pw"}
    ).status_code == 403


def test_deleting_a_user_keeps_their_vault_and_says_so(client, brain):
    (brain.config.vaults_dir / "sam").mkdir(parents=True, exist_ok=True)
    (brain.config.vaults_dir / "sam" / "diary.md").write_text("private", encoding="utf-8")
    signin(client, "erwin")
    body = client.delete("/api/admin/users/sam").json()
    assert body["ok"] is True
    assert body["vault_kept"].endswith("/vaults/sam")
    assert (brain.config.vaults_dir / "sam" / "diary.md").read_text() == "private"


def test_info_reports_index_and_model_health(client, brain):
    signin(client, "erwin")
    info = client.get("/api/info").json()
    assert info["indexed"] is False  # nothing indexed in the fixture brain
    assert info["indexing"] is False
    assert info["model_error"] == ""
    assert "chat_endpoint" in info
    assert client.post("/api/reindex").json()["ok"] is True


def test_reindex_is_admin_only(client):
    signin(client, "sam")
    assert client.post("/api/reindex").status_code == 403


def test_capture_returns_a_line_number(client, brain):
    signin(client, "sam")
    body = client.post("/api/capture", json={"text": "milk"}).json()
    assert isinstance(body["line"], int) and body["line"] > 0
    note = (brain.config.vaults_dir / "sam" / body["path"]).read_text().splitlines()
    assert note[body["line"] - 1] == body["text"]


def test_read_any_readable_indexed_file(client, brain):
    src = brain.config.sources_dir / "calendar_ics"
    src.mkdir(parents=True, exist_ok=True)
    (src / "e.md").write_text("# Standup\n", encoding="utf-8")
    signin(client, "sam")
    got = client.get("/api/file", params={"path": "sources/calendar_ics/e.md"})
    assert got.status_code == 200
    assert got.json()["text"] == "# Standup\n"
    assert got.json()["editable"] is False

    # someone else's vault stays invisible through this route too
    (brain.config.vaults_dir / "erwin").mkdir(parents=True, exist_ok=True)
    (brain.config.vaults_dir / "erwin" / "d.md").write_text("secret", encoding="utf-8")
    assert client.get(
        "/api/file", params={"path": "vaults/erwin/d.md"}
    ).status_code == 404
    assert client.get("/api/file", params={"path": "../../etc/passwd"}).status_code == 404


def test_mentions_are_recorded_only_for_real_other_users(client, brain):
    signin(client, "erwin")
    cid = client.get("/api/channels").json()["channels"][0]["id"]
    posted = client.post(
        f"/api/channels/{cid}/messages",
        json={"body": "@sam can you look at this? cc @nobody and @erwin and @cortex"},
    )
    assert posted.status_code == 200
    client.post("/api/auth/logout")

    signin(client, "sam")
    mentions = client.get("/api/mentions").json()["mentions"]
    assert len(mentions) == 1
    assert mentions[0]["author"] == "erwin" and mentions[0]["channel"] == "general"
    assert "look at this" in mentions[0]["body"]

    # reading the channel clears them; ambient chatter never created any
    assert client.post("/api/mentions/read", json={"channel_id": cid}).json()["cleared"] == 1
    assert client.get("/api/mentions").json()["mentions"] == []


def test_plain_channel_chatter_mentions_nobody(client):
    signin(client, "erwin")
    cid = client.get("/api/channels").json()["channels"][0]["id"]
    client.post(f"/api/channels/{cid}/messages", json={"body": "the bins go out tonight"})
    client.post("/api/auth/logout")
    signin(client, "sam")
    assert client.get("/api/mentions").json()["mentions"] == []


def test_demo_content_endpoint(client, brain):
    signin(client, "erwin")
    assert client.get("/api/info").json()["demo_installed"] is False
    body = client.post("/api/demo").json()
    assert body["count"] == 5
    assert client.get("/api/info").json()["demo_installed"] is True
    assert client.delete("/api/demo").json()["removed"] == 5


def test_demo_is_admin_only(client):
    signin(client, "sam")
    assert client.post("/api/demo").status_code == 403


def test_rules_crud_preview_and_apply(client, brain):
    signin(client, "erwin")
    (brain.config.shared_vault / "shakshuka.md").write_text("#recipe\n", encoding="utf-8")

    listing = client.get("/api/rules").json()
    assert listing["rules"] == [] and len(listing["suggested"]) >= 3
    assert "delete" not in listing["action_kinds"]

    saved = client.put("/api/rules", json={"rule": {
        "name": "file recipes",
        "matches": [{"kind": "tag", "value": "recipe"}],
        "action": {"kind": "move", "value": "recipes"},
    }})
    assert saved.status_code == 200
    assert saved.json()["describes"] == "tag matches 'recipe' → move into recipes/"

    # preview changes nothing
    preview = client.get("/api/rules/preview").json()
    assert preview["count"] == 1
    assert (brain.config.shared_vault / "shakshuka.md").is_file()

    applied = client.post("/api/rules/apply").json()
    assert applied["count"] == 1
    assert (brain.config.shared_vault / "recipes" / "shakshuka.md").is_file()
    assert client.get("/api/rules/history").json()["history"][0]["path"] == "shakshuka.md"
    assert client.delete("/api/rules/file recipes").json()["ok"] is True


def test_rules_reject_a_destination_outside_the_vault(client):
    signin(client, "erwin")
    res = client.put("/api/rules", json={"rule": {
        "name": "escape",
        "matches": [{"kind": "path", "value": "*.md"}],
        "action": {"kind": "move", "value": "../../etc"},
    }})
    assert res.status_code == 422 and "climb out" in res.json()["detail"]


def test_jobs_crud_and_run_now(client, brain):
    signin(client, "erwin")
    listing = client.get("/api/jobs").json()
    assert listing["jobs"] == [] and len(listing["suggested"]) >= 3
    assert "index" in listing["kinds"]

    saved = client.put("/api/jobs", json={"job": {
        "name": "nightly tidy", "kind": "rules", "interval_hours": 24,
        "settings": {"dry_run": True},
    }})
    assert saved.status_code == 200
    assert saved.json()["describes"] == "preview the tidying rules daily"

    ran = client.post("/api/jobs/nightly tidy/run").json()
    assert ran["status"] == "ok"
    assert "would be made" in ran["detail"]
    assert client.get("/api/jobs").json()["jobs"][0]["last_status"] == "ok"
    assert client.delete("/api/jobs/nightly tidy").json()["ok"] is True


def test_a_digest_job_writes_a_note(client, brain):
    signin(client, "erwin")
    (brain.config.shared_vault / "t.md").write_text("- [ ] a real task\n", encoding="utf-8")
    client.put("/api/jobs", json={"job": {
        "name": "briefing", "kind": "digest", "settings": {"vault": "shared"},
    }})
    ran = client.post("/api/jobs/briefing/run").json()
    assert ran["status"] == "ok" and "briefings/" in ran["detail"]
    written = list((brain.config.shared_vault / "briefings").glob("*.md"))
    assert written and "a real task" in written[0].read_text()


def test_an_empty_channel_digest_posts_nothing(client, brain):
    """A scheduled 'nothing to report' is what teaches people to ignore a channel."""
    signin(client, "erwin")
    client.put("/api/jobs", json={"job": {
        "name": "morning", "kind": "channel_digest", "settings": {"channel": "general"},
    }})
    ran = client.post("/api/jobs/morning/run").json()
    assert ran["status"] == "ok" and "nothing was posted" in ran["detail"]
    cid = client.get("/api/channels").json()["channels"][0]["id"]
    assert client.get(f"/api/channels/{cid}/messages").json()["messages"] == []


def test_rules_and_jobs_are_admin_only(client):
    signin(client, "sam")
    assert client.get("/api/rules").status_code == 403
    assert client.get("/api/jobs").status_code == 403
    assert client.post("/api/rules/apply").status_code == 403


def test_skill_library_lists_and_installs(client, brain):
    signin(client, "erwin")
    lib = client.get("/api/extensions/library").json()["skills"]
    assert len(lib) >= 5
    assert all(not s["installed"] for s in lib)
    assert all(s["description"] and s["instructions"] for s in lib)

    installed = client.post("/api/extensions/library/skill/weekly-review")
    assert installed.status_code == 200
    again = client.get("/api/extensions/library").json()["skills"]
    assert next(s for s in again if s["name"] == "weekly-review")["installed"] is True
    # and it is a real skill the agent can now reach
    names = [s["name"] for s in client.get("/api/extensions").json()["skills"]]
    assert "weekly-review" in names
    assert client.post("/api/extensions/library/skill/nope").status_code == 404


def test_connector_library_lists_and_installs(client, brain):
    signin(client, "erwin")
    lib = client.get("/api/extensions/library").json()
    names = {c["name"]: c for c in lib["connectors"]}
    assert {"calendar_ics", "rss"} <= set(names)
    assert names["calendar_ics"]["kind"] == "builtin"
    assert names["rss"]["kind"] == "template"
    assert not any(c["installed"] for c in lib["connectors"])

    # a template writes real, loadable code into the brain
    added = client.post("/api/extensions/library/connector/rss")
    assert added.status_code == 200
    assert (brain.config.connectors_dir / "rss.py").is_file()
    listed = {c["name"]: c for c in client.get("/api/extensions").json()["connectors"]}
    assert "rss" in listed and not listed["rss"]["error"]

    # a built-in just gets its settings seeded
    client.post("/api/extensions/library/connector/calendar_ics")
    again = {c["name"]: c for c in client.get("/api/extensions/library").json()["connectors"]}
    assert again["rss"]["installed"] and again["calendar_ics"]["installed"]
    assert client.post("/api/extensions/library/connector/nope").status_code == 404


def test_suggestions_carry_their_own_sentence(client):
    signin(client, "erwin")
    for rule in client.get("/api/rules").json()["suggested"]:
        assert rule["describes"]
    for job in client.get("/api/jobs").json()["suggested"]:
        assert job["describes"]
