"""The dashboard backend: auth, agent chat, vaults, peer channels, admin.

One FastAPI app per brain. Contract: docs/product-spec.md — the frontend
in web/ is built against exactly these shapes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import sys
import time
from datetime import date
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

from cortex import auth, capture, extensions, scope, vaults
from cortex import demo as demomod
from cortex import digest as digestmod
from cortex import jobs as jobsmod
from cortex import library as librarymod
from cortex import rules as rulesmod
from cortex.brain import Brain
from cortex.events import AgentEvent
from cortex.memory.search import hybrid_search

_ASSET_TYPES = {".css": "text/css", ".svg": "image/svg+xml"}
_MENTION = re.compile(r"@cortex\b", re.IGNORECASE)
_ANY_MENTION = re.compile(r"@([a-z0-9][a-z0-9_-]{1,31})", re.IGNORECASE)
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


class ExtensionSave(BaseModel):
    name: str = ""
    code: str = ""
    description: str = ""
    instructions: str = ""
    settings: dict | None = None
    spec: dict | None = None


class EnabledBody(BaseModel):
    enabled: bool


class SettingsBody(BaseModel):
    settings: dict


class CaptureBody(BaseModel):
    text: str
    vault: str = ""


class PasswordChange(BaseModel):
    current_password: str = ""
    new_password: str


class PasswordReset(BaseModel):
    new_password: str


class IdentitySave(BaseModel):
    text: str
    base_mtime: float | None = None


class TemplateSave(BaseModel):
    name: str
    body: str


class NoteFromTemplate(BaseModel):
    template: str
    vault: str = "shared"
    title: str


class MemoryBody(BaseModel):
    body: str
    kind: str = "fact"
    subject: str = ""


class MentionsRead(BaseModel):
    channel_id: int | None = None


class RuleBody(BaseModel):
    rule: dict


class JobBody(BaseModel):
    job: dict


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
        await self._send(event, to=None)

    async def send_to(self, username: str, event: dict) -> None:
        """One user's sockets only — used for anything naming a personal
        vault, which nobody else is allowed to know exists."""
        await self._send(event, to=username)

    async def _send(self, event: dict, to: str | None) -> None:
        payload = json.dumps(event, ensure_ascii=False)
        for ws, owner in list(self._clients.items()):
            if to is not None and owner != to:
                continue
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001 - a dead socket is dropped, not fatal
                self.drop(ws)


# -- app --------------------------------------------------------------------


def build_app(brain: Brain) -> FastAPI:
    secret = auth.load_secret(brain.config.state_dir)
    ws_manager = WsManager()
    reindex_wanted = asyncio.Event()
    state: dict = {"runtime": None, "indexing": False, "model_error": ""}
    agent_lock = asyncio.Lock()  # one agent turn at a time keeps SQLite happy

    # tools that write (capture_note, complete_task) ask the brain to
    # re-index; here that means nudging the debounced worker below
    brain._reindex_hook = reindex_wanted.set

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        brain.store.ensure_channel("general", "cortex")
        runtime = brain.runtime()
        await runtime.__aenter__()
        state["runtime"] = runtime
        worker = asyncio.create_task(_reindex_worker())
        schedule = asyncio.create_task(_connector_worker())
        clock = asyncio.create_task(_job_worker())
        try:
            yield
        finally:
            worker.cancel()
            schedule.cancel()
            clock.cancel()
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
            state["indexing"] = True
            try:
                await run_index(brain.config, brain.store, embedder)
            except ProviderError:
                await run_index(brain.config, brain.store, None)
            except Exception:  # noqa: BLE001 - indexing must not kill the app
                pass
            finally:
                state["indexing"] = False
                await ws_manager.broadcast(
                    {"type": "index_done", "stats": brain.store.stats()}
                )

    def _load_rules() -> list:
        out = []
        for row in brain.store.list_rules():
            try:
                out.append(rulesmod.parse_rule(json.loads(row["spec"])))
            except (rulesmod.RuleError, ValueError):
                continue
        return out

    def _load_jobs() -> list:
        out = []
        for row in brain.store.list_jobs():
            try:
                job = jobsmod.parse_job(json.loads(row["spec"]))
            except (jobsmod.JobError, ValueError):
                continue
            job.enabled = bool(row["enabled"])
            job.last_run = row["last_run"]
            job.last_status = row["last_status"]
            job.last_detail = row["last_detail"]
            out.append(job)
        return out

    async def _run_job(job) -> tuple[str, str]:
        """Execute one job. Returns (status, human-readable detail)."""
        from cortex.connectors import run_connectors
        from cortex.memory.indexer import run_index

        if job.kind == "connector":
            name = job.settings.get("connector", "")
            settings = extensions.effective_connectors(brain.config, brain.store)
            results = await asyncio.to_thread(run_connectors, brain.config, settings, name)
            outcome = results.get(name, "connector not found or disabled")
            reindex_wanted.set()
            return ("ok" if outcome == "ok" else "error", outcome)

        if job.kind == "index":
            report = await run_index(brain.config, brain.store, brain.embedder())
            return ("ok", f"indexed {report.indexed}, removed {report.removed}")

        if job.kind == "rules":
            rules = _load_rules()
            if job.settings.get("dry_run"):
                planned = await asyncio.to_thread(rulesmod.plan, brain.config, rules)
                return ("ok", f"{len(planned)} changes would be made")
            actions = await asyncio.to_thread(rulesmod.apply, brain.config, rules)
            brain.store.record_rule_actions(actions)
            if actions:
                reindex_wanted.set()
                await ws_manager.broadcast({"type": "rules_ran", "count": len(actions)})
            errors = sum(1 for a in actions if a["action"] == "error")
            detail = f"{len(actions) - errors} filed" + (f", {errors} failed" if errors else "")
            return ("error" if errors else "ok", detail)

        if job.kind in ("digest", "channel_digest"):
            vault = job.settings.get("vault", "shared")
            built = digestmod.build_digest(brain.config, brain.store, vault=vault)
            text = digestmod.format_digest(built)
            if job.kind == "digest":
                rel = f"briefings/{built.day}.md"
                target = vaults.vault_path(brain.config, vault, rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text + "\n", encoding="utf-8")
                await _after_write(vault, rel)
                return ("ok", f"wrote vaults/{vault}/{rel}")
            # An empty digest posts nothing. A scheduled message that says
            # "nothing to report" is exactly what teaches people to ignore
            # the channel it arrives in.
            if built.is_empty():
                return ("ok", "nothing on today, so nothing was posted")
            channel_name = job.settings.get("channel", "general")
            channel_id = brain.store.ensure_channel(channel_name, "cortex")
            message_id, at = brain.store.add_channel_message(channel_id, "cortex", text)
            await ws_manager.broadcast({
                "type": "channel_message",
                "channel_id": channel_id,
                "message": {"id": message_id, "author": "cortex", "body": text, "at": at},
            })
            return ("ok", f"posted into #{channel_name}")

        return ("error", f"unknown job kind {job.kind}")

    async def _job_worker() -> None:
        """The clock. Checks every minute for work that is due."""
        await asyncio.sleep(8)
        while True:
            try:
                for job in _load_jobs():
                    if not job.due():
                        continue
                    try:
                        status, detail = await _run_job(job)
                    except Exception as exc:  # noqa: BLE001 - one bad job, not the loop
                        status, detail = "error", str(exc)
                    brain.store.record_job_run(job.name, status, detail)
                    if status != "ok":
                        print(f"job {job.name}: {detail}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"scheduler error: {exc}", file=sys.stderr)
            await asyncio.sleep(60)

    async def _connector_worker() -> None:
        """Run connectors that ask for a schedule.

        A source that only refreshes when someone remembers to run a command
        is a stale source. Opt in per connector with an `interval_minutes`
        setting (editable in the Extend panel); connectors without one stay
        manual.
        """
        from cortex.connectors import run_connectors

        last: dict[str, float] = {}
        await asyncio.sleep(5)  # let startup settle before doing any work
        while True:
            try:
                settings = extensions.effective_connectors(brain.config, brain.store)
                now = time.monotonic()
                due = []
                for name, conf in settings.items():
                    minutes = conf.get("interval_minutes")
                    if not isinstance(minutes, (int, float)) or minutes <= 0:
                        continue
                    if now - last.get(name, 0.0) >= minutes * 60:
                        due.append(name)
                for name in due:
                    last[name] = now
                    results = await asyncio.to_thread(
                        run_connectors, brain.config, settings, name
                    )
                    outcome = results.get(name, "no result")
                    if outcome != "ok":
                        print(f"connector {name}: {outcome}", file=sys.stderr)
                    reindex_wanted.set()
            except Exception as exc:  # noqa: BLE001 - a bad cycle must not end the loop
                print(f"connector schedule error: {exc}", file=sys.stderr)
            await asyncio.sleep(30)

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

    # One throttle per app instance. In-process is the right scope: cortex is a
    # single-process self-hosted server, and a counter that outlived a restart
    # would lock people out of their own house after a crash.
    throttle = auth.Throttle()

    def set_session_cookie(request: Request, response: Response, username: str) -> None:
        """Mint a session cookie, marked `secure` when the connection deserves it.

        The quick start says `cortex serve --host 0.0.0.0`, so the intended
        deployment is a LAN — where a cookie without this travels in cleartext.
        It is conditional rather than always-on because setting `secure` on plain
        HTTP means the browser silently drops the cookie and nobody can sign in.
        """
        forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
        https = (forwarded or request.url.scheme) == "https"
        response.set_cookie(
            auth.SESSION_COOKIE,
            auth.mint_session(secret, username),
            httponly=True,
            samesite="lax",
            secure=https,
            max_age=auth.SESSION_DAYS * 86400,
        )

    @app.post("/api/auth/login")
    def login(body: LoginRequest, request: Request, response: Response) -> dict:
        username = body.username.strip().lower()
        # Keyed on client and username together: on username alone anyone could
        # lock a housemate out, on address alone one bad client wedges everyone
        # behind the same router.
        key = f"{request.client.host if request.client else '?'}|{username}"

        wait = throttle.retry_after(key)
        if wait > 0:
            raise HTTPException(
                status_code=429,
                detail="too many sign-in attempts — wait a few minutes and try again",
                headers={"Retry-After": str(int(wait) + 1)},
            )

        row = brain.store.get_user(username)
        if row is None or not auth.verify_password(body.password, row["pw_hash"], row["salt"]):
            throttle.record_failure(key)
            raise HTTPException(status_code=401, detail="wrong username or password")

        throttle.clear(key)
        set_session_cookie(request, response, row["username"])
        return {"username": row["username"], "role": row["role"]}

    @app.post("/api/auth/logout")
    def logout(response: Response) -> dict:  # no-auth: clearing a cookie is harmless
        response.delete_cookie(auth.SESSION_COOKIE)
        return {"ok": True}

    @app.get("/api/me")
    def me(user: dict = Depends(current_user)) -> dict:
        return user

    @app.post("/api/me/password")
    def change_password(
        body: PasswordChange,
        request: Request,
        response: Response,
        user: dict = Depends(current_user),
    ) -> dict:
        """Anyone can change their own password, knowing the current one."""
        row = brain.store.get_user(user["username"])
        if row is None or not auth.verify_password(
            body.current_password, row["pw_hash"], row["salt"]
        ):
            raise HTTPException(status_code=403, detail="current password is wrong")
        if len(body.new_password) < 8:
            raise HTTPException(status_code=422, detail="password must be 8+ characters")
        pw_hash, salt = auth.hash_password(body.new_password)
        brain.store.set_password(user["username"], pw_hash, salt)
        # Re-mint: the old cookie stays valid otherwise, which is not what
        # "I changed my password" is supposed to mean.
        set_session_cookie(request, response, user["username"])
        return {"ok": True}

    # -- info / search ----------------------------------------------------

    @app.get("/health")
    def health() -> dict:  # no-auth: liveness probe
        return {"ok": True, "brain": brain.config.name}

    @app.get("/api/info")
    def info(user: dict = Depends(current_user)) -> dict:
        chat_profile = brain.config.provider_for("chat")
        embed_profile = brain.config.provider_for("embed")
        stats = brain.store.stats()
        return {
            "brain": brain.config.name,
            "stats": stats,
            "chat_model": chat_profile.chat_model if chat_profile else "",
            "embed_model": embed_profile.embed_model if embed_profile else "",
            "chat_endpoint": chat_profile.base_url if chat_profile else "",
            "tools": [p.name for p in brain.registry.plugins()],
            # Health the UI can act on rather than leaving the user to guess:
            # an unindexed brain answers nothing, and looks identical to an
            # empty one unless we say so.
            "indexed": stats["files"] > 0,
            "demo_installed": demomod.installed(brain.config),
            "indexing": state["indexing"],
            "model_error": state["model_error"],
        }

    @app.post("/api/demo")
    async def install_demo(user: dict = Depends(admin_user)) -> dict:
        """Seed example notes from the empty state, so a new brain has
        something to search before anyone has written anything."""
        written = await asyncio.to_thread(demomod.install, brain.config, "shared")
        reindex_wanted.set()
        return {"written": written, "count": len(written)}

    @app.delete("/api/demo")
    async def remove_demo(user: dict = Depends(admin_user)) -> dict:
        count = await asyncio.to_thread(demomod.remove, brain.config, "shared")
        reindex_wanted.set()
        return {"removed": count}

    @app.post("/api/reindex")
    async def reindex(user: dict = Depends(admin_user)) -> dict:
        reindex_wanted.set()
        return {"ok": True, "indexing": True}

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

    @app.get("/api/digest")
    def digest(user: dict = Depends(current_user)) -> dict:
        """Today, computed without the model so it always answers."""
        built = digestmod.build_digest(
            brain.config, brain.store, prefixes=user_scope(user), vault=user["username"]
        )
        return built.as_dict()

    @app.post("/api/capture")
    async def capture_note(body: CaptureBody, user: dict = Depends(current_user)) -> dict:
        target = body.vault or user["username"]
        _check_vault_access(user, target)
        _ensure_personal_vault(user)
        try:
            rel, text, lineno = capture.append_note(brain.config, target, body.text)
        except vaults.VaultError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await _after_write(target, rel)
        return {"vault": target, "path": rel, "line": lineno, "text": text}

    # -- identity -----------------------------------------------------------
    #
    # Everyone can read who the brain thinks it is for; admins edit it. The
    # agent may propose and may not write: a system that quietly rewrites
    # its own instructions is one nobody can reason about.

    @app.get("/api/identity")
    def get_identity(user: dict = Depends(current_user)) -> dict:
        from cortex import identity as identitymod

        def as_proposal(row) -> dict:
            return {
                "id": row["id"],
                "text": row["text"],
                "reason": row["reason"],
                "created_at": row["created_at"],
                "status": row["status"],
                "decided_at": row["decided_at"],
                "decided_by": row["decided_by"],
            }

        return {
            "text": identitymod.read(brain.config),
            "starter": identitymod.STARTER,
            "untouched": identitymod.is_untouched(brain.config),
            "persona": brain.config.persona,
            "max_chars": identitymod.MAX_IDENTITY_CHARS,
            "mtime": identitymod.mtime(brain.config),
            "proposals": [as_proposal(r) for r in brain.store.identity_proposals()],
            # what was decided, and by whom — the same question the rules
            # history answers for moved notes
            "decided": [
                as_proposal(r) for r in brain.store.identity_proposals("decided")
            ],
        }

    @app.put("/api/identity")
    async def put_identity(body: IdentitySave, user: dict = Depends(admin_user)) -> dict:
        from cortex import identity as identitymod

        try:
            identitymod.write(brain.config, body.text, base_mtime=body.base_mtime)
        except identitymod.IdentityConflict as exc:
            return JSONResponse(
                status_code=409,
                content={"detail": "conflict", "server_mtime": exc.server_mtime},
            )
        except identitymod.IdentityError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await _reload_agent()
        return {"ok": True, "mtime": identitymod.mtime(brain.config)}

    @app.post("/api/identity/proposals/{proposal_id}/{decision}")
    async def decide_proposal(
        proposal_id: int, decision: str, user: dict = Depends(admin_user)
    ) -> dict:
        from cortex import identity as identitymod

        if decision not in ("accept", "discard"):
            raise HTTPException(status_code=422, detail="accept or discard")
        row = brain.store.get_identity_proposal(proposal_id)
        if row is None or row["status"] != "pending":
            raise HTTPException(status_code=404, detail="no pending proposal with that id")
        if decision == "accept":
            try:
                identitymod.write(brain.config, row["text"])
            except identitymod.IdentityError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        brain.store.decide_identity_proposal(
            proposal_id, "accepted" if decision == "accept" else "discarded",
            user["username"],
        )
        if decision == "accept":
            await _reload_agent()
        return {"ok": True, "decision": decision}

    # -- templates ----------------------------------------------------------
    #
    # Capture handles the unstructured half. A meeting, a trip or a person
    # has a shape, and rebuilding it from memory each time is the friction
    # that stops people writing them down at all.

    @app.get("/api/templates")
    def list_templates(user: dict = Depends(current_user)) -> dict:
        from cortex import templates as templatesmod

        return {
            "templates": [t.as_dict() for t in templatesmod.list_templates(brain.config)],
            "placeholders": sorted(templatesmod.placeholders("Example", user=user["username"])),
        }

    @app.post("/api/templates/install")
    def install_templates(user: dict = Depends(admin_user)) -> dict:
        from cortex import templates as templatesmod

        return {"written": templatesmod.install_builtin(brain.config)}

    @app.put("/api/templates")
    def save_template(body: TemplateSave, user: dict = Depends(admin_user)) -> dict:
        from cortex import templates as templatesmod

        try:
            saved = templatesmod.save(brain.config, body.name, body.body)
        except templatesmod.TemplateError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return saved.as_dict()

    @app.delete("/api/templates/{name}")
    def delete_template(name: str, user: dict = Depends(admin_user)) -> dict:
        from cortex import templates as templatesmod

        if not templatesmod.delete(brain.config, name):
            raise HTTPException(status_code=404, detail="no such template")
        return {"ok": True}

    @app.post("/api/templates/new-note")
    async def note_from_template(
        body: NoteFromTemplate, user: dict = Depends(current_user)
    ) -> dict:
        from cortex import templates as templatesmod

        _check_vault_access(user, body.vault)
        _ensure_personal_vault(user)
        template = templatesmod.get(brain.config, body.template)
        if template is None:
            raise HTTPException(status_code=404, detail="no such template")
        try:
            rel, _ = templatesmod.create_note(
                brain.config, template, body.vault, body.title, user=user["username"]
            )
        except templatesmod.TemplateError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await _after_write(body.vault, rel)
        return {"vault": body.vault, "path": rel}

    # -- memory -----------------------------------------------------------
    #
    # Everyone can see and correct what the brain believes. A brain that
    # quietly remembers a wrong thing about a person is worse than one that
    # remembers nothing, so none of this is admin-only.

    @app.get("/api/memory")
    def list_memory(user: dict = Depends(current_user), kind: str = "") -> dict:
        from cortex.memory import facts as factsmod

        try:
            rows = brain.store.facts_by_kind(
                factsmod.normalise_kind(kind) if kind else ""
            )
        except factsmod.MemoryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "kinds": list(factsmod.KINDS),
            "memories": [
                {
                    "id": r["id"],
                    "kind": r["kind"],
                    "subject": r["subject"],
                    "body": r["body"],
                    "source": r["source"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
        }

    @app.post("/api/memory")
    def add_memory(body: MemoryBody, user: dict = Depends(current_user)) -> dict:
        from cortex.memory import facts as factsmod

        text = body.body.strip()
        if not text:
            raise HTTPException(status_code=422, detail="a memory needs a body")
        try:
            kind = factsmod.normalise_kind(body.kind)
        except factsmod.MemoryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        memory_id = brain.store.add_fact(
            text,
            f"dashboard:{user['username']}",
            kind=kind,
            subject=factsmod.normalise_subject(body.subject),
        )
        return {"id": memory_id, "kind": kind}

    @app.put("/api/memory/{memory_id}")
    def edit_memory(
        memory_id: int, body: MemoryBody, user: dict = Depends(current_user)
    ) -> dict:
        from cortex.memory import facts as factsmod

        text = body.body.strip()
        if not text:
            raise HTTPException(status_code=422, detail="a memory needs a body")
        try:
            kind = factsmod.normalise_kind(body.kind)
        except factsmod.MemoryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not brain.store.update_fact(
            memory_id, text, kind, factsmod.normalise_subject(body.subject)
        ):
            raise HTTPException(status_code=404, detail="no such memory")
        return {"ok": True}

    @app.delete("/api/memory/{memory_id}")
    def forget_memory(memory_id: int, user: dict = Depends(current_user)) -> dict:
        if not brain.store.retire_fact(memory_id):
            raise HTTPException(status_code=404, detail="no such memory")
        return {"ok": True}

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
                    text = _explain_model_failure(exc, brain)
                    state["model_error"] = text
                    await queue.put(AgentEvent("error", {"text": text}))
                else:
                    state["model_error"] = ""
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
        event = {"type": "vault_changed", "vault": vault, "path": path}
        if vault == "shared":
            await ws_manager.broadcast(event)
        else:
            # A personal vault's filenames are as private as its contents:
            # "erwin edited therapy.md" must not reach the whole household.
            await ws_manager.send_to(vault, event)

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

    @app.get("/api/file")
    def read_indexed_file(path: str, user: dict = Depends(current_user)) -> dict:
        """Read any file the caller may see by its index key.

        The vault endpoints only address `vaults/<name>/…`; connector output
        under `sources/…` is readable but was unopenable, so a calendar event
        in the digest linked nowhere.
        """
        prefixes = user_scope(user)
        if not any(path.startswith(p) for p in prefixes):
            raise HTTPException(status_code=404, detail="no such file")
        target = brain.config.resolve_key(path)
        if target is None or not target.is_file():
            raise HTTPException(status_code=404, detail="no such file")
        return {
            "path": path,
            "text": target.read_text(encoding="utf-8", errors="replace"),
            "mtime": target.stat().st_mtime,
            "editable": path.startswith("vaults/"),
        }

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
        mentioned = _record_mentions(message_id, channel_id, text, user["username"])
        await ws_manager.broadcast(
            {
                "type": "channel_message",
                "channel_id": channel_id,
                "message": {
                    "id": message_id,
                    "author": user["username"],
                    "body": text,
                    "at": at,
                    "mentions": mentioned,
                },
            }
        )
        if _MENTION.search(text):
            asyncio.create_task(_agent_channel_reply(channel_id, user["username"], text))
        return {"id": message_id, "at": at}

    def _record_mentions(
        message_id: int, channel_id: int, text: str, author: str
    ) -> list[str]:
        """Record @name for real users other than the author."""
        found: list[str] = []
        for raw in {m.lower() for m in _ANY_MENTION.findall(text)}:
            if raw == author or raw == "cortex":
                continue
            if brain.store.get_user(raw) is None:
                continue
            brain.store.add_mention(message_id, channel_id, raw, author)
            found.append(raw)
        return sorted(found)

    @app.get("/api/mentions")
    def mentions(user: dict = Depends(current_user)) -> dict:
        rows = brain.store.unread_mentions(user["username"])
        return {
            "mentions": [
                {
                    "channel_id": r["channel_id"],
                    "channel": r["channel"],
                    "message_id": r["message_id"],
                    "author": r["author"],
                    "body": r["body"] or "",
                    "at": r["created_at"],
                }
                for r in rows
            ]
        }

    @app.post("/api/mentions/read")
    def mentions_read(
        body: MentionsRead, user: dict = Depends(current_user)
    ) -> dict:
        cleared = brain.store.mark_mentions_read(user["username"], body.channel_id)
        return {"cleared": cleared}

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
                    # Rotate the channel's agent thread weekly: one
                    # ever-growing checkpointer thread would eventually
                    # exceed the model's context window.
                    week = date.today().strftime("%G-W%V")
                    answer = await state["runtime"].run(
                        f"channel-{channel_id}-{week}",
                        f"[{author} in channel] {text}",
                        sink,
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

    # -- extensions (admin only) -------------------------------------------
    #
    # Saving a plugin or connector runs the author's code as the server user
    # — the same trust level as configuring a stdio MCP server. Admin-only,
    # and the UI says so.

    async def _reload_agent() -> None:
        """Rebuild the registry and the compiled graph so a saved extension
        is live on the next turn without a restart."""
        brain.load_extensions()
        async with agent_lock:
            old = state["runtime"]
            fresh = brain.runtime()
            await fresh.__aenter__()
            state["runtime"] = fresh
            if old is not None:
                await old.__aexit__(None, None, None)

    @app.get("/api/extensions")
    def extensions_list(user: dict = Depends(admin_user)) -> dict:
        payload = extensions.list_all(brain.config, brain.store)
        payload["load_errors"] = brain.registry.load_errors
        payload["mcp_errors"] = getattr(state["runtime"], "mcp_errors", [])
        return payload

    @app.get("/api/extensions/library")
    def extensions_library(user: dict = Depends(admin_user)) -> dict:
        """Ready-made skills, so an empty section offers something to add
        rather than an empty list and a blank editor."""
        installed = {s.name for s in brain.skills}
        existing = {
            c["name"] for c in extensions.list_all(brain.config, brain.store)["connectors"]
        }
        configured = extensions.effective_connectors(brain.config, brain.store)
        return {
            "skills": [
                {**skill, "installed": skill["name"] in installed}
                for skill in librarymod.as_dicts()
            ],
            "connectors": [
                {
                    **conn,
                    # a built-in exists already; "installed" for it means
                    # somebody has actually configured it
                    "installed": (
                        bool(configured.get(conn["name"]))
                        if conn["kind"] == "builtin"
                        else conn["name"] in existing
                    ),
                }
                for conn in librarymod.connectors_as_dicts()
            ],
        }

    @app.post("/api/extensions/library/skill/{name}")
    async def install_library_skill(name: str, user: dict = Depends(admin_user)) -> dict:
        skill = librarymod.get(name)
        if skill is None:
            raise HTTPException(status_code=404, detail="no such library skill")
        extensions.write_skill(
            brain.config, skill.name, skill.description, skill.instructions
        )
        await _reload_agent()
        return {"name": skill.name}

    @app.post("/api/extensions/library/connector/{name}")
    async def install_library_connector(
        name: str, user: dict = Depends(admin_user)
    ) -> dict:
        """Add a ready-made connector: seed a built-in's settings, or write
        a template's starter code into the brain."""
        conn = librarymod.get_connector(name)
        if conn is None:
            raise HTTPException(status_code=404, detail="no such library connector")
        try:
            if conn.kind == "template":
                extensions.write_connector(brain.config, conn.name, conn.code)
            extensions.set_connector_settings(brain.store, conn.name, dict(conn.settings))
        except extensions.ExtensionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await _reload_agent()
        return {"name": conn.name, "kind": conn.kind, "settings": conn.settings}

    @app.get("/api/extensions/scaffold")
    def extensions_scaffold(kind: str, user: dict = Depends(admin_user)) -> dict:
        try:
            return extensions.scaffold(kind)
        except extensions.ExtensionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/extensions/source")
    def extensions_source(kind: str, name: str, user: dict = Depends(admin_user)) -> dict:
        try:
            return extensions.read_source(brain.config, kind, name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="no such extension") from exc
        except extensions.ExtensionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put("/api/extensions/{kind}")
    async def extensions_save(
        kind: str, body: ExtensionSave, user: dict = Depends(admin_user)
    ) -> dict:
        try:
            if kind == "plugin":
                tools = extensions.write_plugin(brain.config, body.name, body.code)
                result = {"name": body.name, "tools": tools}
            elif kind == "connector":
                extensions.write_connector(brain.config, body.name, body.code)
                if body.settings is not None:
                    extensions.set_connector_settings(brain.store, body.name, body.settings)
                result = {"name": body.name}
            elif kind == "skill":
                extensions.write_skill(
                    brain.config, body.name, body.description, body.instructions
                )
                result = {"name": body.name}
            elif kind == "mcp":
                name = extensions.save_mcp_server(brain.config, brain.store, body.spec or {})
                result = {"name": name}
            else:
                raise HTTPException(status_code=404, detail=f"unknown kind {kind!r}")
        except extensions.ExtensionError as exc:
            # a plugin that will not load is a 422 with the loader's words
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await _reload_agent()
        return result

    @app.post("/api/extensions/{kind}/{name}/enabled")
    async def extensions_enabled(
        kind: str, name: str, body: EnabledBody, user: dict = Depends(admin_user)
    ) -> dict:
        try:
            extensions.set_enabled(brain.config, brain.store, kind, name, body.enabled)
        except extensions.ExtensionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await _reload_agent()
        return {"ok": True, "enabled": body.enabled}

    @app.post("/api/extensions/connector/{name}/settings")
    async def extensions_connector_settings(
        name: str, body: SettingsBody, user: dict = Depends(admin_user)
    ) -> dict:
        try:
            extensions.set_connector_settings(brain.store, name, body.settings)
        except extensions.ExtensionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/extensions/connector/{name}/run")
    async def extensions_connector_run(
        name: str, user: dict = Depends(admin_user)
    ) -> dict:
        from cortex.connectors import run_connectors

        settings = extensions.effective_connectors(brain.config, brain.store)
        results = await asyncio.to_thread(
            run_connectors, brain.config, settings, name
        )
        if name not in results:
            raise HTTPException(status_code=404, detail="no such connector, or it is disabled")
        reindex_wanted.set()
        return {"name": name, "result": results[name]}

    @app.delete("/api/extensions/{kind}/{name}")
    async def extensions_delete(
        kind: str, name: str, user: dict = Depends(admin_user)
    ) -> dict:
        try:
            extensions.delete_extension(brain.config, brain.store, kind, name)
        except extensions.ExtensionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await _reload_agent()
        return {"ok": True}

    # -- rules and scheduled jobs (admin only) ------------------------------
    #
    # Rules move someone's writing, so: preview is always available, there
    # is no delete action, and every applied change is logged.

    @app.get("/api/rules")
    def list_rules(user: dict = Depends(admin_user)) -> dict:
        rules = []
        for row in brain.store.list_rules():
            try:
                rules.append(rulesmod.rule_to_dict(rulesmod.parse_rule(json.loads(row["spec"]))))
            except (rulesmod.RuleError, ValueError) as exc:
                rules.append({"name": row["name"], "error": str(exc)})
        return {
            "rules": rules,
            "suggested": rulesmod.suggested_rules(),
            "match_kinds": list(rulesmod.MATCH_KINDS),
            "action_kinds": list(rulesmod.ACTION_KINDS),
        }

    @app.put("/api/rules")
    def save_rule(body: RuleBody, user: dict = Depends(admin_user)) -> dict:
        try:
            rule = rulesmod.parse_rule(body.rule)
        except rulesmod.RuleError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        brain.store.upsert_rule(rule.name, json.dumps(rulesmod.rule_to_dict(rule)))
        return rulesmod.rule_to_dict(rule)

    @app.delete("/api/rules/{name}")
    def delete_rule(name: str, user: dict = Depends(admin_user)) -> dict:
        if not brain.store.delete_rule(name):
            raise HTTPException(status_code=404, detail="no such rule")
        return {"ok": True}

    @app.get("/api/rules/preview")
    async def preview_rules(user: dict = Depends(admin_user)) -> dict:
        """Exactly what a run would do, without doing any of it."""
        planned = await asyncio.to_thread(rulesmod.plan, brain.config, _load_rules())
        return {"planned": [p.as_dict() for p in planned], "count": len(planned)}

    @app.post("/api/rules/apply")
    async def apply_rules(user: dict = Depends(admin_user)) -> dict:
        actions = await asyncio.to_thread(rulesmod.apply, brain.config, _load_rules())
        brain.store.record_rule_actions(actions)
        if actions:
            reindex_wanted.set()
        return {"actions": actions, "count": len(actions)}

    @app.get("/api/rules/history")
    def rules_history(user: dict = Depends(admin_user)) -> dict:
        return {
            "history": [
                {"at": r["ran_at"], "rule": r["rule"], "action": r["action"],
                 "path": r["path"], "target": r["target"]}
                for r in brain.store.rule_history()
            ]
        }

    @app.get("/api/jobs")
    def list_jobs(user: dict = Depends(admin_user)) -> dict:
        return {
            "jobs": [j.as_dict() for j in _load_jobs()],
            "suggested": jobsmod.suggested_jobs(),
            "kinds": list(jobsmod.JOB_KINDS),
            "connectors": sorted(
                extensions.effective_connectors(brain.config, brain.store)
            ),
        }

    @app.put("/api/jobs")
    def save_job(body: JobBody, user: dict = Depends(admin_user)) -> dict:
        try:
            job = jobsmod.parse_job(body.job)
        except jobsmod.JobError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        brain.store.upsert_job(job.name, json.dumps(job.as_dict()), job.enabled)
        return job.as_dict()

    @app.delete("/api/jobs/{name}")
    def delete_job(name: str, user: dict = Depends(admin_user)) -> dict:
        if not brain.store.delete_job(name):
            raise HTTPException(status_code=404, detail="no such job")
        return {"ok": True}

    @app.post("/api/jobs/{name}/run")
    async def run_job_now(name: str, user: dict = Depends(admin_user)) -> dict:
        job = next((j for j in _load_jobs() if j.name == name), None)
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")
        try:
            status, detail = await _run_job(job)
        except Exception as exc:  # noqa: BLE001 - report it, do not 500
            status, detail = "error", str(exc)
        brain.store.record_job_run(name, status, detail)
        return {"name": name, "status": status, "detail": detail}

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

    @app.post("/api/admin/users/{username}/password")
    def user_reset_password(
        username: str, body: PasswordReset, user: dict = Depends(admin_user)
    ) -> dict:
        if brain.store.get_user(username) is None:
            raise HTTPException(status_code=404, detail="no such user")
        if len(body.new_password) < 8:
            raise HTTPException(status_code=422, detail="password must be 8+ characters")
        pw_hash, salt = auth.hash_password(body.new_password)
        brain.store.set_password(username, pw_hash, salt)
        return {"ok": True}

    @app.delete("/api/admin/users/{username}")
    def user_delete(username: str, user: dict = Depends(admin_user)) -> dict:
        if username == user["username"]:
            raise HTTPException(status_code=422, detail="cannot delete yourself")
        if brain.store.get_user(username) is None:
            raise HTTPException(status_code=404, detail="no such user")
        brain.store.delete_user(username)
        # Their vault is deliberately left on disk: deleting an account
        # should not delete someone's writing. Say so, so an admin is not
        # surprised later by a recreated user inheriting old notes.
        vault = brain.config.vaults_dir / username
        return {
            "ok": True,
            "vault_kept": str(vault) if vault.is_dir() else "",
        }

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

    # -- MCP over HTTP ----------------------------------------------------

    # Mounted rather than routed: the session manager speaks raw ASGI and owns
    # its own request/response cycle, including streaming. Wrapping it in a
    # FastAPI route would mean buffering what it is designed not to buffer.
    #
    # Its own Bearer auth, checked inside, because `current_user` is a cookie
    # session and no MCP client has one.
    from cortex.mcp.http import build_asgi as _build_mcp_asgi

    _mcp_app = _build_mcp_asgi(brain)
    if _mcp_app is not None:
        app.mount("/mcp", _mcp_app, name="mcp")

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


def _explain_model_failure(exc: Exception, brain: Brain) -> str:
    """Turn a transport error into something a self-hoster can act on.

    "Connection error." is the least useful sentence we could show: the one
    thing the reader needs is which endpoint failed, and that we know.
    """
    raw = str(exc).strip() or exc.__class__.__name__
    profile = brain.config.provider_for("chat")
    url = profile.base_url if profile else ""
    lowered = raw.lower()
    transport = any(
        s in lowered
        for s in ("connection", "connect", "timeout", "refused", "unreachable", "name or service")
    )
    if transport and url:
        return (
            f"Cannot reach the chat model at {url} — {raw}. "
            "Check that the server is running and that base_url in cortex.yaml is right."
        )
    if "api key" in lowered or "401" in lowered or "unauthorized" in lowered:
        return f"The model endpoint rejected our credentials: {raw}"
    return raw


def _frame(event: AgentEvent) -> str:
    return f"data: {json.dumps({'type': event.type, **event.data}, ensure_ascii=False)}\n\n"
