#!/usr/bin/env python3
"""The web application end to end with a scripted model (no API key needed): a real uvicorn server, a real editor-engine process per project, real tools.
Auth, projects, uploads (valid and hostile), chat with tool calls, spending/turn caps, isolation between projects, idle shutdown and restart, process cap, export + download,
the live viewer through the proxy. What a REAL model does with these tools is measured elsewhere (tools/validation). Run: python3 test_app.py"""
import json, os, shutil, socket, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["MLT_LOG"] = "off"
os.environ.pop("ANTHROPIC_API_KEY", None)
import httpx, uvicorn  # noqa: E402
from app.api import create_app  # noqa: E402
from app.config import AppSettings  # noqa: E402
from app.model import ScriptedModel  # noqa: E402

A, B = os.path.join(HERE, "media", "clip_a.mp4"), os.path.join(HERE, "media", "clip_b.mp4")
TOKEN = "test-token-123"
ok = bad = 0


def check(name, cond, detail=""):
    global ok, bad
    ok += bool(cond); bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:400]}]" if not cond and detail else ""))


class Server:
    def __init__(self, **kw):
        self.tmp = tempfile.mkdtemp(prefix="app_test_")
        self.holder = {"model": None, "by_project": {}}
        self.settings = AppSettings(data_dir=self.tmp, token=TOKEN, **kw)
        self.app = create_app(self.settings, model_factory=lambda pid: self.holder["by_project"].get(pid) or self.holder["model"])
        s = socket.socket(); s.bind(("127.0.0.1", 0)); self.port = s.getsockname()[1]; s.close()
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error", access_log=False))
        self.server.install_signal_handlers = lambda: None
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(100):
            try:
                httpx.get(self.url("/health"), timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        self.http = httpx.Client(base_url=self.url(""), headers={"Authorization": f"Bearer {TOKEN}"}, timeout=120)

    def url(self, p):
        return f"http://127.0.0.1:{self.port}{p}"

    def stop(self):
        self.http.close()
        self.server.should_exit = True
        self.thread.join(timeout=30)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def chat(self, pid, text, model):
        self.holder["model"] = model
        events = []
        with self.http.stream("POST", f"/api/projects/{pid}/chat", json={"message": text}) as r:
            if r.status_code != 200:
                return r.status_code, json.loads(r.read())
            for line in r.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
        return 200, events

    def new_project(self, name="t", w=640, h=360, fps=25):
        r = self.http.post("/api/projects", json={"name": name, "width": w, "height": h, "fps": fps})
        return r.json()["id"]

    def put(self, pid, name, data):
        return self.http.put(f"/api/projects/{pid}/uploads/{name}", content=data)


S = Server(max_upload_mb=1)
try:
    # ------------------------------------------------------------------------------------------------------------------------ auth
    check("/health needs no token", httpx.get(S.url("/health")).status_code == 200)
    check("the API refuses a request with no token", httpx.get(S.url("/api/projects")).status_code == 401)
    check("...and one with a wrong token", httpx.get(S.url("/api/projects"), headers={"Authorization": "Bearer nope"}).status_code == 401)
    check("a bearer token works", S.http.get("/api/projects").status_code == 200)
    bad_login = httpx.post(S.url("/api/login"), json={"token": "wrong"})
    good_login = httpx.post(S.url("/api/login"), json={"token": TOKEN})
    check("login refuses a wrong token and accepts the right one with an HttpOnly cookie", bad_login.status_code == 401 and good_login.status_code == 200
          and "httponly" in good_login.headers.get("set-cookie", "").lower() and "samesite=strict" in good_login.headers.get("set-cookie", "").lower(), good_login.headers)
    check("the cookie works as credentials", httpx.get(S.url("/api/projects"), cookies={"mlt_token": TOKEN}).status_code == 200)
    fails = [httpx.post(S.url("/api/login"), json={"token": "x"}).status_code for _ in range(10)]
    check("repeated wrong tokens are slowed down (429)", 429 in fails, fails)

    # ------------------------------------------------------------------------------------------------------------------------ projects
    pid = S.new_project("first")
    check("a project is created with an id of the right shape", pid.startswith("p_") and len(pid) == 10, pid)
    check("it is listed", any(p["id"] == pid for p in S.http.get("/api/projects").json()["projects"]))
    check("silly sizes are refused", S.http.post("/api/projects", json={"width": 5, "height": 5, "fps": 25}).status_code == 400)
    for bad_id in ("..%2f..%2fetc", "p_zzzzzzzz", "p_1234567", "%2e%2e"):
        check(f"a project id like {bad_id!r} is a 404, never a path", S.http.get(f"/api/projects/{bad_id}").status_code == 404)

    # ------------------------------------------------------------------------------------------------------------------------ uploads
    r = S.put(pid, "viaje.mp4", open(B, "rb").read())
    sid = r.json().get("source_id")
    check("a valid video is stored and imported as a source", r.status_code == 201 and sid == "viaje" and r.json()["info"].get("duration_s"), r.text[:300])
    r = S.put(pid, "viaje.mp4", open(B, "rb").read())
    check("the same name again does not overwrite: it is renamed and imported under another id", r.status_code == 201 and r.json()["name"] == "viaje_2.mp4" and r.json()["source_id"] == "viaje_2", r.text[:300])
    r = S.put(pid, "big.mp4", open(A, "rb").read())
    check("a file over the upload limit is refused (413) and nothing is left", r.status_code == 413 and "big.mp4" not in S.http.get(f"/api/projects/{pid}").json()["uploads"], r.text[:200])
    r = S.put(pid, "corrupt.mp4", os.urandom(4000))
    check("random bytes named .mp4 are refused (422) and removed", r.status_code == 422 and "corrupt.mp4" not in os.listdir(os.path.join(S.tmp, "projects", pid, "uploads")), r.text[:200])
    check("a disallowed type is refused (415)", S.put(pid, "run.exe", b"MZ....").status_code == 415)
    check("an empty file is refused (400)", S.put(pid, "empty.mp4", b"").status_code == 400)
    from app.projects import safe_filename
    check("safe_filename turns paths and odd names into a harmless base name", safe_filename("../../evil.mp4") == "evil.mp4" and safe_filename("..\\..\\x y.MP4") == "x y.mp4"
          and safe_filename("/etc/passwd") == "passwd" and safe_filename("..") == "" and safe_filename(".hidden.mp4") == "hidden.mp4" and len(safe_filename("a" * 500 + ".mp4")) <= 90, safe_filename("..\\..\\x y.MP4"))
    r1 = S.put(pid, "..%2F..%2F..%2Fevil.mp4", open(B, "rb").read())
    r2 = S.put(pid, "..%5C..%5C..%5Cevil2.mp4", open(B, "rb").read())
    pdir = os.path.join(S.tmp, "projects", pid)
    inside = set(os.listdir(os.path.join(pdir, "uploads")))
    check("a name with ../ is never stored (the router refuses it), one with backslashes is flattened into the uploads folder, and nothing is written outside it",
          r1.status_code in (404, 400, 405) and r2.status_code == 201 and "evil2.mp4" in inside and not any(os.path.exists(os.path.join(base, n)) for base in (S.tmp, os.path.join(S.tmp, "projects"), pdir) for n in ("evil.mp4", "evil2.mp4")),
          (r1.status_code, r2.status_code, sorted(inside)))
    info = S.http.get(f"/api/projects/{pid}").json()
    check("the project reports its imported sources (the UI needs them) with their durations", {"viaje", "viaje_2"} <= set(info["sources"]) and info["sources"]["viaje"]["duration_s"] > 0, str(info["sources"])[:200])

    # ------------------------------------------------------------------------------------------------------------------------ chat
    model = ScriptedModel([
        [{"type": "text", "text": "I will cut the first three seconds and add a title."},
         {"type": "tool_use", "name": "add_clip", "input": {"source": "viaje", "start_s": 0, "end_s": 3}}],
        [{"type": "tool_use", "name": "add_text", "input": {"text": "Hola mundo", "start_s": 0.5, "dur_s": 1.5}}],
        [{"type": "tool_use", "name": "get_still", "input": {"time_s": 1.0}}],
        [{"type": "text", "text": "Done: a 3 s clip with the title."}],
    ])
    status, ev = S.chat(pid, "make a short intro", model)
    kinds = [e["type"] for e in ev]
    check("a chat turn streams text, tool calls, results, usage and a final done", status == 200 and {"text_delta", "tool_call", "tool_result", "usage", "project_changed", "done"} <= set(kinds) and kinds[-1] == "done", kinds)
    tr = [e for e in ev if e["type"] == "tool_result"]
    check("the model's tool calls really ran in the engine (all ok)", len(tr) == 3 and all(e["ok"] for e in tr), [(e["name"], e["ok"], e["text"][:80]) for e in tr])
    check("a still comes back to the UI as an image thumbnail", any(e["images"] and e["images"][0].startswith("data:image/png;base64,") for e in tr))
    check("the edit is in the project (a clip of 3 s and the text)", "Hola mundo" in json.dumps(S.http.get(f"/api/projects/{pid}").json()["timeline"]))
    check("the model was given the editor's tools and the uploaded file paths", len(model.calls[0]["tools"]) >= 40 and "viaje.mp4" in model.calls[0]["system"] and "Editor engine instructions" in model.calls[0]["system"], (len(model.calls[0]["tools"]), model.calls[0]["system"][:200]))
    last_user = model.calls[3]["messages"][-1]
    check("the still the model asked for reached it as an image block", any(b.get("type") == "tool_result" and any(c.get("type") == "image" for c in b["content"]) for b in model.calls[3]["messages"][-1]["content"]) if last_user["role"] == "user" else False, str(last_user)[:200])
    done = ev[-1]
    check("cost is accounted (4 steps x the scripted usage) and shown", done["turns"] == 4 and done["message_usd"] > 0 and done["project_usd"] >= done["message_usd"], done)
    saved = S.http.get(f"/api/projects/{pid}").json()["chat"]
    check("the conversation is saved, with the old image replaced by nothing heavy", len(saved) >= 7 and "base64" not in json.dumps(saved), len(saved))
    model2 = ScriptedModel([[{"type": "text", "text": "ok"}]])
    S.chat(pid, "and now?", model2)
    check("the next message continues the same conversation", len(model2.calls[0]["messages"]) >= 8 and "make a short intro" in json.dumps(model2.calls[0]["messages"]), len(model2.calls[0]["messages"]))

    # ------------------------------------------------------------------------------------------------------------------------ caps and containment
    pid2 = S.new_project("caps")
    S.settings.max_turns = 3
    loop = ScriptedModel([[{"type": "tool_use", "name": "list_sources", "input": {}}]] * 10)
    _, ev = S.chat(pid2, "loop forever", loop)
    check("the turn cap stops a model that keeps calling tools", any(e.get("code") == "MAX_TURNS" for e in ev) and ev[-1]["turns"] == 3, [e.get("code") for e in ev if e["type"] == "error"])
    S.settings.max_turns = 30
    S.settings.max_usd_message = 0.001
    pricey = ScriptedModel([[{"type": "tool_use", "name": "list_sources", "input": {}}]] * 5, usage={"input_tokens": 100000, "output_tokens": 5000})
    _, ev = S.chat(pid2, "spend", pricey)
    check("the per-message spending cap stops the loop", any(e.get("code") == "BUDGET_MESSAGE" for e in ev), [e.get("code") for e in ev if e["type"] == "error"])
    S.settings.max_usd_message = 1.0
    S.settings.max_usd_project = 0.0005
    _, ev = S.chat(pid2, "more", ScriptedModel([[{"type": "text", "text": "hi"}]]))
    check("the per-project cap refuses further messages", any(e.get("code") == "BUDGET_PROJECT" for e in ev), [e.get("code") for e in ev if e["type"] == "error"])
    S.settings.max_usd_project = 10.0
    other_file = os.path.join(S.tmp, "projects", pid, "uploads", "viaje.mp4")
    attack = ScriptedModel([[{"type": "tool_use", "name": "import_clip", "input": {"path": "/etc/passwd", "id": "x"}}, {"type": "tool_use", "name": "import_clip", "input": {"path": other_file, "id": "y"}}],
                            [{"type": "text", "text": "tried"}]])
    _, ev = S.chat(pid2, "read other files", attack)
    res = [e for e in ev if e["type"] == "tool_result"]
    check("a model that asks for /etc/passwd or ANOTHER project's file is refused by the engine (PATH_NOT_ALLOWED)", len(res) == 2 and all(not e["ok"] and "PATH_NOT_ALLOWED" in e["text"] for e in res), [(e["ok"], e["text"][:100]) for e in res])
    check("the engine process of a project does not receive the application's secrets and may only touch its own folder",
          (lambda env: "MLT_APP_TOKEN" not in env and not any(k.startswith("ANTHROPIC_") for k in env) and env["MLT_EDITOR_ROOTS"] == env["MLT_EDITOR_HOME"])(
              (os.environ.update(ANTHROPIC_API_KEY="sk-secret", MLT_APP_TOKEN="t"), S.settings.engine_env("/data/x"))[1]))
    os.environ.pop("ANTHROPIC_API_KEY"); os.environ.pop("MLT_APP_TOKEN")
    S.holder["model"] = None
    r = S.http.post(f"/api/projects/{pid2}/chat", json={"message": "hi"})
    check("with no model configured chat answers 503 MODEL_NOT_CONFIGURED instead of failing obscurely", r.status_code == 503 and r.json()["error"]["code"] == "MODEL_NOT_CONFIGURED", r.text[:200])

    import asyncio
    from app.model import AnthropicModel, ModelError

    async def no_key():
        saved = {k: os.environ.pop(k, None) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")}
        try:
            async for _ in AnthropicModel("claude-sonnet-5-5").step(system="s", messages=[{"role": "user", "content": "hi"}], tools=[]):
                pass
        except ModelError as e:
            return e.code
        except Exception as e:                       # noqa: BLE001
            return f"RAW:{type(e).__name__}"
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
        return "NO ERROR"
    check("the real model client without any credentials fails with a clear AUTH error (no network involved), not a raw exception", asyncio.run(no_key()) == "AUTH")

    # ------------------------------------------------------------------------------------------------------------------------ two projects at once
    pa, pb = S.new_project("A"), S.new_project("B")
    for p in (pa, pb):
        S.put(p, "c.mp4", open(B, "rb").read())
    def work(p, label):
        S.holder["by_project"][p] = ScriptedModel([[{"type": "tool_use", "name": "add_clip", "input": {"source": "c", "start_s": 0, "end_s": 2}}],
                                                   [{"type": "tool_use", "name": "add_text", "input": {"text": label, "start_s": 0.2, "dur_s": 1}}], [{"type": "text", "text": "ok"}]], delay=0.01)
        sess = httpx.Client(base_url=S.url(""), headers={"Authorization": f"Bearer {TOKEN}"}, timeout=120)
        with sess.stream("POST", f"/api/projects/{p}/chat", json={"message": label}) as r:
            for _ in r.iter_lines():
                pass
    ts = [threading.Thread(target=work, args=(pa, "ALPHA")), threading.Thread(target=work, args=(pb, "BRAVO"))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    ta, tb = json.dumps(S.http.get(f"/api/projects/{pa}").json()["timeline"]), json.dumps(S.http.get(f"/api/projects/{pb}").json()["timeline"])
    check("two projects edited at the same time each get exactly their own edit and nothing of the other", "ALPHA" in ta and "BRAVO" not in ta and "BRAVO" in tb and "ALPHA" not in tb, (ta[:150], tb[:150]))
    check("they ran in two separate engine processes", S.http.get("/health").json()["engine_processes"] >= 2, S.http.get("/health").json())
    slow = ScriptedModel([[{"type": "text", "text": "x" * 240}]], delay=0.2)
    S.holder["by_project"][pa] = slow
    def slow_chat():
        with S.http.stream("POST", f"/api/projects/{pa}/chat", json={"message": "slow"}) as resp:
            for _ in resp.iter_lines():
                pass
    t_slow = threading.Thread(target=slow_chat)
    t_slow.start()
    time.sleep(1.5)
    r = S.http.post(f"/api/projects/{pa}/chat", json={"message": "second"})
    check("a second message to a project that is still answering is refused (409 BUSY)", r.status_code == 409 and r.json()["error"]["code"] == "BUSY", r.text[:200])
    t_slow.join()

    # ------------------------------------------------------------------------------------------------------------------------ export, download, undo, viewer
    r = S.http.post(f"/api/projects/{pid}/export", json={"quality": "draft", "name": "mi video"})
    check("an export starts as a job", r.status_code == 202 and "job_id" in json.dumps(r.json()), r.text[:300])
    jid = r.json()["result"]["job_id"]
    for _ in range(120):
        j = S.http.get(f"/api/projects/{pid}/jobs/{jid}").json()["result"]
        if j["state"] != "running":
            break
        time.sleep(0.5)
    check("the job finishes", j["state"] == "done", j)
    name = r.json()["name"]
    d = S.http.get(f"/api/projects/{pid}/exports/{name}")
    check("the export downloads as an mp4", d.status_code == 200 and d.content[4:8] == b"ftyp" and len(d.content) > 5000, (d.status_code, len(d.content)))
    for bad_name in ("..%2F..%2Fmeta.json", "nope.mp4", "%2e%2e%2fproject.json"):
        check(f"a download name like {bad_name!r} is a 404", S.http.get(f"/api/projects/{pid}/exports/{bad_name}").status_code == 404)
    u = S.http.post(f"/api/projects/{pid}/undo")
    check("undo goes through the engine's own tool", u.status_code == 200 and u.json()["ok"], u.text[:200])
    check("only undo and redo are reachable as actions", S.http.post(f"/api/projects/{pid}/export_all").status_code == 404 and S.http.post(f"/api/projects/{pid}/new_project").status_code == 404)
    v = S.http.post(f"/api/projects/{pid}/viewer")
    vurl = v.json().get("url", "")
    page = S.http.get(vurl)
    check("the live viewer opens through the proxy (HTML with the player)", v.status_code == 200 and page.status_code == 200 and "<video" in page.text.lower(), (v.text[:100], page.status_code))
    check("...and refuses a request without the token", httpx.get(S.url(vurl)).status_code == 401)
    check("an unknown viewer token is a 404", S.http.get("/0123456789abcdef/").status_code == 404)

    m = S.http.get("/api/metrics").json()
    check("metrics report requests, engine processes, tool calls and cost", m["requests"] > 20 and m["engine_processes"] >= 1 and m["chat"].get("tool_calls", {}).get("add_clip", 0) >= 1 and m["usd_total"] > 0, m)
    check("deleting a project removes its folder and stops its engine", S.http.delete(f"/api/projects/{pid2}").status_code == 200 and not os.path.exists(os.path.join(S.tmp, "projects", pid2)))
finally:
    S.stop()

# ---------------------------------------------------------------------------------------------------------------------------- idle shutdown and the process cap (own servers)
S2 = Server(idle_s=2.0, max_procs=1)
try:
    p1, p2 = S2.new_project("one"), S2.new_project("two")
    S2.put(p1, "c.mp4", open(B, "rb").read())
    live = S2.http.get("/health").json()["engine_processes"]
    check("with a cap of one process, working on a second project stops the first one (LRU) instead of failing", live == 1, live)
    rev_before = S2.http.get(f"/api/projects/{p1}").json()
    time.sleep(6)
    check("an idle engine process is stopped by the reaper", S2.http.get("/health").json()["engine_processes"] == 0)
    again = S2.http.get(f"/api/projects/{p1}").json()
    check("...and the project is intact when its process restarts on the next call", "c" in json.dumps(again["timeline"]) and again["uploads"] == rev_before["uploads"])
finally:
    S2.stop()

print(f"\n{ok} passed, {bad} failed")
sys.exit(1 if bad else 0)
