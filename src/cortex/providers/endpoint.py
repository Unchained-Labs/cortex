"""Classify a model endpoint by network facts, not vendor guesses.

An endpoint is *trusted* when its hostname is a Tailscale name or every
address it resolves to is private, loopback, link-local, or CGNAT space.
Anything else is *public*: allowed, but the CLI says so out loud, because
DNS cannot prove whether a remote host is self-hosted.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_TAILSCALE_SUFFIXES = (".ts.net", ".tailscale.net")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def endpoint_scope(url: str) -> str:
    """Return "trusted", "public", or "unresolved"."""
    host = urlparse(url).hostname
    if not host:
        return "unresolved"
    lowered = host.lower()
    if lowered.endswith(_TAILSCALE_SUFFIXES):
        return "trusted"
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return "unresolved"
    addrs = []
    for info in infos:
        try:
            addrs.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    if not addrs:
        return "unresolved"
    if all(a.is_private or a.is_loopback or a.is_link_local or a in _CGNAT for a in addrs):
        return "trusted"
    return "public"
