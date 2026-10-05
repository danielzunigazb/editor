#!/usr/bin/env python3
"""The real-footage corpus the validation tasks run on. The clips are the user's own and git-ignored: this file only lists where they are and what ffprobe says
about them (written to corpus.json, which carries no media). `python tools/validation/corpus.py` refreshes it; `load()` returns it with absolute paths."""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CLIPS = {   # key -> (path relative to poc_mlt, what it is)
    "R1": ("media_real/real1.mp4", "phone, vertical, h264"),
    "R2": ("media_real/real2.mp4", "phone, vertical, low resolution, variable frame rate"),
    "R3": ("media_real/real3.mp4", "phone, vertical, HEVC, stereo"),
    "C": ("media_user/charla.mp4", "talk, 1080p30, two voices"),
    "K": ("media_user/clip.mp4", "1080p50, no audio"),
}
SRT = "media_user/charla.srt"


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height,avg_frame_rate,channels:format=duration,size", "-of", "json", path],
                       capture_output=True, text=True)
    d = json.loads(r.stdout)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    n, m = v["avg_frame_rate"].split("/")
    return {"duration_s": round(float(d["format"]["duration"]), 2), "width": v["width"], "height": v["height"], "fps": round(float(n) / float(m), 2),
            "codec": v["codec_name"], "audio": (a or {}).get("codec_name"), "size_mb": round(int(d["format"]["size"]) / 1e6, 1)}


def build():
    out = {k: {"path": p, "what": w, **probe(os.path.join(ROOT, p))} for k, (p, w) in CLIPS.items()}
    json.dump({"clips": out, "srt": SRT}, open(os.path.join(HERE, "corpus.json"), "w"), indent=1)
    return out


def load():
    d = json.load(open(os.path.join(HERE, "corpus.json")))
    for c in d["clips"].values():
        c["abs"] = os.path.join(ROOT, c["path"])
    d["srt_abs"] = os.path.join(ROOT, d["srt"])
    return d


if __name__ == "__main__":
    missing = [p for p, _ in CLIPS.values() if not os.path.exists(os.path.join(ROOT, p))]
    if missing:
        sys.exit(f"missing footage: {missing}")
    for k, v in build().items():
        print(k, v)
