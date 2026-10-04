"""Seeing and exporting: stills, contact sheet, preview, export."""
import json, os, re, subprocess, time

from .. import assets as assets_lib
from .. import engine as live
from .. import ops as O
from .. import server as sv
from ..config import S
from . import tool
from mcp.server.fastmcp import Image

# ------------------------------------------------------------------ tools: seeing + exporting (rendering)
_TRACTORS = {}          # key -> (tractor, model, total); tiny LRU of built timelines for stills (see _built)


_TRACTOR_SLOTS = 2      # stills (0.5x) and contact sheets (0.25x) alternate; more would pin many open 4K decoders


def _state_key(st, scale):
    """Everything a built timeline depends on: the edit list, the format, and the files behind it (path+mtime+size)."""
    files = []
    for p in sorted({v["path"] for v in st["sources"].values()} | set(O.project_files(st["ops"]))):
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
    sv.bind(st, scale)
    if not S.tractor_cache:
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
        r = subprocess.run(cmd, input=b"".join(f[2] for f in frames), capture_output=True, timeout=sv.SUBPROCESS_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffmpeg took more than {sv.SUBPROCESS_TIMEOUT}s to encode the still")
    if r.returncode:
        raise RuntimeError(r.stderr.decode()[-300:])
    return r.stdout


@tool
def get_still(time_s: float, full_res: bool = False) -> Image:
    """Render ONE frame of the current edit at `time_s` and return it as an image, so you can look at
    the result. Half resolution by default (faster); full_res=True renders at export size."""
    st = sv.load()
    sv.require_fresh(st)
    return Image(data=_png(_frames(st, [time_s], 1.0 if full_res else 0.5)), format="png")


@tool
def get_contact_sheet(count: int = 6) -> Image:
    """Render `count` (2-12) evenly spaced frames of the current edit as one contact-sheet image.
    The best single call to review the whole edit: you see cuts, dissolves, fades and overlays."""
    if not 2 <= count <= 12:
        raise ValueError("count must be between 2 and 12")
    st = sv.load()
    sv.require_fresh(st)
    sv.bind(st)
    total = live.layout(st["ops"])["total"] if st["ops"] else 0
    if total <= 0:
        raise ValueError("the timeline is empty; add_clip first")
    times = [round(i * (total - 1 / st["fps"]) / (count - 1), 3) for i in range(count)]
    cols = 3 if count % 3 == 0 or count > 4 else 2
    rows = -(-count // cols)
    frames = _frames(st, times, 0.25)
    frames += [(frames[0][0], frames[0][1], bytes(len(frames[0][2])))] * (cols * rows - count)   # black padding
    return Image(data=_png(frames, (cols, rows)), format="png")


@tool
def render_preview() -> dict:
    """Render the whole edit to a small half-resolution mp4 (with audio) for quick playback.
    Returns its path and how long rendering took."""
    st = sv.load()
    sv.require_fresh(st)
    if not st["ops"]:
        raise ValueError("the timeline is empty; add_clip first")
    sv.bind(st, 0.5)
    p, tr, m, total = live.build(st["ops"])
    out = os.path.join(sv.HOME, "preview.mp4")
    t0 = time.perf_counter()
    live.render(p, tr, out)
    return {"path": out, "duration_s": round(m["total"], 3), "size_kb": os.path.getsize(out) // 1024,
            "render_s": round(time.perf_counter() - t0, 2)}


@tool
def export(output_path: str, quality: str = "high", overwrite: bool = False, master: str = "") -> dict:
    """Export the final video: MLT composes the timeline at full project resolution and the ffmpeg CLI
    does the H.264/AAC encode. output_path must end in .mp4 or .mov. An existing file is NOT replaced unless
    overwrite=true. quality: 'high' (CRF 20, preset medium) or 'draft' (CRF 28, ultrafast).
    master: '' (default) or 'loudnorm' = normalise the sound to -16 LUFS integrated / -1.5 dB true peak. The result reports the loudness measured on the
    exported file (loudness_lufs, true_peak_db).
    Blocking; can take about as long as the video itself at 1080p."""
    if master not in ("", "loudnorm"):
        raise ValueError("master must be '' or 'loudnorm'")
    if quality not in ("high", "draft"):
        raise ValueError("quality must be 'high' or 'draft'")
    st = sv.load()
    sv.require_fresh(st)
    if not st["ops"]:
        raise ValueError("the timeline is empty; add_clip first")
    out = sv._safe_path(output_path, "export", must_exist=False)
    if not out.lower().endswith((".mp4", ".mov")):
        raise ValueError("output_path must end in .mp4 or .mov")
    if os.path.isdir(out):
        raise ValueError(f"output_path is a directory: {out}")
    if os.path.exists(out) and not overwrite:
        raise ValueError(f"{out} already exists; choose another name or pass overwrite=true")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    sv.bind(st, 1.0)
    p, tr, m, total = live.build(st["ops"])
    t0 = time.perf_counter()
    live.render(p, tr, out, *(("medium", 20, "160k") if quality == "high" else ("ultrafast", 28, "96k")), master=master)
    info = sv._probe(out)
    res = {"path": out, "duration_s": info["duration_s"], "resolution": f"{info['width']}x{info['height']}",
           "size_kb": os.path.getsize(out) // 1024, "render_s": round(time.perf_counter() - t0, 2), **_loudness(out)}
    credits = assets_lib.credit_lines(O.project_assets(st["ops"]))
    credit_path = os.path.splitext(out)[0] + ".credits.txt"
    if credits:                                              # CC-BY pieces must be credited: write the text next to the video
        with open(credit_path, "w", encoding="utf-8") as f:
            f.write("Music and sound credits\n\n" + "\n\n".join(credits) + "\n")
        res["credits_file"], res["credits_required"] = credit_path, credits
    elif os.path.exists(credit_path):                        # a stale file from an earlier export of this name would lie
        os.remove(credit_path)
    return res


def _loudness(path):
    """{'loudness_lufs', 'true_peak_db'} measured on an exported file with ebur128 ({} if it has no audible audio)."""
    r = subprocess.run(["ffmpeg", "-nostats", "-i", path, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True, timeout=120)
    tail = r.stderr.rsplit("Summary:", 1)[-1]
    i, pk = re.search(r"I:\s+(-?[\d.]+) LUFS", tail), re.search(r"Peak:\s+(-?[\d.]+) dBFS", tail)
    if not i or float(i.group(1)) < -69:                       # ebur128 floors silence at about -70 LUFS
        return {}
    return {"loudness_lufs": round(float(i.group(1)), 1), **({"true_peak_db": round(float(pk.group(1)), 1)} if pk else {})}
