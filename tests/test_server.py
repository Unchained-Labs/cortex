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
