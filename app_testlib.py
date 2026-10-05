"""A real application server for tests: uvicorn in a thread, a temporary data folder, and a model per project that the test chooses (a ScriptedModel)."""
import json, shutil, socket, tempfile, threading, time

import httpx, uvicorn

from app.api import create_app
from app.config import AppSettings

TOKEN = "test-token-123"


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
