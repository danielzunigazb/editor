"""Proxies: a small, intra-only copy of every large source, used for previews (stills, contact sheets, the preview mp4) so that seeking to any frame costs
one small decode instead of a long-GOP 4K one. Exports always read the originals.

A proxy is made in the background after import_clip (never blocking an edit), by ffmpeg:
  * scaled to `proxy_height` (default 540), H.264 with every frame a keyframe (-g 1): any frame decodes alone
  * the SAME frame rate and frame count as the original (no fps filter, timestamps passed through), so frame N of the proxy is frame N of the source
  * audio kept (AAC) so the preview mp4 has the clips' sound
It is written to a temporary name and renamed when complete: a proxy file either is whole or does not exist.
State is not stored in the project (it is a cache): `state()` derives it from the disk and the job table."""
import concurrent.futures, hashlib, os, subprocess, threading

from ..config import S

VERSION = 1
_POOL = {"ex": None}
_JOBS, _FAILED = {}, {}                       # proxy path -> Future / error text
_LOCK = threading.Lock()


def _dir(home):
    return os.path.join(home, "proxies")


def wanted(src):
    """Does this source need a proxy? Only if proxies are on and the source is taller than the proxy (a small file is already cheap to decode)."""
    return bool(S.proxy_enabled) and int(src.get("height") or 0) > int(S.proxy_height)


def path_for(home, src):
    """Where the proxy of a source lives: named by the file's identity (path, size, mtime) and the proxy settings, so a changed file never reuses it."""
    try:
        st = os.stat(src["path"])
        sig = [st.st_size, st.st_mtime_ns]                  # the file as it is NOW, not as it was recorded: a replaced file never shows an old proxy
    except OSError:
        sig = None
    key = f"v{VERSION}|{src['path']}|{sig}|{S.proxy_height}|{S.proxy_crf}"
    return os.path.join(_dir(home), hashlib.sha1(key.encode()).hexdigest()[:20] + ".mp4")


def state(home, src):
    """'ready' | 'pending' | 'failed' | 'none' (not needed or disabled)."""
    if not wanted(src):
        return "none"
    p = path_for(home, src)
    if os.path.exists(p):
        return "ready"
    with _LOCK:
        if p in _JOBS and not _JOBS[p].done():
            return "pending"
        if p in _FAILED:
            return "failed"
    return "pending" if p in _JOBS else "none"


def _make(src_path, out, height, crf):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = f"{out}.{os.getpid()}.part.mp4"
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", src_path, "-map", "0:v:0", "-map", "0:a:0?", "-vf", f"scale=-2:{height}:flags=bilinear",
           "-fps_mode", "passthrough", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-g", "1", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", tmp]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if r.returncode or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RuntimeError(r.stderr.strip()[-300:] or "ffmpeg failed")
    os.replace(tmp, out)


def ensure(home, src):
    """Start making the proxy of a source in the background if it is wanted and not there yet. Returns the state. Never blocks."""
    if not wanted(src):
        return "none"
    p = path_for(home, src)
    if os.path.exists(p):
        return "ready"
    with _LOCK:
        if p in _JOBS and not _JOBS[p].done():
            return "pending"
        if _POOL["ex"] is None:
            _POOL["ex"] = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(S.proxy_workers)), thread_name_prefix="proxy")
        _FAILED.pop(p, None)
        fut = _POOL["ex"].submit(_make, src["path"], p, int(S.proxy_height), int(S.proxy_crf))
        _JOBS[p] = fut

    def done(f):
        e = f.exception()
        if e is not None:
            with _LOCK:
                _FAILED[p] = str(e)
    fut.add_done_callback(done)
    return "pending"


def wait(home, srcs, timeout=60.0):
    """Wait (up to `timeout` s) for the proxies of these sources; returns {id: state}. For tests and for callers that prefer waiting to a slow original."""
    futs = []
    with _LOCK:
        for s in srcs.values():
            if wanted(s) and path_for(home, s) in _JOBS:
                futs.append(_JOBS[path_for(home, s)])
    concurrent.futures.wait(futs, timeout=timeout)
    return {k: state(home, s) for k, s in srcs.items()}


def media_for_preview(home, sources, scale):
    """{source id: path to open} for a preview at `scale`: the proxy where one is ready and the preview is smaller than full size, else the original."""
    out = {}
    for k, s in sources.items():
        out[k] = path_for(home, s) if scale < 1.0 and state(home, s) == "ready" else s["path"]
    return out


def prune(home, max_mb):
    """Keep the proxy folder under max_mb: delete the least recently used proxies first (they are rebuilt on demand). Returns files removed."""
    d = _dir(home)
    if not os.path.isdir(d):
        return 0
    files = []
    for n in os.listdir(d):
        p = os.path.join(d, n)
        try:
            st = os.stat(p)
            if n.endswith(".part.mp4") or ".part.mp4" in n:
                if st.st_mtime < __import__("time").time() - 6 * 3600:
                    os.remove(p)
                continue
            files.append((max(st.st_atime, st.st_mtime), st.st_size, p))
        except OSError:
            pass
    files.sort()
    total, removed = sum(f[1] for f in files), 0
    for _, size, p in files:
        if total <= max_mb * 1_000_000:
            break
        try:
            os.remove(p); total -= size; removed += 1
        except OSError:
            pass
    return removed
