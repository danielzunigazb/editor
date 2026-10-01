#!/usr/bin/env python3
"""MCP server: a non-linear video editor driven entirely by an LLM (no UI, no controls).

Engine: MLT (timeline + preview rendering). Final export: MLT composes, the ffmpeg CLI encodes.
Edits are cheap and validated instantly (pure-python timeline model); rendering happens on demand
(get_still / get_contact_sheet / render_preview / export), always from the full edit list.

Run (stdio):  .venv/bin/python server.py          [MLT_EDITOR_HOME=<project dir>]
Needs the apt binding (python3-mlt) -> use the venv built with --system-site-packages on python3.12.
"""
import io, json, os, re, shutil, subprocess, sys, tempfile, time

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
    proc = subprocess.Popen(["Xvfb", "-displayfd", str(w), "-screen", "0", "1280x720x24", "-nolisten", "tcp"],
                            pass_fds=[w], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(w)
    num = os.read(r, 16).decode().strip()
    if not num:
        raise RuntimeError("could not start Xvfb (needed by the qtblend transition); install xvfb or set DISPLAY")
    os.environ["DISPLAY"] = f":{num}"
    import atexit
    atexit.register(proc.terminate)


_ensure_display()
import live  # noqa: E402  (engine: layout/build/render)
from mcp.server.fastmcp import FastMCP, Image  # noqa: E402

mcp = FastMCP("mlt-video-editor")

# ------------------------------------------------------------------ project state
DEFAULT = {"sources": {}, "ops": [], "width": 1280, "height": 720, "fps": 25}


def load():
    if os.path.exists(PROJECT):
        return json.load(open(PROJECT))
    return json.loads(json.dumps(DEFAULT))


def save(st):
    tmp = PROJECT + ".tmp"
    json.dump(st, open(tmp, "w"), indent=1)
    os.replace(tmp, PROJECT)


def bind(st, scale=1.0):
    """Point the engine at this project's sources/resolution. scale<1 => proxy-resolution preview."""
    live.CLIPS = {k: v["path"] for k, v in st["sources"].items()}
    live.CLIP_LEN = {k: v["duration_s"] for k, v in st["sources"].items()}
    live.W = max(2, int(st["width"] * scale) // 2 * 2)
    live.H = max(2, int(st["height"] * scale) // 2 * 2)
    live.FPS = st["fps"]


def commit(st, op):
    """Validate the op against the whole timeline, then persist. Nothing is saved on error."""
    bind(st)
    live.layout(st["ops"] + [op])
    st["ops"].append(op)
    save(st)
    return summary(st)


def summary(st):
    bind(st)
    m = live.layout(st["ops"])
    return {
        "duration_s": round(m["total"], 3),
        "entries": [{"index": i, "source": e["src"], "source_in_s": round(e["in"], 3),
                     "start_s": round(e["start"], 3), "end_s": round(e["start"] + e["dur"], 3)}
                    for i, e in enumerate(m["entries"])],
        "crossfades": [{"between": [a, a + 1], "dur_s": d} for a, d in sorted(m["xfades"].items())],
        "fade": m["fade"],
        "pip": m["pip"],
        "ops": [{"index": i, **o} for i, o in enumerate(st["ops"])],
    }


def _probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                       capture_output=True, text=True)
    if r.returncode:
        raise ValueError(f"ffprobe could not read '{path}': {r.stderr.strip() or 'unknown error'}")
    info = json.loads(r.stdout)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if not v:
        raise ValueError(f"'{path}' has no video stream")
    return {"duration_s": round(float(info["format"]["duration"]), 3), "width": v["width"], "height": v["height"],
            "codec": v["codec_name"], "has_audio": any(s["codec_type"] == "audio" for s in info["streams"])}


# ------------------------------------------------------------------ tools: project / sources
@mcp.tool()
def new_project(width: int = 1280, height: int = 720, fps: int = 25) -> dict:
    """Start an empty project (discards the current timeline and imported sources).
    width/height/fps define the final export format; previews are rendered at half size."""
    if width < 64 or height < 64 or not 1 <= fps <= 120:
        raise ValueError("width/height must be >= 64 and fps in 1..120")
    st = {**json.loads(json.dumps(DEFAULT)), "width": width, "height": height, "fps": fps}
    save(st)
    return {"ok": True, "format": f"{width}x{height}@{fps}"}


@mcp.tool()
def import_clip(path: str, id: str = "") -> dict:
    """Register a video file as a source and return its id. Use the id in add_clip / add_pip.
    `id` is optional (letters/digits/_ ); default is S1, S2, ..."""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise ValueError(f"file not found: {path}")
    st = load()
    sid = id or f"S{len(st['sources']) + 1}"
    if not re.fullmatch(r"[A-Za-z0-9_]+", sid):
        raise ValueError("id must be letters, digits or underscore")
    if sid in st["sources"]:
        raise ValueError(f"source id '{sid}' already exists")
    info = _probe(path)
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
    op = {"op": "add", "src": source, "in": start_s}
    if end_s is not None:
        op["end"] = end_s
    return commit(load(), op)


@mcp.tool()
def cut_clip(index: int, at_s: float) -> dict:
    """Cut timeline entry `index` at `at_s` seconds from the ENTRY's own start and drop everything after
    (the entry becomes `at_s` long). Later entries shift earlier."""
    return commit(load(), {"op": "cut", "clip": index, "at": at_s})


@mcp.tool()
def crossfade(first_index: int, dur_s: float = 1.0) -> dict:
    """Dissolve (video) and crossfade (audio) between entry `first_index` and the next one.
    The two entries overlap by dur_s, so the timeline gets shorter by dur_s."""
    return commit(load(), {"op": "crossfade", "between": [first_index, first_index + 1], "dur": dur_s})


@mcp.tool()
def set_fades(fade_in_s: float = 0.0, fade_out_s: float = 0.0) -> dict:
    """Fade from black/silence at the start and to black/silence at the end of the whole timeline.
    Replaces any previous fade setting."""
    return commit(load(), {"op": "fade", "in": fade_in_s, "out": fade_out_s})


@mcp.tool()
def add_pip(source: str, start_s: float, dur_s: float, position: str = "top-right",
            scale: float = 0.3, opacity: float = 1.0, source_in_s: float = 0.0) -> dict:
    """Overlay a picture-in-picture on a second track from start_s for dur_s (timeline time).
    position: top-right | top-left | bottom-right | bottom-left. scale: fraction of frame width (0-1].
    opacity 0-1 (fades in/out at the edges). Only one PiP is supported in this version;
    a new one replaces the previous."""
    return commit(load(), {"op": "pip", "src": source, "start": start_s, "dur": dur_s, "pos": position,
                           "scale": scale, "opacity": opacity, "in": source_in_s})


@mcp.tool()
def get_timeline() -> dict:
    """Current timeline: entries with start/end times, crossfades, fades, PiP and the full op list
    (each op has an index usable with remove_op)."""
    return summary(load())


@mcp.tool()
def undo() -> dict:
    """Remove the last edit."""
    st = load()
    if not st["ops"]:
        raise ValueError("nothing to undo")
    removed = st["ops"].pop()
    save(st)
    return {"removed": removed, **summary(st)}


@mcp.tool()
def remove_op(index: int) -> dict:
    """Remove edit number `index` (see get_timeline). Rejected, with nothing changed, if later edits
    depend on it (e.g. removing an add_clip that a later cut refers to)."""
    st = load()
    if not 0 <= index < len(st["ops"]):
        raise ValueError(f"no op {index} (have {len(st['ops'])})")
    bind(st)
    rest = st["ops"][:index] + st["ops"][index + 1:]
    live.layout(rest)          # raises if the remaining ops are no longer valid
    removed = st["ops"][index]
    st["ops"] = rest
    save(st)
    return {"removed": removed, **summary(st)}


# ------------------------------------------------------------------ tools: seeing + exporting (rendering)
def _frames(st, times, scale):
    """Render stills at the given timeline times with one engine build. Returns [(w, h, rgb_bytes)]."""
    import mlt7
    if not st["ops"]:
        raise ValueError("the timeline is empty; add_clip first")
    bind(st, scale)
    p, tr, m, total = live.build(st["ops"])
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
    r = subprocess.run(cmd, input=b"".join(f[2] for f in frames), capture_output=True)
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
def export(output_path: str, quality: str = "high") -> dict:
    """Export the final video: MLT composes the timeline at full project resolution and the ffmpeg CLI
    does the H.264/AAC encode. quality: 'high' (CRF 20, preset medium) or 'draft' (CRF 28, ultrafast).
    Blocking; can take about as long as the video itself at 1080p."""
    if quality not in ("high", "draft"):
        raise ValueError("quality must be 'high' or 'draft'")
    st = load()
    if not st["ops"]:
        raise ValueError("the timeline is empty; add_clip first")
    out = os.path.abspath(os.path.expanduser(output_path))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    bind(st, 1.0)
    p, tr, m, total = live.build(st["ops"])
    t0 = time.perf_counter()
    live.render(p, tr, out, *(("medium", 20, "160k") if quality == "high" else ("ultrafast", 28, "96k")))
    info = _probe(out)
    return {"path": out, "duration_s": info["duration_s"], "resolution": f"{info['width']}x{info['height']}",
            "size_kb": os.path.getsize(out) // 1024, "render_s": round(time.perf_counter() - t0, 2)}


if __name__ == "__main__":
    mcp.run()
