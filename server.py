#!/usr/bin/env python3
"""MCP server: a non-linear video editor driven entirely by an LLM (no UI, no controls).

Engine: MLT (timeline + preview rendering). Final export: MLT composes, the ffmpeg CLI encodes.
Edits are cheap and validated instantly (pure-python timeline model); rendering happens on demand
(get_still / get_contact_sheet / render_preview / export), always from the full edit list.

Run (stdio):  .venv/bin/python server.py          [MLT_EDITOR_HOME=<project dir>]
Needs the apt binding (python3-mlt) -> use the venv built with --system-site-packages on python3.12.
"""
import contextlib, fcntl, hashlib, io, json, os, re, subprocess, sys, time

# ---- stdout hygiene: MLT/ffmpeg/LADSPA may print to fd 1, which would corrupt the stdio protocol.
_real = os.dup(1)
os.dup2(2, 1)
sys.stdout = io.TextIOWrapper(os.fdopen(_real, "wb", closefd=False), encoding="utf-8", write_through=True)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
HOME = os.environ.get("MLT_EDITOR_HOME", os.path.join(HERE, "out", "mcp"))
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
        proc = subprocess.Popen(["Xvfb", "-displayfd", str(w), "-screen", "0", "1280x720x24", "-nolisten", "tcp"],
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
import live  # noqa: E402  (engine: layout/build/render)
import themes  # noqa: E402
import transitions  # noqa: E402
import cards  # noqa: E402
import icons  # noqa: E402
import assets_lib  # noqa: E402
import graphics  # noqa: E402
import textrender  # noqa: E402
from mcp.server.fastmcp import FastMCP, Image  # noqa: E402

mcp = FastMCP("mlt-video-editor")

# ------------------------------------------------------------------ project state
DEFAULT = {"sources": {}, "ops": [], "width": 1280, "height": 720, "fps": 25, "theme": {"name": "luxury", "accent": None}}
LOCK = os.path.join(HOME, "project.lock")
MAX_OPS, MAX_SOURCES = 500, 50
SUBPROCESS_TIMEOUT = 60          # ffprobe / still-encode: a hung or hostile file must not freeze the server
ROOTS = [os.path.realpath(os.path.expanduser(r)) for r in os.environ.get("MLT_EDITOR_ROOTS", "").split(os.pathsep) if r]


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


OVERLAYS = ("pip", "text", "subtitles", "image", "graphic", "lower_third", "callout", "audio")


def _validate(st, op):
    """Check `op` against the whole timeline as it is NOW (st['ops'] included). Raises ValueError. Saves nothing."""
    bind(st)                                   # export resolution: text-fit is checked at full size
    before = live.layout(st["ops"])["total"]
    live.layout(st["ops"] + [op])
    live.check_new_op(op)
    if op["op"] in OVERLAYS and op["op"] != "subtitles" and op["start"] >= before - 1e-6:
        raise ValueError(f"{op['op']} starts at {op['start']:g}s but the timeline is only {before:g}s long "
                         f"(add the clips first, or start it earlier)")
    if op["op"] == "subtitles" and all(c["start"] >= before - 1e-6 for c in op["cues"]):
        raise ValueError(f"every subtitle starts after the end of the timeline ({before:g}s); add the clips first "
                         f"or check offset_s")


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


# ---- one builder per edit tool: (tool arguments) -> engine op. The tools and apply_ops share them, so a batch behaves
# exactly like the same calls made one by one.
def _b_add_clip(source, start_s=0.0, end_s=None):
    op = {"op": "add", "src": source, "in": start_s}
    if end_s is not None:
        op["end"] = end_s
    return op


def _b_cut_clip(index, at_s):
    return {"op": "cut", "clip": index, "at": at_s}


def _b_crossfade(first_index, dur_s=1.0, style="dissolve"):
    return {"op": "crossfade", "between": [first_index, first_index + 1], "dur": dur_s, **({"style": style} if style != "dissolve" else {})}


def _b_set_fades(fade_in_s=0.0, fade_out_s=0.0):
    return {"op": "fade", "in": fade_in_s, "out": fade_out_s}


def _b_add_pip(source, start_s, dur_s, position="top-right", scale=0.3, opacity=1.0, source_in_s=0.0, anim=None):
    return {"op": "pip", "src": source, "start": start_s, "dur": dur_s, "pos": position, "scale": scale,
            "opacity": opacity, "in": source_in_s, "anim": anim or None}


def _b_add_text(text, start_s, dur_s, position="bottom", size=0.06, style="auto", color="", box=None,
                uppercase=None, ornament="", fade_s=0.15, anim=None):
    return {"op": "text", "text": text, "start": start_s, "dur": dur_s, "pos": position, "size": size, "style": style,
            "color": color or None, "box": box, "uppercase": uppercase, "ornament": ornament or None, "fade": fade_s, "anim": anim or None}


def _b_add_subtitles(srt_path="", cues=None, offset_s=0.0, position="bottom", size=0.05, style="auto", color="",
                     box=None):
    if bool(srt_path) == bool(cues):
        raise ValueError("give exactly one of srt_path or cues")
    items = textrender.parse_srt(_safe_path(srt_path, "add_subtitles")) if srt_path else cues
    shifted = []
    for i, c in enumerate(items):
        if not isinstance(c, dict) or not {"start", "end", "text"} <= set(c):
            raise ValueError(f"cue {i} must be an object with start, end and text")
        if not all(isinstance(c[k], (int, float)) and not isinstance(c[k], bool) for k in ("start", "end")):
            raise ValueError(f"cue {i}: start and end must be numbers (seconds)")
        shifted.append({"start": c["start"] + offset_s, "end": c["end"] + offset_s, "text": c["text"]})
    return {"op": "subtitles", "cues": shifted, "pos": position, "size": size, "style": style,
            "color": color or None, "box": box, "fade": 0.0, "ornament": "none"}


def _theme_arg(theme):
    return None if theme in (None, "", "auto") else theme


def _b_add_graphic(kind, start_s, dur_s, amount=None, opacity=1.0, fade_s=0.5, theme="auto"):
    return {"op": "graphic", "kind": kind, "start": start_s, "dur": dur_s, "amount": amount, "opacity": opacity,
            "fade": fade_s, "theme": _theme_arg(theme)}


def _b_add_lower_third(title, subtitle="", start_s=0.0, dur_s=4.0, align="left", fade_s=0.4, theme="auto", anim=None):
    return {"op": "lower_third", "title": title, "subtitle": subtitle, "start": start_s, "dur": dur_s, "align": align,
            "fade": fade_s, "theme": _theme_arg(theme), "anim": anim or None}


def _b_add_image(start_s, dur_s, path="", position="center", scale=0.3, opacity=1.0, icon="", color="", at=None, plate=None, theme="auto", anim=None):
    op = {"op": "image", "path": _safe_path(path, "add_image") if path else "", "start": start_s, "dur": dur_s,
          "pos": position, "scale": scale, "opacity": opacity, "icon": icon or "", "color": color or None, "at": at, "theme": _theme_arg(theme), "anim": anim or None}
    if plate is not None:
        op["plate"] = plate
    return op


def _b_add_callout(title, track, subtitle="", start_s=None, dur_s=None, side="auto", fade_s=0.3, theme="auto"):
    if not isinstance(track, list) or not track or not all(isinstance(p, (list, tuple)) and len(p) == 3 for p in track):
        raise ValueError("track must be a list of [t_s, x, y] points (timeline seconds; x, y = fractions 0-1 of the frame)")
    t0 = track[0][0]
    start = t0 if start_s is None else start_s
    dur = (track[-1][0] - start if dur_s is None else dur_s)
    if dur_s is None and dur <= 0:
        dur = 2.0                                          # a single point: show it for a couple of seconds
    return {"op": "callout", "title": title, "subtitle": subtitle, "path": [list(p) for p in track], "start": start,
            "dur": dur, "side": side, "fade": fade_s, "theme": _theme_arg(theme)}


def _probe_audio(path):
    """Duration (s) of the audio in `path` (an audio file, or a video with an audio track). ValueError if it has none."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path], capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise ValueError(f"ffprobe timed out reading '{path}'")
    if r.returncode:
        raise ValueError(f"ffprobe could not read '{path}': {r.stderr.strip() or 'unknown error'}")
    info = json.loads(r.stdout)
    if not any(s_.get("codec_type") == "audio" for s_ in info.get("streams", [])):
        raise ValueError(f"'{path}' has no audio stream")
    try:
        dur = float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"'{path}' has no readable duration")
    if dur < 0.1:
        raise ValueError(f"'{path}' is too short to use as audio ({dur:g}s)")
    return dur


def _b_add_audio(start_s=0.0, dur_s=None, path="", source_in_s=0.0, volume_db=-14.0, fade_in_s=None, fade_out_s=None, loop=False,
                 duck_under=None, duck_db=-12.0, asset=""):
    if bool(path) == bool(asset):
        raise ValueError("give exactly one of asset (an id from list_assets(kind='music' or 'sfx')) or path (an audio file, or a video with an audio track)")
    if asset:
        item = assets_lib.find(asset)
        p, name = assets_lib.path(asset), item["title"]                  # downloads from R2 on first use, SHA-256 checked
    else:
        p = _safe_path(path, "add_audio")
        name = os.path.basename(p)
    op = {"op": "audio", "path": p, "src_dur": _probe_audio(p), "start": start_s, "in": source_in_s, "dur": dur_s, "volume_db": volume_db,
          "fade_in": fade_in_s, "fade_out": fade_out_s, "loop": loop, "duck": duck_under or [], "duck_db": duck_db, "name": name}
    if asset:
        op["asset"] = asset
    return op


def _speech_intervals(st):
    """Timeline intervals [(start_s, end_s)] where the main track's own audio is not silent (speech), found with ffmpeg silencedetect on each
    entry. Used to duck music under dialogue. Sources without audio contribute nothing."""
    bind(st)
    m = live.layout(st["ops"])
    out = []
    for e in m["entries"]:
        path = st["sources"][e["src"]]["path"]
        if not st["sources"][e["src"]].get("has_audio"):
            continue
        r = subprocess.run(["ffmpeg", "-v", "info", "-ss", f"{e['in']:.3f}", "-t", f"{e['dur']:.3f}", "-i", path, "-vn", "-af", "silencedetect=noise=-35dB:d=0.5", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)
        sil, cur = [], None
        for ln in r.stderr.splitlines():
            a = re.search(r"silence_start: (-?[\d.]+)", ln)
            b = re.search(r"silence_end: (-?[\d.]+)", ln)
            if a:
                cur = max(0.0, float(a.group(1)))
            elif b and cur is not None:
                sil.append((cur, float(b.group(1)))); cur = None
        if cur is not None:
            sil.append((cur, e["dur"]))
        pos = 0.0
        for a, b in sil + [(e["dur"], e["dur"])]:
            if a - pos > 0.05:
                out.append((e["start"] + pos, e["start"] + min(a, e["dur"])))
            pos = max(pos, b)
    merged = []
    for a, b in sorted(out):                                    # join pauses shorter than 0.5 s, drop blips shorter than 0.4 s
        if merged and a - merged[-1][1] < 0.5:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in merged if b - a >= 0.4]


BUILDERS = {n[3:]: f for n, f in list(globals().items()) if n.startswith("_b_")}


def summary(st, full=False):
    """Timeline as JSON. Edit tools return the compact form (the model already knows the op it just sent);
    get_timeline/undo/remove_op return `full` with the numbered op list."""
    bind(st)
    m = live.layout(st["ops"])
    layers, seen = [], {}
    for L in m["layers"]:
        if L["kind"] == "text" and "sub" in L:                    # collapse a subtitle file into one line
            g = seen.get(L["op"])
            if g:
                g["cues"] += 1; g["end_s"] = round(max(g["end_s"], L["start"] + L["dur"]), 3); continue
            g = {"kind": "subtitles", "op": L["op"], "cues": 1, "start_s": round(L["start"], 3),
                 "end_s": round(L["start"] + L["dur"], 3), "track": L["track"]}
            seen[L["op"]] = g; layers.append(g); continue
        d = {"kind": L["kind"], "op": L["op"], "start_s": round(L["start"], 3), "end_s": round(L["start"] + L["dur"], 3),
             "track": L["track"]}
        if L["kind"] == "text":
            d["text"] = L["text"]
        elif L["kind"] == "image":
            d["image"] = L.get("icon") or os.path.basename(L["path"])
        elif L["kind"] == "graphic":
            d["graphic"] = L["gk"]
        elif L["kind"] == "callout":
            d["callout"] = L["title"]
        else:
            d["source"] = L["src"]
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
        "warnings": m["warnings"],
        "op_count": len(st["ops"]),
    }
    credits = assets_lib.credit_lines([o.get("asset") for o in st["ops"] if o.get("op") == "audio" and o.get("asset")])
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


# ------------------------------------------------------------------ tools: project / sources
@mcp.tool()
def new_project(width: int = 1280, height: int = 720, fps: int = 25, motion: bool = False) -> dict:
    """Start an empty project (discards the current timeline and imported sources).
    width/height/fps define the final export format; previews are rendered at half size.
    motion: switch on the template's own motion (default animations, transitions via style="auto"); off by default."""
    if not (64 <= width <= 7680 and 64 <= height <= 4320 and 1 <= fps <= 120):
        raise ValueError("width 64-7680, height 64-4320, fps 1-120")
    st = {**json.loads(json.dumps(DEFAULT)), "width": width, "height": height, "fps": fps, **({"motion": True} if motion else {})}
    with locked():
        save(st)
    return {"ok": True, "format": f"{width}x{height}@{fps}", **({"motion": True} if motion else {})}


@mcp.tool()
def import_clip(path: str, id: str = "") -> dict:
    """Register a video file as a source and return its id. Use the id in add_clip / add_pip.
    `id` is optional (letters/digits/_ ); default is S1, S2, ..."""
    path = _safe_path(path, "import_clip")
    if not os.path.isfile(path):
        raise ValueError(f"not a file: {path}")
    if id and not re.fullmatch(r"[A-Za-z0-9_]{1,32}", id):
        raise ValueError("id must be 1-32 letters, digits or underscore")
    info = _probe(path)                                     # slow (spawns ffprobe): done outside the lock
    with locked():
        st = load()
        sid = id or f"S{len(st['sources']) + 1}"
        if sid in st["sources"]:
            raise ValueError(f"source id '{sid}' already exists")
        if len(st["sources"]) >= MAX_SOURCES:
            raise ValueError(f"the project already has {MAX_SOURCES} sources (the limit)")
        st["sources"][sid] = {"path": path, **info}
        save(st)
    return {"id": sid, **info}


@mcp.tool()
def list_sources() -> dict:
    """List imported sources with duration, resolution and whether they have audio."""
    return load()["sources"]


# ------------------------------------------------------------------ tools: edits (instant, no rendering)
@mcp.tool()
def add_clip(source: str, start_s: float = 0.0, end_s: float | None = None) -> dict:
    """Append a clip (or the range start_s..end_s of it) to the end of the main track.
    Returns the updated timeline. The new entry's index is the last one."""
    return commit(_b_add_clip(source, start_s, end_s))


@mcp.tool()
def cut_clip(index: int, at_s: float) -> dict:
    """Cut timeline entry `index` at `at_s` seconds from the ENTRY's own start and drop everything after
    (the entry becomes `at_s` long). Later entries shift earlier."""
    return commit(_b_cut_clip(index, at_s))


@mcp.tool()
def crossfade(first_index: int, dur_s: float = 1.0, style: str = "dissolve") -> dict:
    """Transition (video) and crossfade (audio) between entry `first_index` and the next one. The two entries overlap by dur_s, so the
    timeline gets shorter by dur_s. style: dissolve | wipe-right|left|up|down | iris-out|in | blinds-v|h | diagonal | clock |
    slide-left|right|up|down (the new clip travels over the old one) | auto (the template's own, only when the project's motion is on).
    Anything but dissolve needs dur_s >= 0.2."""
    return commit(_b_crossfade(first_index, dur_s, style))


@mcp.tool()
def set_fades(fade_in_s: float = 0.0, fade_out_s: float = 0.0) -> dict:
    """Fade from black/silence at the start and to black/silence at the end of the whole timeline.
    Replaces any previous fade setting."""
    return commit(_b_set_fades(fade_in_s, fade_out_s))


@mcp.tool()
def add_pip(source: str, start_s: float, dur_s: float, position: str = "top-right",
            scale: float = 0.3, opacity: float = 1.0, source_in_s: float = 0.0, anim: dict | None = None) -> dict:
    """Overlay a picture-in-picture video on its own layer from start_s for dur_s (TIMELINE time).
    position: top-right | top-left | bottom-right | bottom-left. scale: fraction of frame width (0-1].
    opacity 0-1 (fades in/out at the edges). Calls accumulate (several PiPs are allowed, up to 6 overlays
    at the same moment). Overlays are placed in timeline seconds and do NOT move if you later edit earlier clips."""
    return commit(_b_add_pip(source, start_s, dur_s, position, scale, opacity, source_in_s, anim))


@mcp.tool()
def list_styles() -> dict:
    """Text styles available for add_text / add_subtitles, and the graphic kinds for add_graphic."""
    bind(load())
    return {"templates": themes.describe(), "current_template": live.THEME.name,
            "text_styles": {k: v["label"] for k, v in textrender.STYLES.items()},
            "default_title_style": live.THEME.title_style, "default_subtitle_style": live.THEME.subtitle_style,
            "graphics": {k: f"amount = {graphics.AMOUNT[k][0]}, {graphics.AMOUNT[k][1]}-{graphics.AMOUNT[k][2]} "
                            f"(default {graphics.AMOUNT[k][3]})" for k in graphics.KINDS},
            "lower_third": "name + role panel with a gold side bar (add_lower_third)", "transitions": list(transitions.STYLES)}


@mcp.tool()
def set_template(name: str, accent: str = "", motion: bool | None = None) -> dict:
    """Choose the project's design template: luxury | corporate | academic | sketch | tech | minimal | playful (see list_styles).
    Everything that does not name its own style (text, subtitles, lower thirds, labels, cards) follows it, so switching
    restyles the whole edit; edits are kept. accent: optional #RRGGBB brand colour replacing the template's signature colour.
    motion: true/false switches the template's own motion (default animations and the style="auto" transition) for the whole project; omit to keep it.
    Rejected, changing nothing, if an existing text would not fit in the new template's type."""
    spec = {"name": name, "accent": accent or None}
    themes.get(spec)                                       # validates name and colour
    note = None
    if name == "luxury" and accent:                        # the luxury look is a metallic gold gradient, not one colour
        spec["accent"], note = None, "luxury keeps its metallic gold; accent was ignored (it applies to the other templates)"
    with locked():
        st = load()
        st["theme"] = spec
        if motion is not None:
            st["motion"] = bool(motion)
        bind(st)
        for i, o in enumerate(st["ops"]):
            try:
                live.check_new_op(o)
            except ValueError as e:
                raise ValueError(f"op {i} would not fit in the '{name}' template: {e}; nothing was changed")
        live.layout(st["ops"])
        save(st)
        return {"template": name, "accent": live.THEME.accent, **({"note": note} if note else {}), **summary(st)}


@mcp.tool()
def add_card(layout: str, title: str = "", subtitle: str = "", items: list[str] | None = None, number: str = "",
             author: str = "", dur_s: float = 4.0, push: bool = False, append: bool = True, theme: str = "auto", animate: bool | None = None) -> dict:
    """Make a full-screen card in the project's template and (append=true) add it to the END of the main track.
    layout: title (title+subtitle) | section (number+title) | quote (title = the quote, author) | list (title + items, up to 5) |
    stat (number = the figure, title/subtitle = its label) | outro (title+subtitle) | bento (title + 1-4 items 'figure|label', e.g. '18 %|growth'). dur_s 0.5-30. push=true adds a slow zoom-in.
    animate: the card's element groups (title, rule, subtitle, list rows, tiles) arrive one after another in the template's own way; default = on
    when the project's motion is on (set_template/new_project motion), otherwise a still picture. The card also becomes a source (CARD1, CARD2...) usable with add_clip/crossfade. It is appended like any clip, so add an INTRO
    card before the clips and an OUTRO after them; dissolve into it with crossfade. Not available inside apply_ops."""
    if not 0.5 <= dur_s <= 30:
        raise ValueError("dur_s must be between 0.5 and 30 seconds")
    with locked():
        st = load()
        bind(st)
        th = themes.get(live._theme_key({"theme": theme}, "add_card"))
        if layout not in cards.LAYOUTS:
            raise ValueError(f"layout must be one of {cards.LAYOUTS}")
        W, H, fps = st["width"] // 2 * 2, st["height"] // 2 * 2, st["fps"]
        kw = dict(title=title, subtitle=subtitle, items=tuple(items or ()), number=number, author=author)
        os.makedirs(os.path.join(HOME, "cards"), exist_ok=True)
        if live.MOTION if animate is None else animate:
            bg_png, layer_pngs = cards.card_layers(os.path.join(HOME, "cache"), layout, W, H, th, **kw)
            key = hashlib.sha1(f"anim|{bg_png}|{len(layer_pngs)}|{fps}|{dur_s}|{push}|{th.motion.get('card')}".encode()).hexdigest()[:16]
            mp4 = cards.card_video_animated(bg_png, layer_pngs, os.path.join(HOME, "cards", f"card_{key}.mp4"), W, H, fps, dur_s, th, push)
        else:
            png = cards.card_png(os.path.join(HOME, "cache"), layout, W, H, th, **kw)
            key = hashlib.sha1(f"{png}|{fps}|{dur_s}|{push}".encode()).hexdigest()[:16]
            mp4 = cards.card_video(png, os.path.join(HOME, "cards", f"card_{key}.mp4"), W, H, fps, dur_s, push)
        sid = next((k for k, v in st["sources"].items() if v["path"] == mp4), None)
        if sid is None:
            if len(st["sources"]) >= MAX_SOURCES:
                raise ValueError(f"the project already has {MAX_SOURCES} sources (the limit)")
            n = 1
            while f"CARD{n}" in st["sources"]:
                n += 1
            sid = f"CARD{n}"
            st["sources"][sid] = {"path": mp4, **_probe(mp4)}
        if append:
            if len(st["ops"]) >= MAX_OPS:
                raise ValueError(f"the project already has {MAX_OPS} edits (the limit)")
            op = _b_add_clip(sid)
            _validate(st, op)
            st["ops"].append(op)
        save(st)
        return {"card": sid, "duration_s": st["sources"][sid]["duration_s"], "appended": append, **summary(st)}


@mcp.tool()
def animate(index: int, anim: dict | None = None) -> dict:
    """Animate an existing text, image/icon, picture-in-picture, lower third or graphic: `index` is its edit number (get_timeline; -1 = the last
    edit). anim=null removes the animation. The same `anim` object is accepted by add_text/add_image/add_lower_third/add_pip.
    anim = {"in": preset, "out": preset, "in_s": 0.5, "out_s": 0.4, "ease_in": e, "ease_out": e, "keys": [...], "keys_ease": e, "rotate": deg, "scale": k}
    presets: none | fade | slide-left | slide-right | slide-top | slide-bottom (the SIDE of the screen: in = comes from it, out = leaves toward it) | pop (grows with a bounce) | zoom | spin | drop (falls from the top and bounces).
    e: linear | in | out | inout | back | bounce. keys = free motion between entrance and exit, each {"t": seconds from the item's start,
    "x": 0-1, "y": 0-1 (centre of the item in the frame), "scale": 1 = normal, "rotate": degrees clockwise, "opacity": 0-1}; omitted fields hold.
    rotate/scale = constant tilt / size multiplier. Examples: {"in": "slide-left", "out": "fade"}; {"in": "pop", "rotate": -6};
    {"keys": [{"t": 0, "x": 0.2, "y": 0.5}, {"t": 2, "x": 0.8, "y": 0.5}]} (glide across). Not for callouts (they follow their own track)."""
    with locked():
        st = load()
        n = len(st["ops"])
        i = index + n if index < 0 else index
        if not 0 <= i < n:
            raise ValueError(f"no op {index} (have {n})")
        op = st["ops"][i]
        if op.get("op") not in ("text", "image", "pip", "graphic", "lower_third"):
            raise ValueError(f"op {i} is a '{op.get('op')}': only text, image, pip, graphic and lower_third can be animated")
        new = dict(op)
        if anim:
            new["anim"] = anim
        else:
            new.pop("anim", None)
        bind(st)
        live.layout(st["ops"][:i] + [new] + st["ops"][i + 1:])      # raises, with a message naming the op, if the animation is not valid there
        st["ops"][i] = new
        save(st)
        return {"animated": i, **summary(st)}


@mcp.tool()
def add_text(text: str, start_s: float, dur_s: float, position: str = "bottom", size: float = 0.06,
             style: str = "auto", color: str = "", box: bool | None = None, uppercase: bool | None = None,
             ornament: str = "", fade_s: float = 0.15, anim: dict | None = None) -> dict:
    """Show a title/caption from start_s for dur_s (TIMELINE time). Latin text with accents, ñ, ¿¡ is
    supported; use \\n for a line break. Long text is wrapped and shrunk to fit (max 4 lines, 200 chars);
    text that cannot fit, or characters the font lacks (CJK, newer emoji), are rejected with a message.
    style: "auto" (default) follows the project's template (set_template); or a name from list_styles
    (luxury, luxury-italic, champagne, noir, modern, classic, corp-*, acad-*, sketch-*, tech-*, min-*, kids-*).
    position: bottom | center | top. size: fraction of frame height (0.02-0.2).
    color: optional #RRGGBB; leave empty to keep the style's own colour (gold gradient for luxury).
    box: panel behind the text (default: the style decides). uppercase: force/forbid capitals (default per style).
    ornament: none | line | diamond (thin gold rule; default per style). All styles add a soft shadow for readability."""
    return commit(_b_add_text(text, start_s, dur_s, position, size, style, color, box, uppercase, ornament, fade_s, anim))


@mcp.tool()
def add_subtitles(srt_path: str = "", cues: list[dict] | None = None, offset_s: float = 0.0,
                  position: str = "bottom", size: float = 0.05, style: str = "auto", color: str = "",
                  box: bool | None = None) -> dict:
    """Add subtitles from an .srt file (srt_path) OR a list of cues [{"start":1.0,"end":2.5,"text":"Hola"}]
    (seconds, timeline time). Give exactly one. offset_s shifts every cue (positive = later).
    style: "auto" (default) = the template's subtitle style, or any name from list_styles.
    Same text rules as add_text (accents/ñ fine; up to 300 cues). Cues after the timeline end are
    dropped with a warning. Calling it again ADDS another subtitle track; use remove_op to replace."""
    return commit(_b_add_subtitles(srt_path, cues, offset_s, position, size, style, color, box))


@mcp.tool()
def add_graphic(kind: str, start_s: float, dur_s: float, amount: float | None = None, opacity: float = 1.0,
                fade_s: float = 0.5, theme: str = "auto") -> dict:
    """Add a luxury graphic overlay (drawn to match the video size) from start_s for dur_s (TIMELINE time).
    kind: frame (thin double gold keyline with diamonds) | letterbox (cinema bars with a gold hairline) |
    vignette (soft dark edges). amount (optional): frame inset 0.015-0.08 | letterbox bar height 0.04-0.25 |
    vignette strength 0.1-1. opacity 0-1; fade_s = fade in/out at the edges. `frame` follows the project's template (gold keyline, corner brackets, page rules, hand-drawn border, neon brackets, hairline, thick bubble border). theme: "auto" = the project's template, or name another one for this item only."""
    return commit(_b_add_graphic(kind, start_s, dur_s, amount, opacity, fade_s, theme))


@mcp.tool()
def add_lower_third(title: str, subtitle: str = "", start_s: float = 0.0, dur_s: float = 4.0,
                    align: str = "left", fade_s: float = 0.4, theme: str = "auto", anim: dict | None = None) -> dict:
    """Name/role caption panel at the bottom: gold side bar, title in metallic gold, subtitle in tracked ivory
    capitals (e.g. title "Señor Muñoz", subtitle "Director de Proyecto"). Single lines only (title max 60 chars,
    subtitle max 80). align: left | right. Shown from start_s for dur_s (TIMELINE time)."""
    return commit(_b_add_lower_third(title, subtitle, start_s, dur_s, align, fade_s, theme, anim))


@mcp.tool()
def add_audio(start_s: float = 0.0, dur_s: float | None = None, path: str = "", source_in_s: float = 0.0, volume_db: float = -14.0,
              fade_in_s: float | None = None, fade_out_s: float | None = None, loop: bool = False, duck_under: list[list[float]] | None = None,
              duck_auto: bool = False, duck_db: float = -12.0, asset: str = "") -> dict:
    """Add music or a sound effect (TIMELINE time) mixed under the video's own audio. path: an audio file (mp3/wav/ogg/m4a...) or a
    video with an audio track. start_s: when it begins. dur_s: how long (default: the whole file, or until the timeline ends).
    source_in_s: start inside the file. volume_db: -60..+6 (default -14, a music bed under speech; use -6..0 for effects).
    fade_in_s/fade_out_s: ramps at its ends (default 1 s in / 2 s out, shorter for short sounds; asking for more than fits is an error). loop=true repeats a short file to fill dur_s. Ducking (music dips while someone talks):
    duck_under=[[start_s, end_s], ...] in timeline seconds, or duck_auto=true to find the speech in the clips' own audio now
    (re-add the audio after changing the cut); duck_db is how much quieter (default -12). Up to 8 audio items."""
    spec = _b_add_audio(start_s, dur_s, path, source_in_s, volume_db, fade_in_s, fade_out_s, loop, duck_under, duck_db, asset)
    if duck_auto:
        with locked():
            spec["duck"] = [list(iv) for iv in _speech_intervals(load())] + [list(iv) for iv in (duck_under or [])]
    return commit(spec)


@mcp.tool()
def add_image(start_s: float, dur_s: float, path: str = "", position: str = "center", scale: float = 0.3,
              opacity: float = 1.0, icon: str = "", color: str = "", at: list[float] | None = None,
              plate: bool | None = None, theme: str = "auto", anim: dict | None = None) -> dict:
    """Show a picture from start_s for dur_s (TIMELINE time). Give EITHER `icon` (a name from list_assets(kind='icon'): about 100
    line icons plus hand-drawn doodle-arrow/star/circle/underline/burst/check/cross/heart) OR `path` (PNG/JPG/WebP with
    transparency kept, or a plain .svg; max 25 MB / 8000 px). position: center | top-right | top-left | bottom-right | bottom-left,
    or at=[x, y] to centre it on an exact point of the frame (fractions 0-1, 0,0 = top-left). scale: fraction of frame width (0-1].
    Icons are tinted with the template's colour (color=#RRGGBB to override) and sit on a round plate that keeps them legible
    (plate=false for the bare glyph). Pair an icon with add_callout/add_text to label things."""
    return commit(_b_add_image(start_s, dur_s, path, position, scale, opacity, icon, color, at, plate, theme, anim))


@mcp.tool()
def list_assets(kind: str = "icon", theme: str = "", mood: str = "", license: str = "", query: str = "") -> dict:
    """List bundled assets. kind: icon (names for add_image(icon=...)) | music (beds for add_audio(asset=...), 2-4 min) | sfx (short effects for
    add_audio(asset=..., volume_db -6..0)). Filters (music/sfx): theme (a template name: luxury, corporate, academic, sketch, tech, minimal,
    playful), mood (e.g. calming, bouncy, ding, whoosh, page-turn), license (CC0 | CC-BY), query (text in id/title). CC-BY pieces need a credit:
    the editor lists the required lines in get_timeline/export for you. Shows up to 40; narrow with filters."""
    if kind == "icon":
        names = [n for n in icons.list_icons() if query.lower() in n]
        return {"kind": kind, "count": len(names), "items": names}
    if kind not in ("music", "sfx"):
        raise ValueError("kind must be icon, music or sfx")
    items, total = assets_lib.listing(kind, theme, mood, license, query)
    return {"kind": kind, "count": total, "shown": len(items), "items": items}


@mcp.tool()
def add_callout(title: str, track: list[list[float]], subtitle: str = "", start_s: float | None = None,
                dur_s: float | None = None, side: str = "auto", fade_s: float = 0.3, theme: str = "auto") -> dict:
    """Pin a name label to a point of the picture: a gold ring on the exact spot, a thin staff and a glass flag with
    `title` (and optional `subtitle`). `track` = [[t_s, x, y], ...]: where the point is at each moment (t_s in TIMELINE
    seconds, increasing; x, y = fractions of the frame, 0,0 = top-left, 1,1 = bottom-right). With several points the ring
    glides linearly between them (use it to follow a moving object); one point = fixed. start_s/dur_s default to
    the first/last point (dur 2 s for one point). side: auto (flag placed so it stays on screen) | ne | nw | se | sw.
    Title max 40 characters, subtitle 60, single lines. Coordinates usually come from detect.py (an external object
    detector), not from guessing."""
    return commit(_b_add_callout(title, track, subtitle, start_s, dur_s, side, fade_s, theme))


@mcp.tool()
def apply_ops(ops: list[dict]) -> dict:
    """Apply several edits in ONE call (all or nothing). Each item is {"tool": "<edit tool name>", ...that tool's
    arguments}, e.g. [{"tool":"add_clip","source":"A","end_s":3}, {"tool":"add_clip","source":"B"},
    {"tool":"crossfade","first_index":0,"dur_s":0.5}, {"tool":"add_text","text":"Hola","start_s":0.5,"dur_s":2}].
    Allowed tools: add_clip, cut_clip, crossfade, set_fades, add_pip, add_text, add_subtitles, add_graphic,
    add_lower_third, add_image, add_callout, add_audio (import_clip, new_project, add_card and duck_auto are separate calls). Items are validated in order against
    the timeline as the previous items leave it; if ANY item is invalid nothing is applied and the error names the
    item. Up to 50 items. Returns the final timeline (check `warnings`: it flags overlays that may overlap on screen).
    Prefer this to many single calls: it is the same result with far fewer round trips."""
    if not isinstance(ops, list) or not 1 <= len(ops) <= 50:
        raise ValueError("ops must be a list of 1-50 items")
    with locked():
        return _apply_ops(ops)


def _apply_ops(ops):
    st = load()
    if len(st["ops"]) + len(ops) > MAX_OPS:
        raise ValueError(f"this batch would take the project past {MAX_OPS} edits (it has {len(st['ops'])})")
    for i, spec in enumerate(ops):
        if not isinstance(spec, dict) or not isinstance(spec.get("tool"), str):
            raise ValueError(f"item {i}: needs a 'tool' key naming an edit tool; nothing was applied")
        tool = spec["tool"]
        builder = BUILDERS.get(tool)
        if builder is None:
            raise ValueError(f"item {i}: unknown tool '{tool}'; allowed: {', '.join(sorted(BUILDERS))}; nothing was applied")
        try:
            op = builder(**{k: v for k, v in spec.items() if k != "tool"})
            _validate(st, op)
        except TypeError as e:
            import inspect
            raise ValueError(f"item {i} ({tool}): bad arguments ({e}); expected {tool}{inspect.signature(builder)}; "
                             f"nothing was applied")
        except ValueError as e:
            raise ValueError(f"item {i} ({tool}): {e}; nothing was applied")
        st["ops"].append(op)                       # in memory only until every item has passed
    save(st)
    return {"applied": len(ops), **summary(st)}


@mcp.tool()
def get_timeline() -> dict:
    """Current timeline: entries with start/end times, crossfades, fades, overlays (pip/text/subtitles/image, with their
    track), warnings (overlays trimmed or hidden by later cuts) and the full op list
    (each op has an index usable with remove_op)."""
    return summary(load(), full=True)


@mcp.tool()
def undo() -> dict:
    """Remove the last edit."""
    with locked():
        st = load()
        if not st["ops"]:
            raise ValueError("nothing to undo")
        removed = st["ops"].pop()
        save(st)
        return {"removed": removed, **summary(st, full=True)}


@mcp.tool()
def remove_op(index: int) -> dict:
    """Remove edit number `index` (see get_timeline). Rejected, with nothing changed, if later edits
    depend on it (e.g. removing an add_clip that a later cut refers to)."""
    with locked():
        st = load()
        if not 0 <= index < len(st["ops"]):
            raise ValueError(f"no op {index} (have {len(st['ops'])})")
        bind(st)
        rest = st["ops"][:index] + st["ops"][index + 1:]
        live.layout(rest)          # raises if the remaining ops are no longer valid
        removed = st["ops"][index]
        st["ops"] = rest
        save(st)
        return {"removed": removed, **summary(st, full=True)}


# ------------------------------------------------------------------ tools: seeing + exporting (rendering)
_TRACTORS = {}          # key -> (tractor, model, total); tiny LRU of built timelines for stills (see _built)
_TRACTOR_SLOTS = 2      # stills (0.5x) and contact sheets (0.25x) alternate; more would pin many open 4K decoders


def _state_key(st, scale):
    """Everything a built timeline depends on: the edit list, the format, and the files behind it (path+mtime+size)."""
    files = []
    for p in sorted({v["path"] for v in st["sources"].values()} | {o["path"] for o in st["ops"] if o.get("op") in ("image", "audio") and o.get("path")}):
        try:
            stt = os.stat(p); files.append((p, stt.st_mtime_ns, stt.st_size))
        except OSError:
            files.append((p, None, None))
    return json.dumps([st["ops"], st["width"], st["height"], st["fps"], scale, files, st.get("theme"), bool(st.get("motion"))], sort_keys=True)


def _built(st, scale):
    """Build the timeline for stills, or reuse the one built by the previous call if nothing it depends on changed.
    Opening the producers is ~80% of a 4K build (2.1 s of 2.6 s), and the model usually looks at stills in bursts.
    Only for stills: a tractor that a consumer has rendered cannot be rendered again, so preview/export build their own.
    MLT_TRACTOR_CACHE=0 disables it."""
    bind(st, scale)
    if os.environ.get("MLT_TRACTOR_CACHE", "1") != "1":
        return live.build(st["ops"])
    key = _state_key(st, scale)
    hit = _TRACTORS.pop(key, None)
    if hit is None:
        hit = live.build(st["ops"])
    _TRACTORS[key] = hit                                    # (re)insert as most recent
    while len(_TRACTORS) > _TRACTOR_SLOTS:
        _TRACTORS.pop(next(iter(_TRACTORS)))
    return hit


def _frames(st, times, scale):
    """Render stills at the given timeline times with one engine build. Returns [(w, h, rgb_bytes)]."""
    import mlt7
    if not st["ops"]:
        raise ValueError("the timeline is empty; add_clip first")
    p, tr, m, total = _built(st, scale)
    out = []
    for t in times:
        if not 0 <= t <= m["total"]:
            raise ValueError(f"time {t:g}s is outside the timeline (0-{m['total']:g}s)")
        tr.seek(min(int(round(t * live.FPS)), total - 1))
        img = tr.get_frame().get_image(mlt7.mlt_image_rgb, live.W, live.H)
        out.append((live.W, live.H, bytes(img)))
    return out


def _png(frames, tile=None):
    w, h, _ = frames[0]
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-framerate", "1", "-i", "-"]
    if tile:
        cmd += ["-vf", f"tile={tile[0]}x{tile[1]}:padding=4:color=black", "-frames:v", "1"]
    else:
        cmd += ["-frames:v", "1"]
    cmd += ["-f", "image2pipe", "-vcodec", "png", "-"]
    try:
        r = subprocess.run(cmd, input=b"".join(f[2] for f in frames), capture_output=True, timeout=SUBPROCESS_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffmpeg took more than {SUBPROCESS_TIMEOUT}s to encode the still")
    if r.returncode:
        raise RuntimeError(r.stderr.decode()[-300:])
    return r.stdout


@mcp.tool()
def get_still(time_s: float, full_res: bool = False) -> Image:
    """Render ONE frame of the current edit at `time_s` and return it as an image, so you can look at
    the result. Half resolution by default (faster); full_res=True renders at export size."""
    st = load()
    return Image(data=_png(_frames(st, [time_s], 1.0 if full_res else 0.5)), format="png")


@mcp.tool()
def get_contact_sheet(count: int = 6) -> Image:
    """Render `count` (2-12) evenly spaced frames of the current edit as one contact-sheet image.
    The best single call to review the whole edit: you see cuts, dissolves, fades and overlays."""
    if not 2 <= count <= 12:
        raise ValueError("count must be between 2 and 12")
    st = load()
    bind(st)
    total = live.layout(st["ops"])["total"] if st["ops"] else 0
    if total <= 0:
        raise ValueError("the timeline is empty; add_clip first")
    times = [round(i * (total - 1 / st["fps"]) / (count - 1), 3) for i in range(count)]
    cols = 3 if count % 3 == 0 or count > 4 else 2
    rows = -(-count // cols)
    frames = _frames(st, times, 0.25)
    frames += [(frames[0][0], frames[0][1], bytes(len(frames[0][2])))] * (cols * rows - count)   # black padding
    return Image(data=_png(frames, (cols, rows)), format="png")


@mcp.tool()
def render_preview() -> dict:
    """Render the whole edit to a small half-resolution mp4 (with audio) for quick playback.
    Returns its path and how long rendering took."""
    st = load()
    if not st["ops"]:
        raise ValueError("the timeline is empty; add_clip first")
    bind(st, 0.5)
    p, tr, m, total = live.build(st["ops"])
    out = os.path.join(HOME, "preview.mp4")
    t0 = time.perf_counter()
    live.render(p, tr, out)
    return {"path": out, "duration_s": round(m["total"], 3), "size_kb": os.path.getsize(out) // 1024,
            "render_s": round(time.perf_counter() - t0, 2)}


@mcp.tool()
def export(output_path: str, quality: str = "high", overwrite: bool = False) -> dict:
    """Export the final video: MLT composes the timeline at full project resolution and the ffmpeg CLI
    does the H.264/AAC encode. output_path must end in .mp4 or .mov. An existing file is NOT replaced unless
    overwrite=true. quality: 'high' (CRF 20, preset medium) or 'draft' (CRF 28, ultrafast).
    Blocking; can take about as long as the video itself at 1080p."""
    if quality not in ("high", "draft"):
        raise ValueError("quality must be 'high' or 'draft'")
    st = load()
    if not st["ops"]:
        raise ValueError("the timeline is empty; add_clip first")
    out = _safe_path(output_path, "export", must_exist=False)
    if not out.lower().endswith((".mp4", ".mov")):
        raise ValueError("output_path must end in .mp4 or .mov")
    if os.path.isdir(out):
        raise ValueError(f"output_path is a directory: {out}")
    if os.path.exists(out) and not overwrite:
        raise ValueError(f"{out} already exists; choose another name or pass overwrite=true")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    bind(st, 1.0)
    p, tr, m, total = live.build(st["ops"])
    t0 = time.perf_counter()
    live.render(p, tr, out, *(("medium", 20, "160k") if quality == "high" else ("ultrafast", 28, "96k")))
    info = _probe(out)
    res = {"path": out, "duration_s": info["duration_s"], "resolution": f"{info['width']}x{info['height']}",
           "size_kb": os.path.getsize(out) // 1024, "render_s": round(time.perf_counter() - t0, 2)}
    credits = assets_lib.credit_lines([o.get("asset") for o in st["ops"] if o.get("op") == "audio" and o.get("asset")])
    credit_path = os.path.splitext(out)[0] + ".credits.txt"
    if credits:                                              # CC-BY pieces must be credited: write the text next to the video
        with open(credit_path, "w", encoding="utf-8") as f:
            f.write("Music and sound credits\n\n" + "\n\n".join(credits) + "\n")
        res["credits_file"], res["credits_required"] = credit_path, credits
    elif os.path.exists(credit_path):                        # a stale file from an earlier export of this name would lie
        os.remove(credit_path)
    return res


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


if __name__ == "__main__":
    prune_cache()
    mcp.run()
