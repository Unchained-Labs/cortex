"""A web page may not redirect the agent into this machine."""

from __future__ import annotations

import pytest

from cortex import urlguard


class FakeResponse:
    def __init__(self, status=200, location=None, text="ok"):
        self.status_code = status
        self.headers = {"location": location} if location else {}
        self.text = text


def resolver(table):
    def getaddrinfo(host, port, *a, **k):
        if host not in table:
            raise OSError("no such host")
        return [(None, None, None, None, (table[host], 0))]

    return getaddrinfo


@pytest.fixture
def dns(monkeypatch):
    monkeypatch.setattr(
        urlguard.socket, "getaddrinfo",
        resolver({"example.com": "93.184.216.34", "nas.lan": "192.168.1.20",
                  "evil.example": "127.0.0.1", "meta.example": "169.254.169.254"}),
    )


def test_loopback_link_local_and_localhost_are_refused(dns):
    for url in ("http://127.0.0.1:8642/api/info", "http://localhost/", "http://[::1]/",
                "http://evil.example/", "http://meta.example/latest/meta-data",
                "http://0.0.0.0/"):
        with pytest.raises(urlguard.BlockedURL):
            urlguard.check_url(url)


def test_public_and_lan_hosts_are_allowed(dns):
    urlguard.check_url("https://example.com/page")
    urlguard.check_url("http://nas.lan/wiki")  # a household's own wiki is a feature


def test_schemes_and_unresolvable_hosts(dns):
    with pytest.raises(urlguard.BlockedURL, match="http"):
        urlguard.check_url("file:///etc/passwd")
    with pytest.raises(urlguard.BlockedURL, match="resolve"):
        urlguard.check_url("https://nowhere.invalid/")


def test_every_redirect_hop_is_checked(dns, monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        assert kw["follow_redirects"] is False
        if url == "https://example.com/start":
            return FakeResponse(302, "/next")
        if url == "https://example.com/next":
            return FakeResponse(301, "http://evil.example/steal")
        return FakeResponse(text="landed")

    monkeypatch.setattr(urlguard.httpx, "get", fake_get)
    with pytest.raises(urlguard.BlockedURL, match="this machine"):
        urlguard.get("https://example.com/start", timeout=1, headers={})
    # the relative hop was resolved against the page, the bad one never fetched
    assert calls == ["https://example.com/start", "https://example.com/next"]


def test_a_benign_redirect_is_followed_to_its_end(dns, monkeypatch):
    def fake_get(url, **kw):
        if url.endswith("/old"):
            return FakeResponse(308, "https://example.com/new")
        return FakeResponse(text="new page")

    monkeypatch.setattr(urlguard.httpx, "get", fake_get)
    assert urlguard.get("https://example.com/old", timeout=1, headers={}).text == "new page"


def test_redirect_loops_end(dns, monkeypatch):
    monkeypatch.setattr(
        urlguard.httpx, "get", lambda url, **kw: FakeResponse(302, "https://example.com/again")
    )
    with pytest.raises(urlguard.BlockedURL, match="more than"):
        urlguard.get("https://example.com/again", timeout=1, headers={})
