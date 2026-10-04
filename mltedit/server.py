#!/usr/bin/env python3
"""MCP server: a non-linear video editor driven entirely by an LLM (no UI, no controls).

Engine: MLT (timeline + preview rendering). Final export: MLT composes, the ffmpeg CLI encodes.
Edits are cheap and validated instantly (pure-python timeline model); rendering happens on demand
(get_still / get_contact_sheet / render_preview / export), always from the full edit list.

Run (stdio):  .venv/bin/python server.py          [MLT_EDITOR_HOME=<project dir>]
Needs the apt binding (python3-mlt) -> use the venv built with --system-site-packages on python3.12.
"""
import contextlib, fcntl, io, json, os, subprocess, sys, time

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
from . import assets as assets_lib  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP(S.server_name)

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


@contextlib.contextmanager
def locked():
    """Exclusive cross-process lock around a read-modify-write of project.json (two server processes or a CLI
    sharing one project would otherwise lose each other's edits). Blocking, released on exit or process death."""
    with open(LOCK, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def load():
    if not os.path.exists(PROJECT):
        return json.loads(json.dumps(DEFAULT))
    try:
        with open(PROJECT) as f:
            st = json.load(f)
        assert isinstance(st, dict) and {"sources", "ops", "width", "height", "fps"} <= set(st)
        return st
    except (ValueError, AssertionError, OSError) as e:
        raise RuntimeError(f"project file {PROJECT} is unreadable ({e}); fix or delete it, or call new_project")


def save(st):
    tmp = f"{PROJECT}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
        f.flush(); os.fsync(f.fileno())               # a crash must leave the old file or the new one, never half
    os.replace(tmp, PROJECT)


def bind(st, scale=1.0):
    """Point the engine at this project's sources/resolution. scale<1 => proxy-resolution preview."""
    live.CLIPS = {k: v["path"] for k, v in st["sources"].items()}
    live.CLIP_LEN = {k: v["duration_s"] for k, v in st["sources"].items()}
    live.W = max(2, int(st["width"] * scale) // 2 * 2)
    live.H = max(2, int(st["height"] * scale) // 2 * 2)
    live.FPS = st["fps"]
    live.CACHE = os.path.join(HOME, "cache")
    live.THEME = themes.get(st.get("theme"))              # projects saved before templates existed have no theme: luxury, as always
    live.MOTION = bool(st.get("motion"))                  # opt-in: projects saved before it existed behave exactly as they did


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
        if len(st["ops"]) >= MAX_OPS:
            raise ValueError(f"the project already has {MAX_OPS} edits (the limit); export it or start a new project")
        _validate(st, op)
        st["ops"].append(op)
        save(st)
        return summary(st)


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
             "track": L["track"], **O.get_layer(L["kind"]).summary(L)}
        layers.append(d)
    out = {
        "template": live.THEME.name,
        **({"motion": True} if live.MOTION else {}),
        "duration_s": round(m["total"], 3),
        "entries": [{"index": i, "source": e["src"], "source_in_s": round(e["in"], 3),
                     "start_s": round(e["start"], 3), "end_s": round(e["start"] + e["dur"], 3)}
                    for i, e in enumerate(m["entries"])],
        "crossfades": [{"between": [a, a + 1], "dur_s": d} for a, d in sorted(m["xfades"].items())],
        "fade": m["fade"],
        "overlays": layers,
        "audio": [{"op": a["op"], "name": a["name"], "start_s": round(a["start"], 3), "end_s": round(a["start"] + a["dur_eff"], 3), "volume_db": a["vol"],
                   "loop": a["loop"], "ducked": len(a["duck"])} for a in m["audios"]],
        "warnings": m["warnings"] + _legibility(st),
        "op_count": len(st["ops"]),
    }
    credits = assets_lib.credit_lines(O.project_assets(st["ops"]))
    if credits:
        out["credits_required"] = credits
    if full:
        out["ops"] = [{"index": i, **({k: v for k, v in o.items() if k != "cues"}), **({"cues": len(o["cues"])} if "cues" in o else {})}
                      for i, o in enumerate(st["ops"])]
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
    mcp.run()


from . import tools  # noqa: E402
tools.install(sys.modules[__name__], mcp)


if __name__ == "__main__":
    main()
