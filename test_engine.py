#!/usr/bin/env python3
"""Frame-by-frame engine test: overlays must never darken/flash a frame and must leave no residue after they end.
Compares EVERY frame of a timeline-with-overlays against the same timeline without overlays.
Run: xvfb-run -a .venv/bin/python test_engine.py"""
import os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import live, mlt7

live.CLIPS = {k: os.path.join(HERE, "media", f"clip_{k.lower()}.mp4") for k in "ABC"}
live.CLIP_LEN = {"A": 6.0, "B": 5.0, "C": 4.0}
live.W, live.H, live.FPS = 320, 180, 25
live.CACHE = tempfile.mkdtemp(prefix="eng_cache_")
BASE = [{"op": "add", "src": "A"}, {"op": "add", "src": "B"}, {"op": "crossfade", "between": [0, 1], "dur": 1.0}]  # 10 s
OVER = ("pip", "text", "subtitles", "image")
from PIL import Image
png = os.path.join(live.CACHE, "mark.png"); Image.new("RGBA", (120, 60), (255, 0, 0, 255)).save(png)
T = lambda t, s, d, **k: {"op": "text", "text": t, "start": s, "dur": d, **k}
cues = lambda spec: {"op": "subtitles", "cues": [{"start": a, "end": b, "text": f"Línea {i}"} for i, (a, b) in enumerate(spec)]}

SCENARIOS = {
    "one text, fade 0.15": BASE + [T("Hola ñandú", 2.0, 2.0)],
    "text without fade": BASE + [T("Hola", 3.0, 1.0, fade=0.0)],
    "3 adjacent subtitle cues": BASE + [cues([(1.0, 2.0), (2.0, 3.0), (3.0, 4.0)])],
    "cues with 1-frame and 2-frame gaps": BASE + [cues([(1.0, 2.0), (2.04, 3.0), (3.08, 4.0)])],
    "cue at 0 and cue ending at the very end": BASE + [cues([(0.0, 1.0), (9.0, 10.0)])],
    "overlapping texts (3 tracks)": BASE + [T("uno", 1.0, 5.0, pos="top"), T("dos", 2.0, 3.0, pos="center"), T("tres", 2.5, 1.0)],
    "image whole video + text + pip": BASE + [{"op": "image", "path": png, "start": 0.0, "dur": 10.0, "pos": "top-left", "scale": 0.2},
                                              T("Título", 1.0, 2.0), {"op": "pip", "src": "C", "start": 4.0, "dur": 3.0}],
    "crossfade + fade-out + subtitles": BASE + [{"op": "fade", "in": 0.5, "out": 1.0}, cues([(2.0, 3.5), (4.0, 5.5), (8.5, 9.8)])],
    "40 dense cues": BASE + [cues([(round(i * 0.25, 2), round(i * 0.25 + 0.2, 2)) for i in range(40)])],
}

def frames(ops, w=320, h=180):
    p, tr, m, total = live.build(ops)
    out = []
    for f in range(total):
        tr.seek(f); out.append(bytes(tr.get_frame().get_image(mlt7.mlt_image_rgb, w, h)))
    return out, m, total

bad = 0
for name, ops in SCENARIOS.items():
    ref_ops = [o for o in ops if o["op"] not in OVER]
    A, m, total = frames(ops)
    R, _, rtotal = frames(ref_ops)
    covered = set()
    for L in m["layers"]:
        s0 = round(L["start"] * live.FPS); covered |= set(range(s0, s0 + max(1, round(L["dur"] * live.FPS)) + 2))
    flashes, residue = [], []
    for f in range(min(total, rtotal)):
        la = sum(A[f][::151]) / len(A[f][::151]); lr = sum(R[f][::151]) / len(R[f][::151])
        diff = sum(abs(a - b) for a, b in zip(A[f][::151], R[f][::151])) / len(A[f][::151])
        if lr > 20 and la < 0.7 * lr: flashes.append(f)                  # darker than the same frame without overlays
        if f not in covered and diff > 1.0: residue.append(f)           # overlay still visible where none should be
    ok = not flashes and not residue and total == rtotal
    bad += not ok
    print(("PASS " if ok else "FAIL ") + f"{name}: {total} frames, {len(m['layers'])} layers" +
          ("" if ok else f"  flashes={flashes[:8]} residue={residue[:8]} frames {total} vs {rtotal}"))

# ---- timing exactness: with fade=0 a subtitle is binary, so it must appear on exactly its first frame and vanish
# on exactly the frame after its last one (regression for Playlist.blank(out) creating out+1 frames => k-frame drift)
spec = [(1.0, 2.0), (2.04, 3.0), (3.08, 4.0), (5.0, 5.4), (6.0, 7.5), (8.0, 8.04)]
ops = BASE + [cues(spec)]
A, m, total = frames(ops); R, _, _ = frames(BASE)
def dif(f):  # number of clearly changed pixels (a sparse sample can miss short text)
    a, r = A[f], R[f]
    return sum(1 for i in range(0, len(a), 3) if abs(a[i] - r[i]) + abs(a[i+1] - r[i+1]) + abs(a[i+2] - r[i+2]) > 60)
active = set()
for L in m["layers"]:
    s0 = round(L["start"] * live.FPS); active |= set(range(s0, s0 + max(1, round(L["dur"] * live.FPS))))
wrong_on = [f for f in sorted(active) if dif(f) < 40]
wrong_off = [f for f in range(total) if f not in active and dif(f) >= 40]
ok = not wrong_on and not wrong_off
bad += not ok
print(("PASS " if ok else "FAIL ") + f"timing: {len(spec)} cues appear/disappear on the exact frame" +
      ("" if ok else f"  visible-expected-but-missing={wrong_on[:10]} visible-but-unexpected={wrong_off[:10]}"))
print(f"\n{len(SCENARIOS)+1-bad} passed, {bad} failed"); sys.exit(1 if bad else 0)
