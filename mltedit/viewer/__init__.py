"""The live viewer: a local web page that plays the current edit and follows it as the agent edits.

* an HTTP server on 127.0.0.1 (any free port; every URL starts with a random token) in a thread of the MCP server process
* the project is cut into segments (preview/segments.py); a playlist (HLS) lists them all from the start and each segment is rendered WHEN FIRST ASKED
  FOR by a separate worker process (preview/worker.py), the ones after it are rendered ahead; a segment is a file named by its hash, so an edit
  re-renders only the segments whose hash changed
* the page long-polls /plan.json: when the project's revision changes it reloads the playlist at the same playback time"""
import heapq, http.server, itertools, json, os, secrets, socketserver, subprocess, sys, threading, time
import urllib.parse

from .. import engine as live
from .. import project as P
from ..config import S

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))     # the folder with `mltedit/` (the worker is run from there)
_SINGLETON = {"v": None}


class Renderer:
    """One worker process, one job at a time, nearest-to-the-playhead first. Single-flight per piece."""

    COUNTERS = {"segment": "segments_rendered", "audio": "audio_rendered", "plan": "plans"}

    def __init__(self, home):
        self.home, self.dir = home, os.path.join(home, "viewer")
        os.makedirs(os.path.join(self.dir, "seg"), exist_ok=True)
        self.q, self.cv, self.n = [], threading.Condition(), itertools.count()
        self.inflight, self.errors = {}, {}
        self.stats = {"segments_rendered": 0, "audio_rendered": 0, "plans": 0, "worker_starts": 0, "segment_seconds": []}
        self.proc = None
        self._ids = itertools.count(1)
        threading.Thread(target=self._loop, daemon=True, name="viewer-render").start()

    # ---- worker process
    def _start(self):
        self.stats["worker_starts"] += 1
        self.proc = subprocess.Popen([sys.executable, "-m", "mltedit.preview.worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
                                     cwd=PKG_ROOT, env={**os.environ, "MLT_EDITOR_HOME": self.home})

    def _call(self, job):
        for attempt in (1, 2):
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            try:
                job["id"] = next(self._ids)
                self.proc.stdin.write(json.dumps(job) + "\n"); self.proc.stdin.flush()
                line = self.proc.stdout.readline()
                if not line:
                    raise BrokenPipeError("worker died")
                return json.loads(line)
            except (BrokenPipeError, OSError, ValueError):
                self.proc = None
                if attempt == 2:
                    return {"ok": False, "error": "the preview worker died twice"}
        return {"ok": False, "error": "unreachable"}

    def _loop(self):
        while True:
            with self.cv:
                while not self.q:
                    self.cv.wait()
                _, _, key, job, ev = heapq.heappop(self.q)
            try:
                r = self._call(job)
            except Exception as e:  # noqa: BLE001
                r = {"ok": False, "error": str(e)}
            if r.get("ok"):
                self.stats[self.COUNTERS[job["job"]]] += 1
                if job["job"] == "segment":
                    self.stats["segment_seconds"].append(r["seconds"])
                ev.result = r.get("result")
            else:
                self.errors[key] = r.get("error", "failed")
                ev.result = None
            ev.ok = bool(r.get("ok"))
            ev.set()
            with self.cv:
                self.inflight.pop(key, None)

    def submit(self, key, job, priority):
        """Queue a job once (single-flight on `key`); returns the Event that is set when it is done (ev.ok, ev.result)."""
        with self.cv:
            if key in self.inflight:
                ev = self.inflight[key]
                return ev
            ev = threading.Event(); ev.ok = False; ev.result = None
            self.inflight[key] = ev
            self.errors.pop(key, None)
            heapq.heappush(self.q, (priority, next(self.n), key, job, ev))
            self.cv.notify()
            return ev

    # ---- pieces
    def plan(self, st):
        key = "plan|" + P.layout_hash(st) + f"|{st.get('revision', 0)}"
        ev = self.submit(key, {"job": "plan", "st": st, "home": self.home}, -1)
        ev.wait(120)
        return ev.result if ev.ok else None

    def seg_path(self, h):
        return os.path.join(self.dir, "seg", h + ".ts")

    def segment(self, h, st, a, b, priority=0, wait=True):
        path = self.seg_path(h)
        if os.path.exists(path):
            return path
        ev = self.submit("seg|" + h, {"job": "segment", "st": st, "home": self.home, "a": a, "b": b, "out": path}, priority)
        if not wait:
            return None
        ev.wait(300)
        return path if ev.ok and os.path.exists(path) else None

    def audio_dir(self, h):
        return os.path.join(self.dir, "audio", h)

    def audio(self, h, st):
        d = self.audio_dir(h)
        if os.path.exists(os.path.join(d, "audio.m3u8")):
            return d
        ev = self.submit("aud|" + h, {"job": "audio", "st": st, "home": self.home, "out_dir": d + ".tmp"}, 0)
        ev.wait(600)
        if ev.ok and not os.path.exists(d):
            os.replace(d + ".tmp", d)
        return d if os.path.exists(os.path.join(d, "audio.m3u8")) else None

    def prune(self, max_mb):
        """Keep the viewer's files under max_mb (oldest first); they are rebuilt on demand."""
        files = []
        for dp, _, fn in os.walk(self.dir):
            for f in fn:
                p = os.path.join(dp, f)
                try:
                    s = os.stat(p); files.append((s.st_mtime, s.st_size, p))
                except OSError:
                    pass
        files.sort()
        total = sum(f[1] for f in files)
        for _, size, p in files:
            if total <= max_mb * 1_000_000:
                break
            try:
                os.remove(p); total -= size
            except OSError:
                pass


class Viewer:
    def __init__(self, home, port=0):
        self.home, self.token = home, secrets.token_hex(8)
        self.renderer = Renderer(home)
        self.renderer.prune(1500)
        self.plans, self.seg_index = {}, {}
        self.lock = threading.Lock()
        viewer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                try:
                    viewer.handle(self)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as e:  # noqa: BLE001
                    try:
                        viewer.send(self, 500, f"error: {e}".encode(), "text/plain")
                    except OSError:
                        pass

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
            allow_reuse_address = True

            def handle_error(self, request, client_address):             # a player that stops reading (seek, tab closed) is not an error
                if not isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError)):
                    super().handle_error(request, client_address)

        self.httpd = Server(("127.0.0.1", port or S.viewer_port), Handler)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/{self.token}/"
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="viewer-http").start()

    # ---- helpers
    def send(self, h, code, body, ctype, cache="no-store"):
        h.send_response(code)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Cache-Control", cache)
        h.end_headers()
        h.wfile.write(body)

    def load_state(self):
        path = os.path.join(self.home, "project.json")
        try:
            with open(path) as f:
                return P.migrate(json.load(f))
        except (OSError, ValueError):
            return None

    def current_plan(self):
        """(plan dict, svg, project state) for the project as it is now; plans are cached by revision."""
        st = self.load_state()
        if st is None or not st["ops"]:
            return None, None, st
        key = st.get("revision", 0)
        with self.lock:
            hit = self.plans.get(("rev", key))
        if hit:
            return hit["plan"], hit, st
        res = self.renderer.plan(st)
        if res is None:
            return None, None, st
        entry = {"plan": res["plan"], "svg": res["svg"], "warnings": res["warnings"], "duration": res["duration"], "st": st}
        with self.lock:
            self.plans[("rev", key)] = entry
            self.plans[("hash", res["plan"]["hash"])] = entry
            for s in res["plan"]["segs"]:
                self.seg_index[s["hash"]] = (res["plan"]["hash"], s["i"])
            for k in [k for k in list(self.plans) if k[0] == "rev"][:-8]:
                self.plans.pop(k, None)
        return res["plan"], entry, st

    # ---- routes
    def handle(self, h):
        u = urllib.parse.urlparse(h.path)
        parts = [p for p in u.path.split("/") if p]
        if not parts or parts[0] != self.token:
            return self.send(h, 404, b"not found", "text/plain")
        rest = parts[1:]
        q = urllib.parse.parse_qs(u.query)
        if not rest:
            with open(S.viewer_page, encoding="utf-8") as f:
                return self.send(h, 200, f.read().replace("@@TOKEN@@", self.token).encode(), "text/html; charset=utf-8")
        if rest == ["vendor", "hls.min.js"]:
            with open(os.path.join(S.vendor_dir, "hls.min.js"), "rb") as f:
                return self.send(h, 200, f.read(), "application/javascript", "max-age=86400")
        if rest == ["plan.json"]:
            since = int(q.get("since", ["-1"])[0])
            deadline = time.time() + 25
            while True:
                st = self.load_state()
                rev = st.get("revision", 0) if st else 0
                if rev != since or time.time() > deadline:
                    break
                time.sleep(0.25)
            return self.plan_json(h)
        if rest == ["stats"]:
            r = self.renderer.stats
            return self.send(h, 200, json.dumps({k: (v if k != "segment_seconds" else [round(x, 2) for x in v]) for k, v in r.items()}).encode(), "application/json")
        if len(rest) == 2 and rest[0] == "m" and rest[1].endswith(".m3u8"):
            e = self.plans.get(("hash", rest[1][:-5]))
            if not e:
                return self.send(h, 404, b"unknown plan", "text/plain")
            from ..preview import segments
            return self.send(h, 200, segments.master_playlist(e["plan"], f"/{self.token}/v/{rest[1]}", f"/{self.token}/a/{e['plan']['audio_hash']}/audio.m3u8").encode(), "application/vnd.apple.mpegurl")
        if len(rest) == 2 and rest[0] == "v" and rest[1].endswith(".m3u8"):
            e = self.plans.get(("hash", rest[1][:-5]))
            if not e:
                return self.send(h, 404, b"unknown plan", "text/plain")
            from ..preview import segments
            return self.send(h, 200, segments.video_playlist(e["plan"], lambda sh: f"/{self.token}/s/{sh}.ts").encode(), "application/vnd.apple.mpegurl")
        if len(rest) == 2 and rest[0] == "s" and rest[1].endswith(".ts"):
            return self.segment(h, rest[1][:-3])
        if len(rest) == 3 and rest[0] == "a":
            return self.audio(h, rest[1], rest[2])
        return self.send(h, 404, b"not found", "text/plain")

    def plan_json(self, h):
        plan, entry, st = self.current_plan()
        if plan is None:
            return self.send(h, 200, json.dumps({"empty": True, "revision": (st or {}).get("revision", 0)}).encode(), "application/json")
        ops = []
        for i, o in enumerate(entry["st"]["ops"]):
            title, code = live.describe(o)
            ops.append({"index": i, "id": o.get("id"), "op": o["op"], "title": title, "code": code})
        body = {"revision": entry["st"].get("revision", 0), "hash": plan["hash"], "master": f"/{self.token}/m/{plan['hash']}.m3u8", "svg": entry["svg"], "ops": ops,
                "warnings": entry["warnings"], "duration": entry["duration"], "segments": len(plan["segs"])}
        return self.send(h, 200, json.dumps(body).encode(), "application/json")

    def segment(self, h, seg_hash):
        path = self.renderer.seg_path(seg_hash)
        if not os.path.exists(path):
            with self.lock:
                ref = self.seg_index.get(seg_hash)
            if ref is None:
                return self.send(h, 404, b"unknown segment", "text/plain")
            e = self.plans[("hash", ref[0])]
            plan, i = e["plan"], ref[1]
            s = plan["segs"][i]
            for k in range(1, S.viewer_prefetch + 1):                 # the ones after it, rendered while this one plays
                if i + k < len(plan["segs"]):
                    n = plan["segs"][i + k]
                    self.renderer.segment(n["hash"], e["st"], n["a"], n["b"], priority=k, wait=False)
            path = self.renderer.segment(seg_hash, e["st"], s["a"], s["b"], priority=0)
            if path is None:
                return self.send(h, 500, ("segment render failed: " + self.renderer.errors.get("seg|" + seg_hash, "?")).encode(), "text/plain")
        with open(path, "rb") as f:
            return self.send(h, 200, f.read(), "video/mp2t", "public, max-age=31536000, immutable")

    def audio(self, h, audio_hash, name):
        if "/" in name or ".." in name:
            return self.send(h, 404, b"not found", "text/plain")
        d = self.renderer.audio_dir(audio_hash)
        if not os.path.exists(os.path.join(d, "audio.m3u8")):
            e = next((v for k, v in list(self.plans.items()) if k[0] == "hash" and v["plan"]["audio_hash"] == audio_hash), None)
            if e is None:
                return self.send(h, 404, b"unknown audio", "text/plain")
            if self.renderer.audio(audio_hash, e["st"]) is None:
                return self.send(h, 500, b"audio render failed", "text/plain")
        p = os.path.join(d, name)
        if not os.path.exists(p):
            return self.send(h, 404, b"not found", "text/plain")
        with open(p, "rb") as f:
            body = f.read()
        return self.send(h, 200, body, "application/vnd.apple.mpegurl" if name.endswith(".m3u8") else "video/mp2t", "no-store" if name.endswith(".m3u8") else "public, max-age=31536000, immutable")


def start(home, port=0):
    """The viewer of this process (made once)."""
    if _SINGLETON["v"] is None:
        _SINGLETON["v"] = Viewer(home, port)
    return _SINGLETON["v"]

