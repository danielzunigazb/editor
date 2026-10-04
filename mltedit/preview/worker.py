"""The preview worker: a separate process that renders live-viewer pieces with MLT, so Qt/MLT never run inside the server's HTTP threads.
Protocol: one JSON object per line on stdin -> one JSON line on stdout ({"id", "ok", "error"?, "seconds"}).
  {"id": n, "job": "segment", "st": <project>, "home": ..., "a": frame, "b": frame, "out": path}   video-only MPEG-TS of timeline frames [a, b)
  {"id": n, "job": "audio",   "st": <project>, "home": ..., "out_dir": dir}                      the whole timeline's sound, one AAC encode split into HLS parts"""
import json, os, sys, time, traceback

_proto = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")       # MLT/ffmpeg may write to fd 1: keep a private channel for the protocol
os.dup2(2, 1)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from mltedit import binding, engine as live  # noqa: E402
from mltedit.core.render import render  # noqa: E402
from mltedit.preview import segments  # noqa: E402


def _build(st, home):
    scale = segments.preview_scale(st)
    binding.bind(st, home, scale)
    return live.build(st["ops"])


def segment(job):
    st, a, b, out = job["st"], job["a"], job["b"], job["out"]
    p, tr, m, total = _build(st, job["home"])
    fps = live.FPS
    seg_f = max(1, round(__import__("mltedit.config", fromlist=["S"]).S.viewer_segment_s * fps))
    tr.set_in_and_out(a, b - 1)                              # frames a..b-1 of the timeline: exactly the frames the full render has there
    enc = ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-g", str(seg_f), "-keyint_min", str(seg_f), "-sc_threshold", "0", "-pix_fmt", "yuv420p",
           "-profile:v", "main", "-level", "4.0", "-bf", "0", "-output_ts_offset", f"{a / fps:.6f}", "-muxdelay", "0", "-muxpreload", "0", "-f", "mpegts"]
    tmp = out + ".part"
    render(p, tr, tmp, encode=enc, mlt_props={"an": "1"})
    os.replace(tmp, out)


def audio(job):
    st, out_dir = job["st"], job["out_dir"]
    p, tr, m, total = _build(st, job["home"])
    os.makedirs(out_dir, exist_ok=True)
    enc = ["-vn", "-c:a", "aac", "-b:a", "96k", "-muxdelay", "0", "-muxpreload", "0", "-f", "hls", "-hls_time", "2", "-hls_list_size", "0", "-hls_playlist_type", "vod",
           "-hls_segment_type", "mpegts", "-hls_segment_filename", os.path.join(out_dir, "aud%d.ts")]
    render(p, tr, os.path.join(out_dir, "audio.m3u8"), encode=enc, mlt_props={"vn": "1"})


def plan(job):
    """The segment plan of a project (and what the page shows besides the video), as JSON-able data."""
    st = job["st"]
    pl = segments.plan(st, job["home"])
    m = pl.pop("layout")
    return {"plan": pl, "svg": live.svg_timeline(m), "warnings": m["warnings"], "duration": m["total"]}


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        job = json.loads(line)
        t0 = time.perf_counter()
        try:
            res = {"segment": segment, "audio": audio, "plan": plan}[job["job"]](job)
            reply = {"id": job["id"], "ok": True, "seconds": round(time.perf_counter() - t0, 3), **({"result": res} if res is not None else {})}
        except Exception as e:  # noqa: BLE001
            reply = {"id": job["id"], "ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-800:]}
        _proto.write(json.dumps(reply) + "\n")


if __name__ == "__main__":
    main()
