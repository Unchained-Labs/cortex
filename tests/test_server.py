import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from cortex import keys as keymod  # noqa: E402
from cortex.brain import Brain  # noqa: E402
from cortex.server.app import build_app  # noqa: E402


@pytest.fixture
def client(brain: Brain):
    return TestClient(build_app(brain))


def test_health_is_public(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_index_page_and_assets(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "cortex" in page.text
    css = client.get("/assets/tokens.css")
    assert css.status_code == 200
    assert "ul-accent" in css.text
    assert client.get("/assets/../secrets.css").status_code in (404, 422)
    assert client.get("/assets/nope.css").status_code == 404


def test_info_without_auth_mode_none(client):
    res = client.get("/api/info")
    assert res.status_code == 200
    body = res.json()
    assert body["brain"] == "testbrain"
    assert "search_brain" in body["tools"]


def test_key_mode_locks_api_but_not_health(brain_dir):
    (brain_dir / "cortex.yaml").write_text(
        "name: locked\nproviders: {}\nserver:\n  auth: key\n", encoding="utf-8"
    )
    brain = Brain(brain_dir)
    try:
        client = TestClient(build_app(brain))
        assert client.get("/health").status_code == 200
        assert client.get("/api/info").status_code == 401

        key = keymod.generate_key()
        brain.store.add_key("test", keymod.key_prefix(key), keymod.hash_key(key))
        ok = client.get("/api/info", headers={"Authorization": f"Bearer {key}"})
        assert ok.status_code == 200
        bad = client.get("/api/info", headers={"Authorization": "Bearer ctx_wrong"})
        assert bad.status_code == 401
    finally:
        brain.close()


def test_search_endpoint_reports_fts_only(client, brain):
    from cortex.memory.chunking import Chunk

    brain.store.replace_file(
        "notes/n.md", "1:1", 0.0, [Chunk(text="rosemary thrives", heading="", start_line=1)], None
    )
    res = client.get("/api/search", params={"q": "rosemary"})
    body = res.json()
    assert body["used_vectors"] is False
    assert body["hits"][0]["path"] == "notes/n.md"
