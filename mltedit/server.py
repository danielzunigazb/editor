#!/usr/bin/env python3
"""MCP server: a non-linear video editor driven entirely by an LLM (no UI, no controls).

Engine: MLT (timeline + preview rendering). Final export: MLT composes, the ffmpeg CLI encodes.
Edits are cheap and validated instantly (pure-python timeline model); rendering happens on demand
(get_still / get_contact_sheet / render_preview / export), always from the full edit list.

Run (stdio):  .venv/bin/python server.py          [MLT_EDITOR_HOME=<project dir>]
Needs the apt binding (python3-mlt) -> use the venv built with --system-site-packages on python3.12.
"""
import contextvars, io, json, os, subprocess, sys, time

# ---- stdout hygiene: MLT/ffmpeg/LADSPA may print to fd 1, which would corrupt the stdio protocol.
_real = os.dup(1)
os.dup2(2, 1)
sys.stdout = io.TextIOWrapper(os.fdopen(_real, "wb", closefd=False), encoding="utf-8", write_through=True)

from .config import S  # noqa: E402
HOME = S.home
PROJECT = os.path.join(HOME, "project.json")
os.makedirs(HOME, exist_ok=True)


def _ensure_display():
    """qtblend (the only compositor that honours opacity) needs X11 even when headless."""
    if os.environ.get("DISPLAY"):
        return
    r, w = os.pipe()
    def die_with_parent():                 # MCP clients usually SIGKILL stdio servers, so atexit never runs:
        import ctypes, signal              # ask the kernel to SIGTERM Xvfb when this process dies (PR_SET_PDEATHSIG)
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)
    try:
        proc = subprocess.Popen(["Xvfb", "-displayfd", str(w), "-screen", "0", S.xvfb_screen, "-nolisten", "tcp"],
                                pass_fds=[w], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=die_with_parent)
    except FileNotFoundError:
        raise RuntimeError("Xvfb is not installed (needed by the qtblend transition): apt-get install xvfb, or set DISPLAY")
    os.close(w)
    num = os.read(r, 16).decode().strip()
    if not num:
        raise RuntimeError("could not start Xvfb (needed by the qtblend transition); install xvfb or set DISPLAY")
    os.environ["DISPLAY"] = f":{num}"
    import atexit
    atexit.register(proc.terminate)


_ensure_display()
from . import engine as live  # noqa: E402  (engine: layout/build/render)
from . import themes  # noqa: E402
from . import ops as O  # noqa: E402
from . import binding  # noqa: E402
from . import project as P  # noqa: E402
from .media import proxy as proxies  # noqa: E402
from .errors import EditError, as_edit_error, check_until  # noqa: E402
from . import assets as assets_lib  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

INSTRUCTIONS = """Video editor. Edits are validated at once and cost nothing to render; look with get_still / get_contact_sheet, or open_viewer for a live player.
Every response has `revision`; every edit and clip has a stable id (op_id, clip id): use ids, not positions, and get_timeline / describe_project / query to find them.
Arguments every EDIT tool takes: expected_revision (refuse the edit if the project moved on: REVISION_CONFLICT), dry_run=true (change nothing, get a diff of what the
call would do), request_id (a retry with the same id applies nothing twice). Overlay and audio edits also take anchor: "clip" (default: the edit is tied to the clip on
screen at start_s and moves with it when earlier clips are cut, trimmed, moved or removed) or "timeline" (stays at that time).
Failures read `CODE: message` plus a JSON line; codes: """ + ", ".join(__import__("mltedit.errors", fromlist=["CODES"]).CODES) + """.
undo/redo cover every change, a whole apply_ops batch is one step. export and render_preview accept background=true (job_status, cancel_job)."""
mcp = FastMCP(S.server_name, instructions=INSTRUCTIONS)

# ------------------------------------------------------------------ project state
DEFAULT = {"sources": {}, "ops": [], **S.project_defaults, "theme": {"name": themes.DEFAULT, "accent": None}}
LOCK = os.path.join(HOME, "project.lock")
MAX_OPS, MAX_SOURCES = S.max_ops, S.max_sources
SUBPROCESS_TIMEOUT = S.subprocess_timeout          # ffprobe / still-encode: a hung or hostile file must not freeze the server
ROOTS = [os.path.realpath(r) for r in S.roots]


def _safe_path(path, what, must_exist=True):
    """Resolve `path` (symlinks included) and, if MLT_EDITOR_ROOTS is set, require it to live under one of those
    directories. An LLM-driven editor reads and writes arbitrary paths otherwise; this is the opt-in fence."""
    if not isinstance(path, str) or not path.strip() or "\0" in path:
        raise ValueError(f"{what}: path must be a non-empty string")
    p = os.path.realpath(os.path.expanduser(path))
    if ROOTS and not any(p == r or p.startswith(r + os.sep) for r in ROOTS):
        raise ValueError(f"{what}: '{path}' is outside the allowed folders (MLT_EDITOR_ROOTS = {os.pathsep.join(ROOTS)})")
    if must_exist and not os.path.exists(p):
        raise ValueError(f"{what}: file not found: {p}")
    return p


CALL = contextvars.ContextVar("mlt_call", default=None)    # per tool call: tool name, expected_revision, ... (set by tools.edit_tool)


def _call():
    return CALL.get() or {}


def locked():
    """Exclusive cross-process lock around a read-modify-write of project.json (see project.locked)."""
    return P.locked(LOCK)


def load():
    return P.load(PROJECT, DEFAULT)


HISTORY = os.path.join(HOME, "history.jsonl")


class Replayed(Exception):
    """An edit with a request_id that was already applied: carries the answer instead of applying it twice."""
    def __init__(self, result):
        super().__init__("replayed")
        self.result = result


def save(st, event=None):
    """Persist (atomic, revision + 1, journal line). `event` describes the change in the journal; the tool name is added.
    In a dry run (dry_run=true on the tool call) nothing is written: the would-be project is kept in the call context for the report."""
    c = _call()
    if c.get("dry_run"):
        c["state"] = json.loads(json.dumps(st))
        return st.get("revision", 0) + 1
    ev = {"kind": "save", **(event or {})}
    if c.get("tool"):
        ev["tool"] = c["tool"]
    if c.get("request_id"):
        ev["request_id"], ev["args"] = c["request_id"], c.get("args")
    return P.save(PROJECT, st, ev)


def dry_run():
    return bool(_call().get("dry_run"))


def check_revision(st):
    """Called by every edit once it holds the project lock and has loaded the project:
    * request_id: if an edit with this id was already applied, answer with that instead of applying it again (an agent that retries after a timeout
      must not duplicate its edit); the same id on a DIFFERENT call is an error
    * expected_revision: refuse the edit, changing nothing, if the project moved on."""
    rid = _call().get("request_id")
    if rid and not dry_run():
        for row in reversed(P.read_journal(HISTORY, last=5000)):
            if row.get("request_id") == rid:
                if row.get("args") != _call().get("args"):
                    raise EditError("INVALID_ARGUMENT", f"request_id '{rid}' was already used by a different call ({row.get('tool')} at revision {row['rev']}); use a new id for a new edit",
                                    field="request_id")
                raise Replayed({"replayed": True, "request_id": rid, "applied_at_revision": row["rev"], "tool": row.get("tool"),
                                "op_ids": row.get("ops") or ([row["op"]] if row.get("op") else []), "note": "this edit was already applied; nothing was done again", **summary(st)})
    exp = _call().get("expected_revision")
    if exp is not None and exp != st.get("revision", 0):
        raise EditError("REVISION_CONFLICT", f"the project is at revision {st.get('revision', 0)} but this edit was made against revision {exp}; "
                        f"call get_timeline to see the current state and make the edit again; nothing was changed", hint="get_timeline")


def diff_states(before, after):
    """What a would-be project changes compared with the current one: ops added / removed / changed (by id), things that moved on the timeline,
    the duration, and the warnings that appear or disappear."""
    b, a = summary(before, full=True), summary(after, full=True)
    bo, ao = {o["id"]: o for o in b["ops"]}, {o["id"]: o for o in a["ops"]}
    strip = lambda o: {k: v for k, v in o.items() if k != "index"}            # noqa: E731
    out = {"ops_added": [{"id": i, "op": ao[i]["op"]} for i in ao if i not in bo], "ops_removed": [{"id": i, "op": bo[i]["op"]} for i in bo if i not in ao],
           "ops_changed": [{"id": i, "fields": sorted(k for k in set(ao[i]) | set(bo[i]) if k != "index" and ao[i].get(k) != bo[i].get(k))} for i in ao if i in bo and strip(ao[i]) != strip(bo[i])]}
    bid = {i: o["id"] for i, o in enumerate(b["ops"])}
    aid = {i: o["id"] for i, o in enumerate(a["ops"])}
    moved = []
    for key in ("overlays", "audio"):
        bs = {bid[x["op"]]: x for x in b[key] if x["op"] in bid}
        for x in a[key]:
            i = aid.get(x["op"])
            if i in bs and (bs[i]["start_s"], bs[i]["end_s"]) != (x["start_s"], x["end_s"]):
                moved.append({"id": i, "from": [bs[i]["start_s"], bs[i]["end_s"]], "to": [x["start_s"], x["end_s"]]})
    shown = lambda s, ids: {ids[x["op"]] for k in ("overlays", "audio") for x in s[k] if x["op"] in ids}       # noqa: E731
    out["moved"] = moved
    out["hidden"] = sorted(shown(b, bid) - shown(a, aid) - {i for i in bo if i not in ao})
    be, ae = {e["id"]: e for e in b["entries"]}, {e["id"]: e for e in a["entries"]}
    out["clips_moved"] = [{"id": i, "from": [be[i]["start_s"], be[i]["end_s"]], "to": [ae[i]["start_s"], ae[i]["end_s"]]} for i in ae if i in be and (be[i]["start_s"], be[i]["end_s"]) != (ae[i]["start_s"], ae[i]["end_s"])]
    out["duration_s"] = {"from": b["duration_s"], "to": a["duration_s"]} if b["duration_s"] != a["duration_s"] else None
    out["warnings_new"] = [w for w in a["warnings"] if w not in b["warnings"]]
    out["warnings_gone"] = [w for w in b["warnings"] if w not in a["warnings"]]
    return {k: v for k, v in out.items() if v}


def dry_run_report(before, ctx, result):
    """The answer to a dry run: nothing was saved; here is what the edit would do."""
    after = ctx.get("state")
    base = {"dry_run": True, "applied": False, "revision": before.get("revision", 0)}
    if after is None:
        return {**base, "diff": {}, "note": "this call would not change the project"}
    a = summary(after)
    return {**base, "diff": diff_states(before, after), "duration_s": a["duration_s"], "op_count": a["op_count"], "warnings": a["warnings"],
            **({"would_be_op_id": result["op_id"]} if isinstance(result, dict) and result.get("op_id") else {}),
            **({"would_be_op_ids": result["op_ids"]} if isinstance(result, dict) and result.get("op_ids") else {})}


def push_undo(st, patch):
    st["undo"].append(patch)
    st["redo"] = []                                         # a new edit ends the redo branch


def prepare(st, op):
    """The op as it will be stored: with an id, and its references to clips as clip ids."""
    bind(st)
    plug = O.get_op(op.get("op"))
    op = {**op, "id": op.get("id") or P.new_op_id({o.get("id") for o in st["ops"]})}
    if plug is not None:
        m = live.layout(st["ops"])
        op = plug.resolve_refs(op, m["entries"])
        if plug.anchorable and not isinstance(op.get("anchor"), dict):
            mode = op.get("anchor") or _call().get("anchor") or "clip"
            op = {**op, "anchor": make_anchor(m["entries"], plug.start_of(op), mode)}
            until = op.get("until") or _call().get("until") or ""
            if until:
                op["until"] = check_until(until, mode)
        try:
            op = plug.freeze(plug.normalize(op, live.CTX), live.CTX)
        except ValueError as e:
            raise as_edit_error(e, f"op {len(st['ops'])} ({op.get('op')})")
    return op


def make_anchor(entries, start, mode="clip"):
    """The anchor of an edit placed at timeline second `start`: the frame of the source clip that is on screen at that moment (the incoming clip wins
    inside a transition), or the timeline frame itself when no clip is there or mode is "timeline". `entries` = the layout's entries."""
    if mode not in ("clip", "timeline"):
        raise EditError("INVALID_ARGUMENT", f"anchor must be 'clip' or 'timeline' (got {mode!r})")
    if not isinstance(start, (int, float)) or isinstance(start, bool) or start != start or start in (float("inf"), float("-inf")):
        return None                                           # not a time: the edit will be refused by validation, with its own message
    t_f = live.CTX.fr(start)
    if mode == "clip":
        here = [e for e in entries if e.get("id") and e["start_f"] <= t_f < e["start_f"] + e["dur_f"]]
        if here:
            e = here[-1]
            return {"clip": e["id"], "src_f": e["in_f"] + t_f - e["start_f"], "t0_f": t_f}
    return {"timeline_f": t_f, "t0_f": t_f}


def reanchor(st, op, mode=None):
    """The op with a fresh anchor for its current start (after its time changed). mode None keeps the kind it had (clip / timeline)."""
    bind(st)
    plug = O.get_op(op.get("op"))
    if plug is None or not plug.anchorable:
        return op
    old = op.get("anchor") if isinstance(op.get("anchor"), dict) else {}
    mode = mode or ("timeline" if "timeline_f" in old else "clip")
    return {**{k: v for k, v in op.items() if k != "anchor"}, "anchor": make_anchor(live.layout(st["ops"])["entries"], plug.start_of(op), mode)}


def _anchor_view(a):
    return {k: v for k, v in a.items() if k != "t0_f"} if isinstance(a, dict) else a


def require_fresh(st):
    """Before rendering: every file the project depends on must still be there. (A file that CHANGED still renders - what is on disk is what you see,
    and the caches are keyed on it - but get_timeline warns and verify_sources lists it, because its recorded length may no longer be true.)"""
    gone = [p for p in P.file_problems(st) if p["code"] == "SOURCE_MISSING"]
    if gone:
        more = f" (and {len(gone) - 1} more: verify_sources lists them)" if len(gone) > 1 else ""
        raise EditError("SOURCE_MISSING", f"{gone[0]['what']}: {gone[0]['path']} is missing{more}", hint="import_clip it again, or remove the edit")


def _file_warnings(st):
    return [f"{p['what']}: {p['path']} changed on disk since it was recorded; " + (f"refresh_source('{p['source']}') re-reads it" if p.get("source") else "check the edit")
            for p in P.file_problems(st) if p["code"] == "SOURCE_CHANGED"][:3]


def bind(st, scale=1.0):
    """Point the engine at this project's sources/resolution. scale<1 => proxy-resolution preview."""
    binding.bind(st, HOME, scale)


def _validate(st, op):
    """Check `op` against the whole timeline as it is NOW (st['ops'] included). Raises ValueError. Saves nothing."""
    bind(st)                                   # export resolution: text-fit is checked at full size
    before = live.layout(st["ops"])["total"]
    live.layout(st["ops"] + [op])
    live.check_new_op(op)
    plug = O.get_op(op["op"])
    err = plug.placement_error(op, before) if plug else None
    if err:
        raise ValueError(err)


def commit(op):
    """Validate the op against the whole timeline, then persist (under the project lock). Nothing is saved on error."""
    with locked():
        st = load()
        check_revision(st)
        if len(st["ops"]) >= MAX_OPS:
            raise ValueError(f"the project already has {MAX_OPS} edits (the limit); export it or start a new project")
        op = prepare(st, op)
        _validate(st, op)
        st["ops"].append(op)
        push_undo(st, {"k": "pop", "id": op["id"]})
        save(st, {"kind": "commit", "op": op["id"]})
        return {"op_id": op["id"], **summary(st)}


def replace_op(st, i, new):
    """Swap the op at position i for `new` (keeping its id): the whole timeline is validated again and the undo patch is recorded. Raises
    ValueError, leaving st as it was."""
    bind(st)                                                   # export resolution: text-fit is checked at full size
    old = st["ops"][i]
    new = {**new, "id": old["id"]}
    live.layout(st["ops"][:i] + [new] + st["ops"][i + 1:])
    live.check_new_op(new)
    st["ops"][i] = new
    push_undo(st, {"k": "replace", "id": old["id"], "op": old})


BUILDERS = {}                       # filled by tools.install(): one op builder per edit tool (the tools and apply_ops share them)


def _legibility(st, max_warnings=3):
    """Warnings for type that would be too small to read at the project's export size; each op plugin judges its own."""
    out = []
    for i, o in enumerate(st["ops"]):
        plug = O.get_op(o.get("op"))
        if plug:
            out += plug.legibility(o, i, st)
    return out[:max_warnings]


def summary(st, full=False):
    """Timeline as JSON. Edit tools return the compact form (the model already knows the op it just sent);
    get_timeline/undo/remove_op return `full` with the numbered op list."""
    bind(st)
    m = live.layout(st["ops"])
    layers, seen = [], {}
    for L in m["layers"]:
        grp = O.get_layer(L["kind"]).group(L)
        if grp:                           # collapse e.g. a subtitle file into one line
            g = seen.get(L["op"])
            if g:
                g["cues"] += 1; g["end_s"] = round(max(g["end_s"], L["start"] + L["dur"]), 3); continue
            g = {"kind": grp, "op": L["op"], "cues": 1, "start_s": round(L["start"], 3),
                 "end_s": round(L["start"] + L["dur"], 3), "track": L["track"]}
            seen[L["op"]] = g; layers.append(g); continue
        d = {"kind": L["kind"], "op": L["op"], "start_s": round(L["start"], 3), "end_s": round(L["start"] + L["dur"], 3),
             "track": L["track"], **({"anchor": _anchor_view(L["anchor"])} if L.get("anchor") else {}), **O.get_layer(L["kind"]).summary(L)}
        layers.append(d)
    out = {
        "revision": st.get("revision", 0),
        "template": live.THEME.name,
        **({"motion": True} if live.MOTION else {}),
        "duration_s": round(m["total"], 3),
        "entries": [{"index": i, "id": e.get("id"), "source": e["src"], "source_in_s": round(e["in"], 3),
                     "start_s": round(e["start"], 3), "end_s": round(e["start"] + e["dur"], 3)}
                    for i, e in enumerate(m["entries"])],
        "crossfades": [{"between": [a, a + 1], "dur_s": d} for a, d in sorted(m["xfades"].items())],
        "fade": m["fade"],
        "overlays": layers,
        "audio": [{"op": a["op"], "name": a["name"], "start_s": round(a["start"], 3), "end_s": round(a["start"] + a["dur_eff"], 3), "volume_db": a["vol"],
                   "loop": a["loop"], "ducked": len(a["duck"]), **({"anchor": _anchor_view(a["anchor"])} if a.get("anchor") else {})} for a in m["audios"]],
        "warnings": m["warnings"] + _legibility(st) + _file_warnings(st),
        "op_count": len(st["ops"]),
    }
    credits = assets_lib.credit_lines(O.project_assets(st["ops"]))
    if credits:
        out["credits_required"] = credits
    if full:
        out["ops"] = [{"index": i, **({k: v for k, v in o.items() if k != "cues"}), **({"cues": len(o["cues"])} if "cues" in o else {})}
                      for i, o in enumerate(st["ops"])]
        out["can_undo"], out["can_redo"] = len(st.get("undo", [])), len(st.get("redo", []))
        out["layout_hash"] = P.layout_hash(st)
    return out


def _probe(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                           capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise ValueError(f"ffprobe timed out reading '{path}' (is it a network mount or a corrupt file?)")
    if r.returncode:
        raise ValueError(f"ffprobe could not read '{path}': {r.stderr.strip() or 'unknown error'}")
    info = json.loads(r.stdout)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if not v:
        raise ValueError(f"'{path}' has no video stream")
    try:
        dur = float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"'{path}' has no readable duration (is it a complete video file?)")
    if dur < 0.2 or v.get("codec_name") in ("mjpeg", "png", "bmp", "webp", "gif"):
        raise ValueError(f"'{path}' looks like an image or a single frame ({dur:g}s, {v.get('codec_name')}); "
                         f"use add_image for pictures")
    return {"duration_s": round(dur, 3), "width": v["width"], "height": v["height"],
            "codec": v["codec_name"], "has_audio": any(s["codec_type"] == "audio" for s in info["streams"])}


def ensure_proxies(st, wait=False):
    """Start (in the background) the proxies of the project's sources that are wanted and missing. wait=True: then wait up to proxy_wait_s for the pending
    ones, so a picture is made from the same media whether the proxy was ready a moment ago or not (past that wait it falls back to the original)."""
    for src in st["sources"].values():
        proxies.ensure(HOME, src)
    if wait:
        proxies.wait(HOME, st["sources"], float(S.proxy_wait_s))


def prune_cache(max_mb=500, max_age_days=14):
    """The rendered-PNG cache (text, graphics, cropped overlays) only grows. Drop what is old, then the oldest until
    the folder fits max_mb; everything in it is regenerated on demand. Returns the number of files removed."""
    cache = os.path.join(HOME, "cache")
    if not os.path.isdir(cache):
        return 0
    files = []
    for name in os.listdir(cache):
        p = os.path.join(cache, name)
        try:
            if os.path.isfile(p):
                stt = os.stat(p); files.append((stt.st_mtime, stt.st_size, p))
        except OSError:
            pass
    files.sort()
    cutoff, total, removed = time.time() - max_age_days * 86400, sum(f[1] for f in files), 0
    for mt, size, p in files:
        if mt < cutoff or total > max_mb * 1_000_000:
            try:
                os.remove(p); total -= size; removed += 1
            except OSError:
                pass
    return removed


def main():
    prune_cache()
    proxies.prune(HOME, S.proxy_cache_mb)
    try:
        ensure_proxies(load())                                 # after a restart: finish what was pending
    except RuntimeError:
        pass
    mcp.run()


from . import tools  # noqa: E402
tools.install(sys.modules[__name__], mcp)


if __name__ == "__main__":
    main()
