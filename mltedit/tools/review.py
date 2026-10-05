"""Seeing and exporting: stills, contact sheet, preview, export."""
import hashlib, json, os, re, subprocess, time

from .. import assets as assets_lib
from .. import engine as live
from .. import jobs, qa, stillqa
from .. import ops as O
from .. import project as P
from .. import server as sv
from ..config import S
from . import tool
from mcp.server.fastmcp import Image

# ------------------------------------------------------------------ tools: seeing + exporting (rendering)
_TRACTORS = {}          # key -> (tractor, model, total); tiny LRU of built timelines for stills (see _built)


_TRACTOR_SLOTS = 2      # stills (0.5x) and contact sheets (0.25x) alternate; more would pin many open 4K decoders


def _state_key(st, scale):
    """Everything a rendered picture depends on: the project (layout_hash: ops, format, template, sources), the files as they are on disk now
    (path+mtime+size), the preview scale, and which sources are read from their proxy."""
    files = []
    for p in sorted({v["path"] for v in st["sources"].values()} | set(O.project_files(st["ops"]))):
        try:
            stt = os.stat(p); files.append((p, stt.st_mtime_ns, stt.st_size))
        except OSError:
            files.append((p, None, None))
    used = sv.proxies.media_for_preview(sv.HOME, st["sources"], scale)
    return json.dumps([P.layout_hash(st), scale, files, sorted(k for k, v in used.items() if v != st["sources"][k]["path"])], sort_keys=True)


def _cached(st, scale, what):
    """Path of the cached PNG for this picture (what = the frame or the sheet description); a hit means no MLT at all."""
    key = hashlib.sha1((_state_key(st, scale) + f"|{what}|v1").encode()).hexdigest()[:20]
    return os.path.join(sv.HOME, "cache", f"still_{key}.png")


def _serve(path, make):
    """The cached PNG at `path`, made by make() (bytes) on a miss, written atomically."""
    if os.path.exists(path):
        os.utime(path)                                      # most recently used: survives pruning longest
        with open(path, "rb") as f:
            return f.read()
    data = make()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return data


def _serve_noted(path, make):
    """_serve for a picture that also has notes (the still QA): make() -> (png bytes, [notes]); the notes sit in a .json next to the cached PNG, so a cache
    hit costs no rendering and still answers with them. Returns (png bytes, notes)."""
    side = path[:-4] + ".qa.json"
    if os.path.exists(path) and os.path.exists(side):
        try:
            with open(side) as f:
                return _serve(path, lambda: b""), json.load(f)
        except (OSError, ValueError):
            pass
    data, found = make()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    with open(side, "w") as f:
        json.dump(found, f)
    return data, found


def _with_notes(img, found):
    """The image alone when nothing is wrong (no tokens spent on 'all good'); otherwise [image, text]."""
    if not found:
        return img
    return [img, "still QA (pixel checks only: black / blown-out frame, low-contrast text; it does not see text over a face or the subject): " + " | ".join(found)]


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
def get_still(time_s: float, full_res: bool = False):
    """Render ONE frame of the current edit at `time_s` and return it as an image, so you can look at
    the result. Half resolution by default (faster); full_res=True renders at export size. If a pixel check finds something (a black or blown-out frame,
    text with almost no contrast against its surroundings) a line of text follows the image; no text means none of those was found."""
    st = sv.load()
    sv.require_fresh(st)
    sv.ensure_proxies(st, wait=True)
    scale = 1.0 if full_res else 0.5
    sv.bind(st)
    t_f = live.CTX.fr(time_s) if isinstance(time_s, (int, float)) and time_s == time_s and abs(time_s) < 1e7 else None
    path = _cached(st, scale, f"still|{t_f}") if t_f is not None else None

    def make():
        frames = _frames(st, [time_s], scale)
        return _png(frames), stillqa.notes(frames[0], time_s, _built(st, scale)[2]["layers"])
    if path is None:
        data, found = make()
    else:
        data, found = _serve_noted(path, make)
    return _with_notes(Image(data=data, format="png"), found)


@tool
def get_contact_sheet(count: int = 6):
    """Render `count` (2-12) evenly spaced frames of the current edit as one contact-sheet image.
    The best single call to review the whole edit: you see cuts, dissolves, fades and overlays."""
    if not 2 <= count <= 12:
        raise ValueError("count must be between 2 and 12")
    st = sv.load()
    sv.require_fresh(st)
    sv.ensure_proxies(st, wait=True)
    sv.bind(st)
    total = live.layout(st["ops"])["total"] if st["ops"] else 0
    if total <= 0:
        raise ValueError("the timeline is empty; add_clip first")
    times = [round(i * (total - 1 / st["fps"]) / (count - 1), 3) for i in range(count)]
    cols = 3 if count % 3 == 0 or count > 4 else 2
    rows = -(-count // cols)

    def make():
        frames = _frames(st, times, 0.25)
        layers = _built(st, 0.25)[2]["layers"]
        found = [n for t, f in zip(times, frames) for n in stillqa.notes(f, t, layers)]
        frames += [(frames[0][0], frames[0][1], bytes(len(frames[0][2])))] * (cols * rows - count)   # black padding
        return _png(frames, (cols, rows)), found
    data, found = _serve_noted(_cached(st, 0.25, f"sheet|{count}"), make)
    return _with_notes(Image(data=data, format="png"), found)


def _qa(path, m, st=None, sync=False):
    """Watch the rendered file for what a viewer would notice (unexpected hard cuts, flashes, frozen stretches) and say so: the model only sees stills, so
    the render checks itself. A hard cut where two clips meet WITHOUT a crossfade is what the edit asked for, not a finding. sync=True (exports): also
    compare the sound of the first clip that has some with its source and report av_offset_ms."""
    try:
        joined = {i for i in m["xfades"]}
        expected = [e["start"] for i, e in enumerate(m["entries"]) if i > 0 and (i - 1) not in joined]
        srcs = (st or {}).get("sources", {})
        entries = [{"path": srcs[e["src"]]["path"], "in": e["in"], "start": e["start"], "dur": e["dur"], "has_audio": srcs[e["src"]].get("has_audio", False)}
                   for e in m["entries"] if e["src"] in srcs] if st else None
        res = qa.check(path, expected, entries=entries)
        if sync and entries:
            found = qa.av_offset(path, entries)
            res.update(found)
            off = found.get("av_offset_ms", 0)
            if abs(off) > S.av_tolerance_ms:
                res["findings"].append(f"audio is {abs(off)} ms {'late' if off > 0 else 'early'} against the picture (tolerance {S.av_tolerance_ms:g} ms)")
                res["ok"] = False
        return res
    except (SystemExit, OSError, ValueError) as e:
        return {"ok": None, "findings": [f"QA could not read the file: {e}"]}


def do_preview(st, progress=None):
    """Render the whole edit at half size to HOME/preview.mp4 (runs in this process or in a job)."""
    sv.bind(st, 0.5)
    p, tr, m, total = live.build(st["ops"])
    out = os.path.join(sv.HOME, "preview.mp4")
    t0 = time.perf_counter()
    live.render(p, tr, out, progress=progress)
    return {"path": out, "duration_s": round(m["total"], 3), "size_kb": os.path.getsize(out) // 1024, "render_s": round(time.perf_counter() - t0, 2), "qa": _qa(out, m, st)}


@tool
def render_preview(background: bool = False) -> dict:
    """Render the whole edit to a small half-resolution mp4 (with audio) for quick playback.
    Returns its path, how long rendering took, and `qa`: the rendered file watched for unexpected hard cuts and one-frame flashes
    (`qa.findings` is empty when it looks right; still stretches are listed as `qa.notes`). background=true: start it as a job and answer at once with a job_id (see job_status, cancel_job)."""
    st = sv.load()
    sv.require_fresh(st)
    if not st["ops"]:
        raise ValueError("the timeline is empty; add_clip first")
    total_s = _total_s(st)
    if background or total_s > S.block_max_s:
        return _as_job("preview", st, {"total_s": total_s * 1.0}, not background)
    return do_preview(st)


def _total_s(st):
    sv.bind(st, 1.0)
    return live.layout(st["ops"])["total"]


def _as_job(kind, st, args, forced):
    r = jobs.start(sv.HOME, kind, st, args)
    if forced:
        r["note"] = (f"the video is {args['total_s']:.0f} s long (over {S.block_max_s:g} s), so it runs as a job instead of blocking the call: "
                     f"job_status(job_id, wait_s=30) waits for it and reports percent/eta_s")
    return r


def check_export(output_path, quality, overwrite, master):
    """The checks of an export, done before anything is rendered (and before a job is started). Returns (project, output path)."""
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
    return st, out


def do_export(st, out, quality, master, progress=None):
    """Render the final video (runs in this process or in a job)."""
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    sv.bind(st, 1.0)
    p, tr, m, total = live.build(st["ops"])
    t0 = time.perf_counter()
    live.render(p, tr, out, *(("medium", 20, "160k") if quality == "high" else ("ultrafast", 28, "96k")), master=master, progress=progress)
    info = sv._probe(out)
    res = {"path": out, "duration_s": info["duration_s"], "resolution": f"{info['width']}x{info['height']}",
           "size_kb": os.path.getsize(out) // 1024, "render_s": round(time.perf_counter() - t0, 2), **_loudness(out), "qa": _qa(out, m, st, sync=True)}
    credits = assets_lib.credit_lines(O.project_assets(st["ops"]))
    credit_path = os.path.splitext(out)[0] + ".credits.txt"
    if credits:                                              # CC-BY pieces must be credited: write the text next to the video
        with open(credit_path, "w", encoding="utf-8") as f:
            f.write("Music and sound credits\n\n" + "\n\n".join(credits) + "\n")
        res["credits_file"], res["credits_required"] = credit_path, credits
    elif os.path.exists(credit_path):                        # a stale file from an earlier export of this name would lie
        os.remove(credit_path)
    return res


@tool
def export(output_path: str, quality: str = "high", overwrite: bool = False, master: str = "", background: bool = False) -> dict:
    """Export the final video: MLT composes the timeline at full project resolution and the ffmpeg CLI
    does the H.264/AAC encode. output_path must end in .mp4 or .mov. An existing file is NOT replaced unless
    overwrite=true. quality: 'high' (CRF 20, preset medium) or 'draft' (CRF 28, ultrafast).
    master: '' (default) or 'loudnorm' = normalise the sound to -16 LUFS integrated / -1.5 dB true peak. The result reports the loudness measured on the
    exported file (loudness_lufs, true_peak_db), and `qa`: the exported file watched for unexpected hard cuts and one-frame flashes
    (qa.ok / qa.findings, qa.notes for still stretches; a cut where two clips meet without a crossfade is intended and not reported).
    Blocking for short videos (up to about 40 s; about as long as the video itself at 1080p); longer ones, and background=true, run as a job (of the project
    as it is at this moment) and the answer is a job_id: job_status(job_id, wait_s=30) waits and shows percent/eta_s, cancel_job stops it."""
    st, out = check_export(output_path, quality, overwrite, master)
    total_s = _total_s(st)
    if background or total_s > S.block_max_s:
        return _as_job("export", st, {"out": out, "quality": quality, "master": master, "total_s": total_s}, not background)
    return do_export(st, out, quality, master)


@tool
def job_status(job_id: str, wait_s: float = 0.0) -> dict:
    """State of a background job (export or render_preview with background=true): running | done | failed | cancelled, how long it has run, `percent` and
    `eta_s` while running, and the result (the same fields the blocking call returns) when done, or the error when failed.
    wait_s: hold the call up to this many seconds (at most about 45) until the job ends - use it instead of polling in a loop."""
    return jobs.wait(sv.HOME, job_id, wait_s, S.job_wait_max_s)


@tool
def cancel_job(job_id: str) -> dict:
    """Stop a running background job; the half-written output file is removed. A finished job is left as it is."""
    return jobs.cancel(sv.HOME, job_id)


@tool
def list_jobs() -> dict:
    """The background jobs of this project (newest first) with their state."""
    return {"jobs": jobs.list_all(sv.HOME)}


def _loudness(path):
    """{'loudness_lufs', 'true_peak_db'} measured on an exported file with ebur128 ({} if it has no audible audio)."""
    r = subprocess.run(["ffmpeg", "-nostats", "-i", path, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True, timeout=120)
    tail = r.stderr.rsplit("Summary:", 1)[-1]
    i, pk = re.search(r"I:\s+(-?[\d.]+) LUFS", tail), re.search(r"Peak:\s+(-?[\d.]+) dBFS", tail)
    if not i or float(i.group(1)) < -69:                       # ebur128 floors silence at about -70 LUFS
        return {}
    return {"loudness_lufs": round(float(i.group(1)), 1), **({"true_peak_db": round(float(pk.group(1)), 1)} if pk else {})}
