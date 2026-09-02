"""The MCP endpoint the network can reach.

stdio is the right transport for a client that can fork the process. Nothing
across a container or machine boundary can, so an agent running anywhere but
this host had no way in at all — which is the gap this closes.

## Auth

A ``ctx_`` Bearer key, checked before a single byte reaches the MCP session
manager. That ordering matters: the manager holds per-session state, so letting
an unauthenticated request establish a session and then rejecting it would let
anyone who can reach the port allocate memory here.

The key resolves to a cortex USER, and every tool call is attributed to it.
There is deliberately no anonymous mode and no "trusted network" bypass: this
endpoint can write to the vault, and a brain reachable over a tailnet is
reachable by every device on it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cortex.brain import Brain

#: One MCP request may carry a whole document on its way into the vault, but a
#: 4 MB ceiling still stops a single POST from being a memory attack.
MAX_BODY = 4 * 1024 * 1024


def build_asgi(brain: Brain):
    """An ASGI app serving MCP over streamable HTTP, or None if unavailable.

    Returns None rather than raising when the SDK is missing: cortex runs fine
    without MCP, and a brain that refuses to start because an optional export
    is unavailable would be trading a whole service for a feature.
    """
    try:
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    except ImportError:
        return None

    from starlette.responses import JSONResponse

    from cortex import auth
    from cortex.mcp.server import build_server

    server = build_server(brain, source="mcp-http")
    # stateless: every request carries its own context and nothing is kept
    # between them. The alternative keeps sessions in memory keyed by a header,
    # which is a leak waiting for a client that never says goodbye — and this
    # endpoint's clients are schedulers that restart on their own timetable.
    manager = StreamableHTTPSessionManager(
        app=server, stateless=True, json_response=True, max_request_body_size=MAX_BODY
    )
    started = False

    async def app(scope, receive, send):
        nonlocal started
        if scope["type"] != "http":
            return

        token = ""
        for key, value in scope.get("headers") or []:
            if key.lower() == b"authorization":
                raw = value.decode("latin-1")
                if raw.lower().startswith("bearer "):
                    token = raw[7:].strip()
                break

        username = auth.check_api_key(brain.store, token) if token else None
        if username is None:
            # 401 with the challenge, so a client knows WHICH scheme to retry
            # with rather than guessing. Deliberately says nothing about
            # whether the key was absent, malformed or revoked.
            response = JSONResponse(
                {"error": "a ctx_ Bearer key is required"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="cortex"'},
            )
            await response(scope, receive, send)
            return

        if not started:
            # The manager's run() is a context manager owning a task group, and
            # it must be entered exactly once for the life of the process. It
            # is entered lazily here rather than at import so that a brain that
            # never receives an MCP request never starts one.
            await _start(manager)
            started = True

        await manager.handle_request(scope, receive, send)

    return app


async def _start(manager) -> None:
    """Enter the session manager's context and leave it running.

    A task group cannot be entered in one task and exited in another — anyio
    raises if you try — so the context is held open inside a background task
    for the life of the process, and the caller only waits for it to be ready.
    Held open on purpose: the manager owns the transport, and re-entering it
    per request would tear that down under any response still in flight.
    """
    import asyncio

    ready: asyncio.Future = asyncio.get_running_loop().create_future()

    async def runner():
        try:
            async with manager.run():
                if not ready.done():
                    ready.set_result(None)
                # Cancelled at process shutdown, which is what closes the
                # context cleanly.
                await asyncio.Event().wait()
        except Exception as exc:                                # noqa: BLE001
            if not ready.done():
                ready.set_exception(exc)

    task = asyncio.create_task(runner())
    # Held so the task is not garbage collected mid-flight, which asyncio
    # permits and which would close the transport for no visible reason.
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)
    await ready


#: Strong references to the manager tasks; see _start.
_RUNNING: set = set()
