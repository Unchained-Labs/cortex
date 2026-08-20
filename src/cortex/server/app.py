"""The dashboard backend: auth, agent chat, vaults, peer channels, admin.

One FastAPI app per brain. Contract: docs/product-spec.md — the frontend
in web/ is built against exactly these shapes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from importlib import resources
from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cortex import auth, scope, vaults
from cortex.brain import Brain
from cortex.events import AgentEvent
from cortex.memory.search import hybrid_search

_ASSET_TYPES = {".css": "text/css", ".svg": "image/svg+xml"}
_MENTION = re.compile(r"@cortex\b", re.IGNORECASE)
CHANNEL_SCOPE = ("vaults/shared/", "sources/")  # the agent never reads personal vaults in public


# -- request bodies --------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


class ChatRequest(BaseModel):
    message: str
    thread: str = ""


class FileWrite(BaseModel):
    vault: str
    path: str
    text: str = ""
    base_mtime: float | None = None


class ChannelCreate(BaseModel):
    name: str


class ChannelPost(BaseModel):
    body: str


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "member"


class ImportRequest(BaseModel):
    vault: str
    git_url: str = ""
    src_path: str = ""


# -- websocket fan-out ------------------------------------------------------


class WsManager:
    def __init__(self) -> None:
        self._clients: dict[WebSocket, str] = {}

    async def connect(self, ws: WebSocket, username: str) -> None:
        await ws.accept()
        self._clients[ws] = username

    def drop(self, ws: WebSocket) -> None:
        self._clients.pop(ws, None)

    async def broadcast(self, event: dict) -> None:
        payload = json.dumps(event, ensure_ascii=False)
        for ws in list(self._clients):
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001 - a dead socket is dropped, not fatal
                self.drop(ws)


# -- app --------------------------------------------------------------------


def build_app(brain: Brain) -> FastAPI:
    secret = auth.load_secret(brain.config.state_dir)
    ws_manager = WsManager()
    reindex_wanted = asyncio.Event()
    state: dict = {"runtime": None}
    agent_lock = asyncio.Lock()  # one agent turn at a time keeps SQLite happy

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        brain.store.ensure_channel("general", "cortex")
        runtime = brain.runtime()
        await runtime.__aenter__()
        state["runtime"] = runtime
        worker = asyncio.create_task(_reindex_worker())
        try:
            yield
        finally:
            worker.cancel()
            await runtime.__aexit__(None, None, None)
            brain.close()

    app = FastAPI(title=brain.config.name, docs_url=None, redoc_url=None, lifespan=lifespan)

    async def _reindex_worker() -> None:
        """Debounced incremental re-index after vault writes/imports."""
        from cortex.memory.indexer import run_index
        from cortex.providers import ProviderError

        while True:
            await reindex_wanted.wait()
            await asyncio.sleep(2.0)
            reindex_wanted.clear()
            embedder = brain.embedder()
            try:
                await run_index(brain.config, brain.store, embedder)
            except ProviderError:
                await run_index(brain.config, brain.store, None)
            except Exception:  # noqa: BLE001 - indexing must not kill the app
                pass

    # -- auth -------------------------------------------------------------

    def current_user(request: Request) -> dict:
        token = request.cookies.get(auth.SESSION_COOKIE, "")
        username = auth.check_session(secret, token) if token else None
        row = brain.store.get_user(username) if username else None
        if row is None:
            raise HTTPException(status_code=401, detail="sign in required")
        return {"username": row["username"], "role": row["role"]}

    def admin_user(user: dict = Depends(current_user)) -> dict:
        if user["role"] != "admin":
            raise HTTPException(status_code=403, detail="admin only")
        return user

    def user_scope(user: dict) -> tuple[str, ...]:
        extra = [p.name for p in brain.config.extra_paths if p.is_dir()]
        return scope.user_prefixes(user["username"], extra)

    @app.post("/api/auth/login")
    def login(body: LoginRequest, response: Response) -> dict:
        row = brain.store.get_user(body.username.strip().lower())
        if row is None or not auth.verify_password(body.password, row["pw_hash"], row["salt"]):
            raise HTTPException(status_code=401, detail="wrong username or password")
        response.set_cookie(
            auth.SESSION_COOKIE,
            auth.mint_session(secret, row["username"]),
            httponly=True,
            samesite="lax",
            max_age=auth.SESSION_DAYS * 86400,
        )
        return {"username": row["username"], "role": row["role"]}

    @app.post("/api/auth/logout")
    def logout(response: Response) -> dict:  # no-auth: clearing a cookie is harmless
        response.delete_cookie(auth.SESSION_COOKIE)
        return {"ok": True}

    @app.get("/api/me")
    def me(user: dict = Depends(current_user)) -> dict:
        return user

    # -- info / search ----------------------------------------------------

    @app.get("/health")
    def health() -> dict:  # no-auth: liveness probe
        return {"ok": True, "brain": brain.config.name}

    @app.get("/api/info")
    def info(user: dict = Depends(current_user)) -> dict:
        chat_profile = brain.config.provider_for("chat")
        embed_profile = brain.config.provider_for("embed")
        return {
            "brain": brain.config.name,
            "stats": brain.store.stats(),
            "chat_model": chat_profile.chat_model if chat_profile else "",
            "embed_model": embed_profile.embed_model if embed_profile else "",
            "tools": [p.name for p in brain.registry.plugins()],
        }

    @app.get("/api/search")
    def search(q: str, user: dict = Depends(current_user)) -> dict:
        prefixes = user_scope(user)
        vector = brain.embed_query_sync(q)
        result = hybrid_search(
            brain.store, q, vector, now=time.time(), prefixes=prefixes
        )
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

    # -- agent chat -------------------------------------------------------

    @app.get("/api/threads")
    def threads(user: dict = Depends(current_user)) -> dict:
        rows = brain.store.list_threads(user["username"])
        return {
            "threads": [
                {"thread": r["thread"], "title": r["title"], "updated_at": r["updated_at"]}
                for r in rows
            ]
        }

    @app.get("/api/history")
    def history(thread: str, user: dict = Depends(current_user)) -> dict:
        owner = brain.store.thread_owner(thread)
        if owner is not None and owner != user["username"]:
            raise HTTPException(status_code=404, detail="no such thread")
        rows = brain.store.history(thread, limit=200)
        return {
            "thread": thread,
            "messages": [
                {"role": r["role"], "body": r["body"], "at": r["created_at"]} for r in rows
            ],
        }

    @app.post("/api/chat")
    async def chat(body: ChatRequest, user: dict = Depends(current_user)) -> StreamingResponse:
        thread = body.thread or Brain.new_thread()
        owner = brain.store.thread_owner(thread)
        if owner is not None and owner != user["username"]:
            raise HTTPException(status_code=404, detail="no such thread")
        prefixes = user_scope(user)

        async def stream():
            queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()

            async def sink(event: AgentEvent) -> None:
                await queue.put(event)

            async def worker() -> None:
                try:
                    async with agent_lock:
                        with scope.scoped(prefixes, user["username"]):
                            answer = await state["runtime"].run(thread, body.message, sink)
                    brain.record_turn(thread, user["username"], body.message, answer)
                except Exception as exc:  # noqa: BLE001 - stream the failure, not a 500
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

    # -- vaults -----------------------------------------------------------

    def _check_vault_access(user: dict, vault: str) -> None:
        if vault != "shared" and vault != user["username"]:
            raise HTTPException(status_code=404, detail="no such vault")

    def _ensure_personal_vault(user: dict) -> None:
        (brain.config.vaults_dir / user["username"]).mkdir(parents=True, exist_ok=True)

    @app.get("/api/vaults")
    def list_vaults(user: dict = Depends(current_user)) -> dict:
        _ensure_personal_vault(user)
        brain.config.shared_vault.mkdir(parents=True, exist_ok=True)
        out = []
        for name in ("shared", user["username"]):
            files = vaults.list_tree(brain.config, name)
            kind = "shared" if name == "shared" else "personal"
            out.append({"name": name, "kind": kind, "files": len(files)})
        return {"vaults": out}

    @app.get("/api/vault/tree")
    def vault_tree(vault: str, user: dict = Depends(current_user)) -> dict:
        _check_vault_access(user, vault)
        _ensure_personal_vault(user)
        try:
            return {"vault": vault, "files": vaults.list_tree(brain.config, vault)}
        except vaults.VaultError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/vault/file")
    def vault_read(vault: str, path: str, user: dict = Depends(current_user)) -> dict:
        _check_vault_access(user, vault)
        try:
            text, mtime = vaults.read_file(brain.config, vault, path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="no such file") from exc
        except vaults.VaultError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"vault": vault, "path": path, "text": text, "mtime": mtime}

    @app.get("/api/vault/raw")
    def vault_raw(vault: str, path: str, user: dict = Depends(current_user)) -> Response:
        _check_vault_access(user, vault)
        try:
            data = vaults.read_raw(brain.config, vault, path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="no such file") from exc
        except vaults.VaultError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return Response(content=data, media_type="application/octet-stream")

    async def _after_write(vault: str, path: str) -> None:
        reindex_wanted.set()
        await ws_manager.broadcast({"type": "vault_changed", "vault": vault, "path": path})

    @app.put("/api/vault/file")
    async def vault_write(body: FileWrite, user: dict = Depends(current_user)) -> dict:
        _check_vault_access(user, body.vault)
        try:
            mtime = vaults.write_file(
                brain.config, body.vault, body.path, body.text, base_mtime=body.base_mtime
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="no such file") from exc
        except vaults.VaultError as exc:
            if str(exc) == "conflict":
                server_mtime = vaults.read_file(brain.config, body.vault, body.path)[1]
                return JSONResponse(
                    status_code=409,
                    content={"detail": "conflict", "server_mtime": server_mtime},
                )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await _after_write(body.vault, body.path)
        return {"mtime": mtime}

    @app.post("/api/vault/file")
    async def vault_create(body: FileWrite, user: dict = Depends(current_user)) -> dict:
        _check_vault_access(user, body.vault)
        _ensure_personal_vault(user)
        try:
            mtime = vaults.write_file(
                brain.config, body.vault, body.path, body.text, create=True
            )
        except vaults.VaultError as exc:
            code = 409 if str(exc) == "exists" else 400
            raise HTTPException(status_code=code, detail=str(exc)) from exc
        await _after_write(body.vault, body.path)
        return {"mtime": mtime}

    @app.delete("/api/vault/file")
    async def vault_delete(vault: str, path: str, user: dict = Depends(current_user)) -> dict:
        _check_vault_access(user, vault)
        try:
            vaults.delete_file(brain.config, vault, path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="no such file") from exc
        except vaults.VaultError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await _after_write(vault, path)
        return {"ok": True}

    @app.post("/api/vault/import")
    async def vault_import(
        request: Request,
        user: dict = Depends(current_user),
        file: UploadFile | None = None,
    ) -> dict:
        content_type = request.headers.get("content-type", "")
        try:
            if file is not None:
                form = await request.form()
                vault = str(form.get("vault") or "")
                _check_vault_access(user, vault)
                _ensure_personal_vault(user)
                report = await asyncio.to_thread(
                    vaults.import_zip, brain.config, vault, await file.read()
                )
            elif content_type.startswith("application/json"):
                body = ImportRequest(**(await request.json()))
                _check_vault_access(user, body.vault)
                _ensure_personal_vault(user)
                if body.git_url:
                    report = await asyncio.to_thread(
                        vaults.import_git, brain.config, body.vault, body.git_url
                    )
                elif body.src_path:
                    report = await asyncio.to_thread(
                        vaults.import_path, brain.config, body.vault, body.src_path
                    )
                else:
                    raise HTTPException(status_code=422, detail="git_url or src_path required")
            else:
                raise HTTPException(status_code=422, detail="zip upload or JSON body required")
        except vaults.VaultError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        reindex_wanted.set()
        return {"imported": report.imported, "skipped": report.skipped}

    # -- channels ---------------------------------------------------------

    @app.get("/api/channels")
    def channels(user: dict = Depends(current_user)) -> dict:
        rows = brain.store.list_channels()
        return {
            "channels": [
                {"id": r["id"], "name": r["name"], "created_by": r["created_by"]} for r in rows
            ]
        }

    @app.post("/api/channels")
    def channel_create(body: ChannelCreate, user: dict = Depends(current_user)) -> dict:
        name = body.name.strip().lstrip("#").lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", name):
            raise HTTPException(status_code=422, detail="bad channel name")
        channel_id = brain.store.ensure_channel(name, user["username"])
        return {"id": channel_id, "name": name}

    @app.get("/api/channels/{channel_id}/messages")
    def channel_history(
        channel_id: int,
        user: dict = Depends(current_user),
        before: int | None = None,
        limit: int = 50,
    ) -> dict:
        if not brain.store.channel_exists(channel_id):
            raise HTTPException(status_code=404, detail="no such channel")
        rows = brain.store.channel_messages(channel_id, before=before, limit=min(limit, 200))
        return {
            "messages": [
                {"id": r["id"], "author": r["author"], "body": r["body"], "at": r["created_at"]}
                for r in rows
            ]
        }

    @app.post("/api/channels/{channel_id}/messages")
    async def channel_post(
        channel_id: int, body: ChannelPost, user: dict = Depends(current_user)
    ) -> dict:
        text = body.body.strip()
        if not text:
            raise HTTPException(status_code=422, detail="empty message")
        if not brain.store.channel_exists(channel_id):
            raise HTTPException(status_code=404, detail="no such channel")
        message_id, at = brain.store.add_channel_message(channel_id, user["username"], text)
        await ws_manager.broadcast(
            {
                "type": "channel_message",
                "channel_id": channel_id,
                "message": {"id": message_id, "author": user["username"], "body": text, "at": at},
            }
        )
        if _MENTION.search(text):
            asyncio.create_task(_agent_channel_reply(channel_id, user["username"], text))
        return {"id": message_id, "at": at}

    async def _agent_channel_reply(channel_id: int, author: str, text: str) -> None:
        """The agent answers in-channel with shared-only scope; its partial
        output streams over WS and the final message is persisted."""
        partial_id = f"agent-{channel_id}-{time.time_ns()}"
        buffer: list[str] = []

        async def sink(event: AgentEvent) -> None:
            if event.type == "token":
                buffer.append(event.data["text"])
                await ws_manager.broadcast(
                    {
                        "type": "agent_partial",
                        "channel_id": channel_id,
                        "message_id": partial_id,
                        "text": "".join(buffer),
                    }
                )

        try:
            async with agent_lock:
                with scope.scoped(CHANNEL_SCOPE, "cortex"):
                    answer = await state["runtime"].run(
                        f"channel-{channel_id}", f"[{author} in channel] {text}", sink
                    )
        except Exception as exc:  # noqa: BLE001 - a failed reply becomes a visible message
            answer = f"(cortex could not answer: {exc})"
        if not answer.strip():
            answer = "(cortex had nothing to add.)"
        message_id, at = brain.store.add_channel_message(channel_id, "cortex", answer)
        await ws_manager.broadcast(
            {
                "type": "channel_message",
                "channel_id": channel_id,
                "message": {
                    "id": message_id,
                    "author": "cortex",
                    "body": answer,
                    "at": at,
                    "replaces": partial_id,
                },
            }
        )

    # -- admin ------------------------------------------------------------

    @app.get("/api/admin/users")
    def users(user: dict = Depends(admin_user)) -> dict:
        rows = brain.store.list_users()
        return {
            "users": [
                {"username": r["username"], "role": r["role"], "created_at": r["created_at"]}
                for r in rows
            ]
        }

    @app.post("/api/admin/users", status_code=201)
    def user_create(body: UserCreate, user: dict = Depends(admin_user)) -> dict:
        try:
            username = auth.validate_username(body.username.strip().lower())
        except auth.AuthError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if body.role not in ("admin", "member"):
            raise HTTPException(status_code=422, detail="role is admin or member")
        if len(body.password) < 8:
            raise HTTPException(status_code=422, detail="password must be 8+ characters")
        if brain.store.get_user(username) is not None:
            raise HTTPException(status_code=409, detail="user exists")
        pw_hash, salt = auth.hash_password(body.password)
        brain.store.add_user(username, pw_hash, salt, body.role)
        (brain.config.vaults_dir / username).mkdir(parents=True, exist_ok=True)
        return {"username": username, "role": body.role}

    @app.delete("/api/admin/users/{username}")
    def user_delete(username: str, user: dict = Depends(admin_user)) -> dict:
        if username == user["username"]:
            raise HTTPException(status_code=422, detail="cannot delete yourself")
        if not brain.store.delete_user(username):
            raise HTTPException(status_code=404, detail="no such user")
        return {"ok": True}

    # -- websocket --------------------------------------------------------

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        token = websocket.cookies.get(auth.SESSION_COOKIE, "")
        username = auth.check_session(secret, token) if token else None
        if not username or brain.store.get_user(username) is None:
            await websocket.close(code=4401)
            return
        await ws_manager.connect(websocket, username)
        try:
            while True:
                await websocket.receive_text()  # pings; all mutations are REST
        except WebSocketDisconnect:
            ws_manager.drop(websocket)

    # -- static -----------------------------------------------------------

    webdist = _webdist_dir()
    if webdist is not None and (webdist / "app").is_dir():
        app.mount("/app", StaticFiles(directory=webdist / "app"), name="app")

    @app.get("/", response_class=HTMLResponse)
    def index():  # no-auth: static shell; every data route checks the session
        if webdist is not None and (webdist / "index.html").is_file():
            return FileResponse(webdist / "index.html")
        return HTMLResponse(
            "<h1>cortex</h1><p>The dashboard is not built. Run "
            "<code>cd web && npm install && npm run build</code>.</p>"
        )

    @app.get("/{name}.svg")
    def root_svg(name: str) -> Response:  # no-auth: favicon + lockup from webdist root
        if webdist is None or "/" in name:
            raise HTTPException(status_code=404)
        target = webdist / f"{name}.svg"
        if not target.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(target, media_type="image/svg+xml")

    @app.get("/assets/{name}")
    def asset(name: str) -> Response:  # no-auth: brand stylesheet/logo
        suffix = "." + name.rsplit(".", 1)[-1]
        if "/" in name or suffix not in _ASSET_TYPES:
            raise HTTPException(status_code=404)
        try:
            body = (resources.files("cortex.server") / "web" / name).read_bytes()
        except (FileNotFoundError, OSError) as exc:
            raise HTTPException(status_code=404) from exc
        return Response(content=body, media_type=_ASSET_TYPES[suffix])

    return app


def _webdist_dir() -> Path | None:
    try:
        path = Path(str(resources.files("cortex.server"))) / "webdist"
    except Exception:  # noqa: BLE001
        return None
    return path if path.is_dir() else None


def _frame(event: AgentEvent) -> str:
    return f"data: {json.dumps({'type': event.type, **event.data}, ensure_ascii=False)}\n\n"
