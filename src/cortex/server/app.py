"""HTTP surface: a small FastAPI app with an SSE chat stream and a
single-page web chat, styled with the vendored Unchained Labs tokens.

Auth modes (cortex.yaml, ``server.auth``):

* ``none`` — no credentials; the CLI only binds loopback in this mode.
* ``key`` — every /api route requires ``Authorization: Bearer ctx_…``
  (issue with `cortex keys issue <name>`); the web page asks for the key
  once and keeps it in localStorage.
"""

from __future__ import annotations

import json
from importlib import resources

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel

from cortex import keys as keymod
from cortex.brain import Brain
from cortex.events import AgentEvent
from cortex.memory.search import hybrid_search

_ASSET_TYPES = {".css": "text/css", ".svg": "image/svg+xml"}


class ChatRequest(BaseModel):
    message: str
    thread: str = ""


def _asset(name: str) -> bytes:
    return (resources.files("cortex.server") / "web" / name).read_bytes()


def build_app(brain: Brain) -> FastAPI:
    app = FastAPI(title=brain.config.name, docs_url=None, redoc_url=None)
    require_key = brain.config.server_auth == "key"

    def check_auth(request: Request) -> None:
        if not require_key:
            return
        header = request.headers.get("authorization", "")
        token = header.removeprefix("Bearer ").strip()
        if not token or not brain.store.key_valid(keymod.hash_key(token)):
            raise HTTPException(status_code=401, detail="a valid ctx_ key is required")

    @app.get("/health")
    def health() -> dict:  # no-auth: liveness probe
        return {"ok": True, "brain": brain.config.name}

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:  # no-auth: static shell; every data route checks the key
        return _asset("index.html").decode("utf-8")

    @app.get("/assets/{name}")
    def asset(name: str) -> Response:  # no-auth: static stylesheet/logo
        suffix = "." + name.rsplit(".", 1)[-1]
        if "/" in name or suffix not in _ASSET_TYPES:
            raise HTTPException(status_code=404)
        try:
            body = _asset(name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404) from exc
        return Response(content=body, media_type=_ASSET_TYPES[suffix])

    @app.get("/api/info")
    def info(request: Request) -> dict:
        check_auth(request)
        chat = brain.config.provider_for("chat")
        embed = brain.config.provider_for("embed")
        return {
            "brain": brain.config.name,
            "stats": brain.store.stats(),
            "chat_model": chat.chat_model if chat else "",
            "embed_model": embed.embed_model if embed else "",
            "tools": [p.name for p in brain.registry.plugins()],
        }

    @app.get("/api/search")
    def search(request: Request, q: str) -> dict:
        check_auth(request)
        import time as _time

        vector = brain.embed_query_sync(q)
        result = hybrid_search(brain.store, q, vector, now=_time.time())
        return {
            "used_vectors": result.used_vectors,
            "hits": [
                {
                    "path": h.path,
                    "score": h.score,
                    "passages": [
                        {"heading": p.heading, "text": p.text, "start_line": p.start_line}
                        for p in h.passages
                    ],
                }
                for h in result.hits
            ],
        }

    @app.get("/api/history")
    def history(request: Request, thread: str) -> dict:
        check_auth(request)
        rows = brain.store.history(thread)
        return {
            "thread": thread,
            "messages": [
                {"role": r["role"], "body": r["body"], "at": r["created_at"]} for r in rows
            ],
        }

    @app.post("/api/chat")
    async def chat(request: Request, body: ChatRequest) -> StreamingResponse:
        check_auth(request)
        thread = body.thread or Brain.new_thread()

        async def stream():
            import asyncio

            queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

            async def sink(event: AgentEvent) -> None:
                await queue.put(event)

            async def worker() -> None:
                try:
                    await brain.chat_turn(thread, body.message, sink)
                except Exception as exc:  # noqa: BLE001 - report to the client, not a 500 mid-stream
                    await queue.put(AgentEvent("error", {"text": str(exc)}))
                finally:
                    await queue.put(None)

            task = asyncio.create_task(worker())
            yield _frame(AgentEvent("thread", {"thread": thread}))
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield _frame(event)
            await task

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


def _frame(event: AgentEvent) -> str:
    return f"data: {json.dumps({'type': event.type, **event.data}, ensure_ascii=False)}\n\n"
