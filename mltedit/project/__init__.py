"""The project file: schema, migration from older files, revisions, the undo/redo patches and the history journal.

project.json (schema 2) = {sources, ops, width, height, fps, theme, motion, schema_version, revision, undo, redo}
  * every op has a stable `id` ("op_" + 6 hex); clips are referenced by the id of their `add` op, never by position
  * `revision` goes up by one on every save, so an agent can say which state its edit was made against
  * `undo` / `redo` are stacks of patches; applying a patch returns its inverse (that is what goes on the other stack)
history.jsonl is an append-only audit log (one line per saved change); the state file is the source of truth.
Nothing here imports the engine or the renderers: only the op plugins (which know their own references) are consulted when migrating."""
import contextlib, fcntl, hashlib, json, os, secrets, time

from .. import ops as O

SCHEMA_VERSION = 2
MAX_UNDO = 200
REQUIRED = {"sources", "ops", "width", "height", "fps"}


# ------------------------------------------------------------------------------------------------ ids and migration
def new_op_id(taken=()):
    while True:
        i = "op_" + secrets.token_hex(3)
        if i not in taken:
            return i


def _stable_id(position, op, taken):
    """Deterministic id for a migrated op: the same v1 file always migrates to the same ids."""
    h = hashlib.sha1(json.dumps([position, op], sort_keys=True, default=str).encode()).hexdigest()
    n = 6
    while "op_" + h[:n] in taken:
        n += 1
    return "op_" + h[:n]


def migrate(st):
    """Bring a project dict to the current schema (in memory; the next save writes it). Idempotent. v1 -> v2: give every op an id, and turn the
    entry positions in `cut.clip` and `crossfade.between` into the ids of the `add` ops that made those entries."""
    if st.get("schema_version", 1) >= SCHEMA_VERSION:
        st.setdefault("revision", 0); st.setdefault("undo", []); st.setdefault("redo", [])
        return st
    taken, entry_ids = set(), []
    for pos, o in enumerate(st["ops"]):
        if "id" not in o:
            o["id"] = _stable_id(pos, {k: v for k, v in o.items() if k != "id"}, taken)
        taken.add(o["id"])
        plug = O.get_op(o.get("op"))                       # the op plugin knows which of its fields refer to clips
        if plug is not None:
            st["ops"][pos] = o = plug.migrate_refs(o, entry_ids)
            if plug.makes_clip:
                entry_ids.append(o["id"])
    st["schema_version"], st["revision"], st["undo"], st["redo"] = SCHEMA_VERSION, st.get("revision", 0), [], []
    return st


def check_shape(st):
    if not (isinstance(st, dict) and REQUIRED <= set(st)):
        raise ValueError("not a project file")


# ------------------------------------------------------------------------------------------------ file access
@contextlib.contextmanager
def locked(lock_path):
    """Exclusive cross-process lock around a read-modify-write of the project (blocking; released on exit or process death)."""
    with open(lock_path, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def load(path, default):
    if not os.path.exists(path):
        return migrate(json.loads(json.dumps(default)))
    try:
        with open(path) as f:
            st = json.load(f)
        check_shape(st)
        return migrate(st)
    except (ValueError, OSError) as e:
        raise RuntimeError(f"project file {path} is unreadable ({e}); fix or delete it, or call new_project")


def save(path, st, event=None):
    """Write the project atomically (a crash leaves the old file or the new one, never half), bump the revision, journal the change."""
    st["revision"] = st.get("revision", 0) + 1
    st["schema_version"] = SCHEMA_VERSION
    st["undo"] = st.get("undo", [])[-MAX_UNDO:]
    st["redo"] = st.get("redo", [])[-MAX_UNDO:]
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)
    journal(os.path.join(os.path.dirname(path), "history.jsonl"), {"rev": st["revision"], "ts": round(time.time(), 3), **(event or {"kind": "save"})})
    return st["revision"]


def journal(path, entry):
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            f.flush(); os.fsync(f.fileno())
    except OSError:
        pass                                                  # an audit line must never fail an edit


def read_journal(path, last=None):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(ln) for ln in f if ln.strip()]
    return rows[-last:] if last else rows


def rotate_journal(path):
    """A new project starts a new journal (request ids of the old one must not replay into it); the old one is kept next to it for the record."""
    if os.path.exists(path) and os.path.getsize(path):
        os.replace(path, f"{path[:-6]}.{int(time.time())}.jsonl")


def clean_stale_tmp(path):
    """Remove `<project>.<pid>.tmp` files left by a process that died between writing and renaming."""
    d, base = os.path.dirname(path) or ".", os.path.basename(path)
    for name in os.listdir(d):
        if name.startswith(base + ".") and name.endswith(".tmp"):
            try:
                pid = int(name[len(base) + 1:-4])
                os.kill(pid, 0)
            except ProcessLookupError:
                try:
                    os.remove(os.path.join(d, name))
                except OSError:
                    pass
            except (ValueError, PermissionError):
                pass


# ------------------------------------------------------------------------------------------------ files the project depends on
def file_problems(st):
    """Files whose state differs from what the project recorded: [{code, what, path, ...}]. Sources record [size, mtime_ns] on import; image and audio
    edits record theirs on commit. A project without a signature (older file) is not checked for that file, only for existing."""
    out = []

    def chk(path, sig, what, **extra):
        now = file_sig(path)
        if now is None:
            out.append({"code": "SOURCE_MISSING", "what": what, "path": path, **extra})
        elif sig is not None and list(sig) != now:
            out.append({"code": "SOURCE_CHANGED", "what": what, "path": path, **extra})

    for sid, src in st["sources"].items():
        chk(src["path"], src.get("sig"), f"source {sid}", source=sid)
    for i, o in enumerate(st["ops"]):
        plug = O.get_op(o.get("op"))
        for f in (plug.files(o) if plug else []):
            chk(f, o.get("file_sig"), f"op {i} ({o['op']})", op=o.get("id"))
    return out


LAYOUT_VERSION = 1                                        # bump when the code changes what a given project lays out to


def layout_hash(st):
    """Hash of everything the layout of a project depends on (the stored ops, the format, the template, the sources' identity): equal hashes mean an
    identical timeline. Used as the key of the preview caches. Pure: reads nothing from disk."""
    data = {"v": LAYOUT_VERSION, "ops": st["ops"], "size": [st["width"], st["height"], st["fps"]], "theme": st.get("theme"), "motion": bool(st.get("motion")),
            "sources": {k: [v["path"], v.get("sig"), v.get("duration_s"), v.get("width"), v.get("height")] for k, v in sorted(st["sources"].items())}}
    return hashlib.sha1(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def file_sig(path):
    from ..ops.common import file_sig as sig
    return sig(path)


# ------------------------------------------------------------------------------------------------ patches (undo / redo)
def index_of(st, op_id):
    for i, o in enumerate(st["ops"]):
        if o.get("id") == op_id:
            return i
    raise ValueError(f"no op with id '{op_id}'")


def apply_patch(st, p):
    """Apply a patch to the project dict in memory and return its inverse.
    {"k":"pop","id"}: remove that op | {"k":"insert","index","op"} | {"k":"replace","id","op"}: put that op in place of the one with the id |
    {"k":"set","fields"}: set top-level fields (theme, motion)."""
    k = p["k"]
    if k == "batch":                                       # several patches as one step (e.g. a transition and its sound effect)
        return {"k": "batch", "patches": [apply_patch(st, q) for q in p["patches"]][::-1]}
    if k == "pop":
        i = index_of(st, p["id"])
        return {"k": "insert", "index": i, "op": st["ops"].pop(i)}
    if k == "insert":
        st["ops"].insert(p["index"], p["op"])
        return {"k": "pop", "id": p["op"]["id"]}
    if k == "replace":
        i = index_of(st, p["id"])
        old, st["ops"][i] = st["ops"][i], p["op"]
        return {"k": "replace", "id": p["id"], "op": old}
    if k == "set":
        old = {f: st.get(f) for f in p["fields"]}
        for f, v in p["fields"].items():
            if v is None:
                st.pop(f, None)
            else:
                st[f] = v
        return {"k": "set", "fields": old}
    raise ValueError(f"unknown patch {k}")
