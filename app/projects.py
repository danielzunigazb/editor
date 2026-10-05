"""Projects on disk: <data>/projects/<id>/ holds the engine's own files (project.json, history, cache, renders) plus meta.json, chat.json, usage.json, uploads/ and exports/.
Ids are `p_` + 8 hex digits and are checked on EVERY use, so an id taken from a URL can never be a path."""
import json, os, re, secrets, shutil, time

ID_RE = re.compile(r"^p_[0-9a-f]{8}$")
UPLOAD_EXT = {"video": (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"), "other": (".mp3", ".wav", ".m4a", ".ogg", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".srt")}


class NotFound(Exception):
    pass


def _write(path, data):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def safe_filename(name):
    """A file name from the browser reduced to something harmless: no folders, no leading dots, no odd characters, a bounded length. '' if nothing is left."""
    base = os.path.basename((name or "").replace("\\", "/"))
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .")
    stem, ext = os.path.splitext(base)
    return (stem[:80].strip(" .") + ext.lower()[:8]) if stem else ""


def is_video(name):
    return name.lower().endswith(UPLOAD_EXT["video"])


def is_allowed_upload(name):
    return name.lower().endswith(UPLOAD_EXT["video"] + UPLOAD_EXT["other"])


class Projects:
    def __init__(self, data_dir):
        self.root = os.path.join(data_dir, "projects")
        os.makedirs(self.root, exist_ok=True)

    def dir(self, pid):
        if not isinstance(pid, str) or not ID_RE.match(pid):
            raise NotFound(pid)
        return os.path.join(self.root, pid)

    def exists(self, pid):
        try:
            return os.path.isfile(os.path.join(self.dir(pid), "meta.json"))
        except NotFound:
            return False

    def create(self, name, width, height, fps):
        pid = "p_" + secrets.token_hex(4)
        d = self.dir(pid)
        os.makedirs(os.path.join(d, "uploads"))
        os.makedirs(os.path.join(d, "exports"))
        meta = {"id": pid, "name": (name or "Untitled").strip()[:80] or "Untitled", "width": width, "height": height, "fps": fps, "created": time.time()}
        _write(os.path.join(d, "meta.json"), meta)
        return meta

    def meta(self, pid):
        if not self.exists(pid):
            raise NotFound(pid)
        return _read(os.path.join(self.dir(pid), "meta.json"), {})

    def list(self):
        out = []
        for n in sorted(os.listdir(self.root)):
            if ID_RE.match(n) and self.exists(n):
                out.append({**self.meta(n), "usd": self.usage(n)["usd"]})
        return sorted(out, key=lambda m: -m["created"])

    def delete(self, pid):
        d = self.dir(pid)
        if not self.exists(pid):
            raise NotFound(pid)
        shutil.rmtree(d)

    def uploads(self, pid):
        d = os.path.join(self.dir(pid), "uploads")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def exports(self, pid):
        d = os.path.join(self.dir(pid), "exports")
        return sorted(f for f in os.listdir(d) if f.endswith((".mp4", ".mov"))) if os.path.isdir(d) else []

    def load_chat(self, pid):
        return _read(os.path.join(self.dir(pid), "chat.json"), [])

    def save_chat(self, pid, messages):
        _write(os.path.join(self.dir(pid), "chat.json"), messages)

    def usage(self, pid):
        return _read(os.path.join(self.dir(pid), "usage.json"), {"usd": 0.0, "input_tokens": 0, "output_tokens": 0, "messages": 0})

    def add_usage(self, pid, usd, usage, message=False):
        u = self.usage(pid)
        u["usd"] = round(u["usd"] + usd, 6)
        u["input_tokens"] += usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
        u["output_tokens"] += usage.get("output_tokens", 0)
        u["messages"] += 1 if message else 0
        _write(os.path.join(self.dir(pid), "usage.json"), u)
        return u
