"""The HTTP API. Every route except /health and the static UI needs the access token (Authorization: Bearer ..., or the cookie that POST /api/login sets).
Uploads are a raw PUT of the file (no multipart), written to a temporary name and renamed only when complete and valid."""
import asyncio, hmac, json, os, re, time
from contextlib import asynccontextmanager

import httpx
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.staticfiles import StaticFiles

from .chat import run_chat
from .host import HostError, ProjectHost
from .projects import NotFound, Projects, is_allowed_upload, is_video, safe_filename

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
COOKIE = "mlt_token"
LOGIN_WINDOW, LOGIN_MAX = 60.0, 8


def err(status, code, message, **extra):
    return JSONResponse({"error": {"code": code, "message": message, **extra}}, status_code=status)


def parse_json_text(text):
    try:
        return json.loads(text)
    except ValueError:
        return {"text": text}


def create_app(settings, model_factory=None):
    projects = Projects(settings.data_dir)
    host = ProjectHost(settings, projects.dir)
    state = {"started": time.time(), "locks": {}, "viewers": {}, "login_fail": {}, "requests": 0, "chat": {}, "model": None}

    def model(pid):
        if model_factory is not None:
            m = model_factory(pid)
            if m is None:
                raise LookupError("no model configured")
            return m
        if state["model"] is None:
            from .model import AnthropicModel
            state["model"] = AnthropicModel(settings.model, cache=settings.cache)
        return state["model"]

    # ------------------------------------------------------------------------------------------------------------------------------ auth
    def authed(request):
        tok = ""
        h = request.headers.get("authorization", "")
        if h.lower().startswith("bearer "):
            tok = h[7:].strip()
        tok = tok or request.cookies.get(COOKIE, "")
        return bool(tok) and hmac.compare_digest(tok.encode(), settings.token.encode())

    def protected(fn):
        async def wrapper(request):
            state["requests"] += 1
            if not authed(request):
                return err(401, "UNAUTHORIZED", "a valid access token is required")
            try:
                return await fn(request)
            except NotFound:
                return err(404, "NOT_FOUND", "no such project")
            except HostError as e:
                return err(503 if e.code in ("TOO_MANY_PROJECTS_OPEN", "ENGINE_START") else 502, e.code, e.message)
        return wrapper

    def pid_of(request):
        pid = request.path_params["pid"]
        if not projects.exists(pid):
            raise NotFound(pid)
        return pid

    # ------------------------------------------------------------------------------------------------------------------------------ routes
    async def health(request):
        return JSONResponse({"ok": True, "engine_processes": len(host.alive())})

    async def login(request):
        ip = request.client.host if request.client else "?"
        now = time.time()
        fails = [t for t in state["login_fail"].get(ip, []) if now - t < LOGIN_WINDOW]
        if len(fails) >= LOGIN_MAX:
            return err(429, "TOO_MANY_ATTEMPTS", "too many wrong tokens; wait a minute")
        try:
            body = await request.json()
        except ValueError:
            body = {}
        tok = str(body.get("token", "")) if isinstance(body, dict) else ""
        if not hmac.compare_digest(tok.encode(), settings.token.encode()):
            state["login_fail"][ip] = fails + [now]
            return err(401, "UNAUTHORIZED", "that is not the access token")
        r = JSONResponse({"ok": True})
        r.set_cookie(COOKIE, settings.token, httponly=True, samesite="strict", path="/", max_age=30 * 86400)
        return r

    @protected
    async def list_projects(request):
        return JSONResponse({"projects": projects.list(), "model": settings.model, "limits": {"max_usd_message": settings.max_usd_message, "max_usd_project": settings.max_usd_project, "max_upload_mb": settings.max_upload_mb}})

    @protected
    async def create_project(request):
        try:
            b = await request.json()
            w, h, fps = int(b.get("width", 1920)), int(b.get("height", 1080)), int(b.get("fps", 25))
        except (ValueError, TypeError, AttributeError):
            return err(400, "INVALID_ARGUMENT", "width, height and fps must be integers")
        if not (64 <= w <= 7680 and 64 <= h <= 7680 and 1 <= fps <= 120):
            return err(400, "INVALID_ARGUMENT", "width and height must be 64-7680 and fps 1-120")
        meta = projects.create(b.get("name", ""), w, h, fps)
        res = await host.call(meta["id"], "new_project", {"width": w, "height": h, "fps": fps})
        if not res["ok"]:
            await host.close(meta["id"])
            projects.delete(meta["id"])
            return err(422, "ENGINE_REFUSED", res["text"][:300])
        return JSONResponse(meta, status_code=201)

    @protected
    async def get_project(request):
        pid = pid_of(request)
        tl = await host.call(pid, "get_timeline")
        src = await host.call(pid, "list_sources")
        return JSONResponse({**projects.meta(pid), "sources": parse_json_text(src["text"]) if src["ok"] else {}, "usage": projects.usage(pid), "uploads": projects.uploads(pid), "exports": projects.exports(pid),
                             "timeline": parse_json_text(tl["text"]) if tl["ok"] else {"error": tl["text"]}, "chat": projects.load_chat(pid)})

    @protected
    async def delete_project(request):
        pid = pid_of(request)
        await host.close(pid)
        projects.delete(pid)
        return JSONResponse({"ok": True})

    @protected
    async def tool_action(request):
        """undo / redo: the engine's own tools, nothing else is exposed this way."""
        pid = pid_of(request)
        name = request.path_params["action"]
        if name not in ("undo", "redo"):
            return err(404, "NOT_FOUND", "no such action")
        res = await host.call(pid, name)
        return JSONResponse({"ok": res["ok"], "result": parse_json_text(res["text"])}, status_code=200 if res["ok"] else 422)

    @protected
    async def upload(request):
        pid = pid_of(request)
        raw = request.path_params["filename"]
        name = safe_filename(raw)
        if not name or not is_allowed_upload(name):
            return err(415, "UNSUPPORTED_FILE", f"'{raw}' is not a file type this editor accepts (video, audio, images, .srt)")
        cap = settings.max_upload_mb * 1_000_000
        try:
            declared = int(request.headers.get("content-length", "0"))
        except ValueError:
            declared = 0
        if declared > cap:
            return err(413, "TOO_LARGE", f"the file is {declared / 1e6:.0f} MB; the limit is {settings.max_upload_mb} MB")
        updir = os.path.join(projects.dir(pid), "uploads")
        stem, ext = os.path.splitext(name)
        final, n = name, 1
        while os.path.exists(os.path.join(updir, final)):
            n += 1
            final = f"{stem}_{n}{ext}"
        tmp, dest = os.path.join(updir, final + ".part"), os.path.join(updir, final)
        size = 0
        try:
            with open(tmp, "wb") as f:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > cap:
                        raise HTTPException(413, f"the file is over the {settings.max_upload_mb} MB limit")
                    f.write(chunk)
            if size == 0:
                return err(400, "EMPTY", "the file is empty")
            os.replace(tmp, dest)
        except HTTPException as e:
            return err(e.status_code, "TOO_LARGE", e.detail)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        out = {"name": final, "size": size}
        if is_video(final):
            base = re.sub(r"[^A-Za-z0-9_]", "_", os.path.splitext(final)[0])[:28] or "clip"
            sid, res = base, None
            for k in range(1, 6):
                res = await host.call(pid, "import_clip", {"path": dest, "id": sid})
                if res["ok"] or "already exists" not in res["text"]:
                    break
                sid = f"{base}_{k + 1}"
            if not res["ok"]:
                os.remove(dest)                                              # nothing hostile or unreadable stays in the project
                code = next((c for c in ("LIMIT_EXCEEDED", "PATH_NOT_ALLOWED", "INVALID_ARGUMENT") if c in res["text"]), "INVALID_FILE")
                return err(422, code, res["text"].splitlines()[0][:300] if res["text"] else "the file could not be read as a video")
            out.update(source_id=sid, info=parse_json_text(res["text"]))
        return JSONResponse(out, status_code=201)

    @protected
    async def start_export(request):
        pid = pid_of(request)
        try:
            b = await request.json()
        except ValueError:
            b = {}
        quality = b.get("quality", "draft") if isinstance(b, dict) else "draft"
        if quality not in ("draft", "high"):
            return err(400, "INVALID_ARGUMENT", "quality must be draft or high")
        name = safe_filename(b.get("name", "")) if isinstance(b, dict) else ""
        name = (os.path.splitext(name)[0] or f"export_{int(time.time())}") + ".mp4"
        res = await host.call(pid, "export", {"output_path": os.path.join(projects.dir(pid), "exports", name), "quality": quality, "overwrite": True, "background": True})
        return JSONResponse({"ok": res["ok"], "name": name, "result": parse_json_text(res["text"])}, status_code=202 if res["ok"] else 422)

    @protected
    async def job_status(request):
        pid = pid_of(request)
        res = await host.call(pid, "job_status", {"job_id": request.path_params["jid"], "wait_s": 0})
        return JSONResponse({"ok": res["ok"], "result": parse_json_text(res["text"])}, status_code=200 if res["ok"] else 404)

    @protected
    async def download(request):
        pid = pid_of(request)
        name = request.path_params["name"]
        if name not in projects.exports(pid):                                 # only what the listing shows: the name is never joined to a path unchecked
            return err(404, "NOT_FOUND", "no such export")
        return FileResponse(os.path.join(projects.dir(pid), "exports", name), filename=name, media_type="video/mp4")

    @protected
    async def open_viewer(request):
        pid = pid_of(request)
        res = await host.call(pid, "open_viewer")
        if not res["ok"]:
            return err(422, "VIEWER", res["text"][:300])
        m = re.match(r"http://127\.0\.0\.1:(\d+)/([0-9a-f]+)/", parse_json_text(res["text"]).get("url", ""))
        if not m:
            return err(502, "VIEWER", "the engine did not return a viewer address")
        state["viewers"] = {t: v for t, v in state["viewers"].items() if v["pid"] != pid}
        state["viewers"][m.group(2)] = {"pid": pid, "port": int(m.group(1))}
        return JSONResponse({"url": f"/{m.group(2)}/"})

    async def viewer_proxy(request):
        state["requests"] += 1
        v = state["viewers"].get(request.path_params["token"])
        if v is None:
            raise HTTPException(404)
        if not authed(request):
            return err(401, "UNAUTHORIZED", "a valid access token is required")
        url = f"http://127.0.0.1:{v['port']}/{request.path_params['token']}/{request.path_params['rest']}"
        if request.url.query:
            url += "?" + request.url.query
        client = httpx.AsyncClient(timeout=30)
        try:
            r = await client.send(client.build_request("GET", url, headers={k: request.headers[k] for k in ("range", "if-none-match") if k in request.headers}), stream=True)
        except httpx.HTTPError:
            await client.aclose()
            return err(502, "VIEWER_DOWN", "the viewer is not running (the project's engine was stopped): open it again")
        keep = {k: r.headers[k] for k in ("content-type", "content-length", "content-range", "accept-ranges", "cache-control", "etag") if k in r.headers}

        async def body():
            try:
                async for c in r.aiter_raw():
                    yield c
            finally:
                await r.aclose()
                await client.aclose()
        return StreamingResponse(body(), status_code=r.status_code, headers=keep)

    @protected
    async def chat(request):
        pid = pid_of(request)
        try:
            b = await request.json()
            text = str(b.get("message", "")).strip()
        except (ValueError, AttributeError):
            return err(400, "INVALID_ARGUMENT", "send {\"message\": \"...\"}")
        if not text or len(text) > 8000:
            return err(400, "INVALID_ARGUMENT", "the message must be 1-8000 characters")
        try:
            m = model(pid)
        except Exception as e:                                               # noqa: BLE001  (the SDK refuses to build a client without credentials)
            return err(503, "MODEL_NOT_CONFIGURED", f"no model credentials: set ANTHROPIC_API_KEY ({type(e).__name__})")
        lock = state["locks"].setdefault(pid, asyncio.Lock())
        if lock.locked():
            return err(409, "BUSY", "this project is already answering a message")

        async def stream():
            async with lock:
                try:
                    async for ev in run_chat(host, projects, m, settings.model, settings, pid, text, state["chat"]):
                        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except HostError as e:
                    yield f"data: {json.dumps({'type': 'error', 'code': e.code, 'message': e.message})}\n\n"
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @protected
    async def metrics(request):
        total = sum(p["usd"] for p in projects.list())
        return JSONResponse({"uptime_s": round(time.time() - state["started"]), "requests": state["requests"], "projects": len(projects.list()),
                             "engine_processes": len(host.alive()), "engine_processes_started": host.started, "chat": state["chat"], "usd_total": round(total, 4), "model": settings.model})

    SECURITY_HEADERS = {   # the page loads nothing from elsewhere; inline styles are set from script (CSSOM), never from markup
        "Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; frame-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'; form-action 'self'",
        "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-cache"}

    async def index(request):
        f = os.path.join(STATIC, "index.html")
        return FileResponse(f, headers=SECURITY_HEADERS) if os.path.exists(f) else Response("the web UI is not installed", status_code=404)

    routes = [
        Route("/health", health), Route("/api/login", login, methods=["POST"]), Route("/api/metrics", metrics),
        Route("/api/projects", list_projects, methods=["GET"]), Route("/api/projects", create_project, methods=["POST"]),
        Route("/api/projects/{pid}", get_project, methods=["GET"]), Route("/api/projects/{pid}", delete_project, methods=["DELETE"]),
        Route("/api/projects/{pid}/chat", chat, methods=["POST"]),
        Route("/api/projects/{pid}/uploads/{filename}", upload, methods=["PUT"]),
        Route("/api/projects/{pid}/export", start_export, methods=["POST"]), Route("/api/projects/{pid}/jobs/{jid}", job_status, methods=["GET"]),
        Route("/api/projects/{pid}/exports/{name}", download, methods=["GET"]),
        Route("/api/projects/{pid}/viewer", open_viewer, methods=["POST"]),
        Route("/api/projects/{pid}/{action}", tool_action, methods=["POST"]),
        Route("/", index),
    ]
    if os.path.isdir(os.path.join(STATIC, "assets")):
        from starlette.routing import Mount
        routes.append(Mount("/assets", StaticFiles(directory=os.path.join(STATIC, "assets")), name="assets"))
    routes.append(Route("/{token}/{rest:path}", viewer_proxy, methods=["GET"]))        # last: the engine viewer's own addresses (/<random token>/...)

    @asynccontextmanager
    async def lifespan(_app):
        host.start_reaper()
        try:
            yield
        finally:
            await host.close_all()

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.settings, app.state.projects, app.state.host, app.state.s = settings, projects, host, state
    return app
