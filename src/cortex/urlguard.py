"""Fetch a URL for the agent without letting a redirect walk it inside.

``fetch_url`` and ``clip_url`` follow redirects, and a public page can 302
to ``http://127.0.0.1:8642/api/…`` — this very server, or the model
endpoint beside it, or the cloud metadata address — and the agent then
reads whatever came back. The first scheduled review of this repository
raised it (reviews/cortex-2026-09-10.md, F2).

The line drawn here: **loopback, link-local and unspecified addresses are
refused; the private LAN is not.** A self-hosted brain lives on a LAN and
"read the wiki on the NAS" is a feature, while nothing legitimate lives
behind 127.0.0.1 or 169.254.169.254 that a web page should be able to
point the agent at. Every hop is checked, not just the first URL, because
the redirect is the attack.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

MAX_HOPS = 5


class BlockedURL(ValueError):
    pass


def check_url(url: str) -> None:
    """Raise BlockedURL when ``url`` must not be fetched on the agent's behalf."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise BlockedURL("only http and https URLs can be fetched")
    host = parsed.hostname
    if not host:
        raise BlockedURL("that URL has no host")
    if host.lower() in ("localhost", "localhost.localdomain"):
        raise BlockedURL(f"{host} is this machine; the agent may not read it")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise BlockedURL(f"could not resolve {host}") from exc
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if addr.is_loopback or addr.is_link_local or addr.is_unspecified:
            raise BlockedURL(
                f"{host} resolves to {addr}, which is this machine or a link-local "
                "service; the agent may not read it"
            )


def get(url: str, *, timeout: float, headers: dict[str, str]) -> httpx.Response:
    """``httpx.get`` that follows redirects one hop at a time, checking each.

    Raises BlockedURL for a hop that must not be taken and httpx.HTTPError for
    transport failures, the same way ``httpx.get`` does."""
    current = url
    for _ in range(MAX_HOPS + 1):
        check_url(current)
        res = httpx.get(current, timeout=timeout, follow_redirects=False, headers=headers)
        if res.status_code in (301, 302, 303, 307, 308) and res.headers.get("location"):
            current = urljoin(current, res.headers["location"])
            continue
        return res
    raise BlockedURL(f"{url} redirected more than {MAX_HOPS} times")
