#!/usr/bin/env python3
"""Frame-by-frame engine test: overlays must never darken/flash a frame and must leave no residue after they end.
Compares EVERY frame of a timeline-with-overlays against the same timeline without overlays.
Run: xvfb-run -a .venv/bin/python test_engine.py"""
import os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import live, mlt7

live.CLIPS = {k: os.path.join(HERE, "media", f"clip_{k.lower()}.mp4") for k in "ABC"}
live.CLIP_LEN = {"A": 6.0, "B": 5.0, "C": 4.0}
live.W, live.H, live.FPS = 320, 180, 25
live.CACHE = tempfile.mkdtemp(prefix="eng_cache_")
BASE = [{"op": "add", "src": "A"}, {"op": "add", "src": "B"}, {"op": "crossfade", "between": [0, 1], "dur": 1.0}]  # 10 s
OVER = ("pip", "text", "subtitles", "image", "graphic", "lower_third")
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
    "luxury stack: frame + vignette + title + lower third": BASE + [
        {"op": "graphic", "kind": "frame", "start": 0.0, "dur": 10.0}, {"op": "graphic", "kind": "vignette", "start": 0.0, "dur": 10.0},
        T("Gran Inauguración", 1.0, 2.0, style="luxury"), {"op": "lower_third", "title": "Señor Muñoz", "subtitle": "Director", "start": 4.0, "dur": 3.0}],
    "letterbox + noir/modern subtitles + luxury-italic title": BASE + [
        {"op": "graphic", "kind": "letterbox", "start": 1.0, "dur": 7.0, "fade": 0.0},
        cues([(2.0, 3.0), (3.0, 4.0)]) | {"style": "noir"}, T("Título", 5.0, 2.0, style="luxury-italic"), T("Moderno", 7.5, 1.0, style="modern")],
    "back-to-back graphics and lower thirds (no fade)": BASE + [
        {"op": "lower_third", "title": "Uno", "start": 1.0, "dur": 1.0, "fade": 0.0}, {"op": "lower_third", "title": "Dos", "start": 2.0, "dur": 1.0, "fade": 0.0, "align": "right"},
        {"op": "graphic", "kind": "frame", "start": 3.0, "dur": 2.0, "fade": 0.0}, {"op": "graphic", "kind": "frame", "start": 5.0, "dur": 2.0, "fade": 0.0}],
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

# =================== regressions found by the full code review ===================
import subprocess, textrender as TR
extra = 0
def chk(name, cond, detail=""):
    global bad, extra
    extra += 1; bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if not cond and detail else ""))
def layout_error(ops):
    try: live.layout(ops); return None
    except ValueError as e: return str(e)
TWO = [{"op": "add", "src": "A"}, {"op": "add", "src": "B"}]

e = layout_error(TWO + [{"op": "crossfade", "between": [0, 1], "dur": 0.01}])
chk("crossfade shorter than one frame is rejected (used to build a 400-frame timeline)", e and "shorter than one frame" in e, e)
e = layout_error([{"op": "add", "src": "A", "in": 0, "end": 0.01}])
chk("clip shorter than one frame is rejected (used to turn into the whole clip)", e and "shorter than one frame" in e, e)
e = layout_error([{"op": "add", "src": "A"}, {"op": "cut", "clip": 0, "at": 0.01}])
chk("cut that leaves a sub-frame piece is rejected", e and "sub-frame" in e, e)

def frames_of(ops):
    m = live.layout(ops); p, tr, mm, total = live.build(ops)       # build() also asserts model == MLT
    return m["total_f"], total
for label, dur, n in [("0.1 s x40", 0.1, 40), ("0.3 s x40", 0.3, 40), ("0.07 s x30", 0.07, 30), ("0.33 s x25", 0.33, 25)]:
    mf, tf = frames_of([{"op": "add", "src": "A", "in": 0, "end": dur} for _ in range(n)])
    chk(f"timeline model == MLT frame count with many odd-length clips ({label})", mf == tf, (mf, tf))
ops = [{"op": "add", "src": "A", "in": 0, "end": 0.33} for _ in range(10)] + [{"op": "crossfade", "between": [i, i + 1], "dur": 0.11} for i in range(9)]
mf, tf = frames_of(ops); chk("timeline model == MLT frame count with 9 odd crossfades", mf == tf, (mf, tf))
m = live.layout(ops); chk("seconds and frames agree (total_f / fps == total)", abs(m["total_f"] / live.FPS - m["total"]) < 1e-9)

# fade-in + fade-out == the whole timeline used to emit a repeated keyframe position and dip the level mid-video
fade_ops = [{"op": "add", "src": "A", "in": 0, "end": 3.5}, {"op": "add", "src": "B", "in": 0, "end": 3.5}, {"op": "fade", "in": 3.5, "out": 3.5}]
Af, _, tot = frames(fade_ops); Rf, _, _ = frames(fade_ops[:2])
ratio = lambda f: (sum(Af[f][::97]) / len(Af[f][::97])) / max(1e-6, sum(Rf[f][::97]) / len(Rf[f][::97]))
mid = [round(ratio(f), 2) for f in range(tot // 2 - 3, tot // 2 + 4)]
chk("fade in+out == timeline length: no brightness dip at the peak", min(mid) > 0.9, mid)

# ffmpeg failures must surface (render() used to 'succeed' silently) and leave nothing behind
tmpd = tempfile.mkdtemp(prefix="eng_render_")
p, tr, mm, tot = live.build([{"op": "add", "src": "A", "in": 0, "end": 1.0}])
bad_out = os.path.join(tmpd, "out.mp4"); os.makedirs(bad_out)
try: live.render(p, tr, bad_out); err = None
except RuntimeError as ex: err = str(ex)
chk("render() raises when ffmpeg cannot write the output", err and "ffmpeg failed" in err, err)
chk("...and leaves no FIFO behind", not os.path.exists(bad_out + ".nut"))
chk("...and does not delete the directory it was pointed at", os.path.isdir(bad_out))
p, tr, mm, tot = live.build([{"op": "add", "src": "A", "in": 0, "end": 1.0}])      # a timeline renders once: rebuild after the failure
good = os.path.join(tmpd, "ok.mp4"); live.render(p, tr, good)
chk("a freshly built timeline renders fine after a failed render", os.path.getsize(good) > 5000)
chk("...and leaves no FIFO behind either", not os.path.exists(good + ".nut"))

# text PNG cache must be consulted BEFORE rendering
cdir = tempfile.mkdtemp(prefix="eng_cache2_")
TR.render_text_png("Caché", 640, 360, "top", 0.07, None, False, cdir, True, "luxury")
calls = []; orig = TR.render_text_image
TR.render_text_image = lambda *a, **k: calls.append(1) or orig(*a, **k)
TR.render_text_png("Caché", 640, 360, "top", 0.07, None, False, cdir, True, "luxury")
TR.render_text_image = orig
chk("text PNG cache hit does not re-render", not calls, len(calls))

# layout() is called several times per edit: it must stay cheap with 300 cues
big = [{"op": "add", "src": "A"}, {"op": "subtitles", "cues": [{"start": i * 0.02, "end": i * 0.02 + 0.015, "text": f"Línea número {i}: ¿qué tal?"} for i in range(300)]}]
live.layout(big); t0 = time.perf_counter(); live.layout(big); dt = (time.perf_counter() - t0) * 1000
chk("layout() with 300 cues is fast (was ~1600 ms)", dt < 400, f"{dt:.0f} ms")

# a clip WITHOUT audio crossfaded with one WITH audio must export with an audio track
silent = os.path.join(tmpd, "silent.mp4")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", silent], check=True)
live.CLIPS["S"], live.CLIP_LEN["S"] = silent, 3.0
p, tr, mm, tot = live.build([{"op": "add", "src": "S"}, {"op": "add", "src": "A", "in": 0, "end": 3}, {"op": "crossfade", "between": [0, 1], "dur": 1.0}, {"op": "fade", "in": 0.3, "out": 0.5}])
mixed = os.path.join(tmpd, "mixed.mp4"); live.render(p, tr, mixed)
kinds = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", mixed], capture_output=True, text=True).stdout.split()
chk("silent clip + clip with audio + crossfade exports with video and audio", sorted(kinds) == ["audio", "video"], kinds)

# =================== overlay optimizations (crop to content, merge simultaneous decoration) ===================
STACK = BASE + [{"op": "fade", "in": 0.5, "out": 1.0},
                {"op": "graphic", "kind": "vignette", "start": 0, "dur": 10, "amount": 0.55}, {"op": "graphic", "kind": "frame", "start": 0, "dur": 10},
                T("Gran Inauguración", 1.0, 2.5, pos="top", size=0.075, style="luxury"),
                {"op": "lower_third", "title": "Señor Muñoz", "subtitle": "Director", "start": 4.0, "dur": 2.5},
                cues([(1.5, 3.0), (6.8, 9.0)]) | {"style": "champagne"}]
def stack_frames(crop, merge):
    os.environ["MLT_OPT_CROP"], os.environ["MLT_OPT_MERGE"] = crop, merge
    live._BBOX.clear(); live.CACHE = tempfile.mkdtemp(prefix="eng_opt_")
    p, tr, mm, total = live.build(STACK); out = []
    for f in range(0, total, 5):
        tr.seek(f); out.append(bytes(tr.get_frame().get_image(mlt7.mlt_image_rgb, 320, 180)))
    return out
ref_f = stack_frames("0", "0")
def worst(a_list, b_list):
    return max(max(abs(x - y) for x, y in zip(a[::7], b[::7])) for a, b in zip(a_list, b_list)), sum(max(abs(x - y) for x, y in zip(a[::7], b[::7])) > 6 for a, b in zip(a_list, b_list))
w, nb = worst(ref_f, stack_frames("1", "0"))
chk("crop-to-content is pixel-identical to full-frame overlays", w == 0, (w, nb))
w, nb = worst(ref_f, stack_frames("0", "1"))
chk("merging simultaneous decoration stays within tolerance (differs only on fade-ramp frames)", w <= 20 and nb <= 3, (w, nb))
w, nb = worst(ref_f, stack_frames("1", "1"))
chk("crop + merge together stay within tolerance (and MLT does not crash on the freed track)", w <= 20 and nb <= 3, (w, nb))
os.environ["MLT_OPT_CROP"] = os.environ["MLT_OPT_MERGE"] = "1"
mk = lambda kind, start, **k: {"kind": "graphic", "gk": kind, "params": {"amount": None}, "start": start, "dur": 10.0, "opacity": 1.0, "fade": 0.4, "track": 1, **k}
merged = live._merge_decor([mk("vignette", 0.0), mk("frame", 0.0)])
chk("_merge_decor merges two decorations with identical timing into one layer", len(merged) == 1 and merged[0]["gk"] == "merged" and len(merged[0]["params"]["parts"]) == 2, merged)
kept = live._merge_decor([mk("vignette", 0.0), mk("frame", 1.0)])
chk("_merge_decor does NOT merge decorations with different timing", len(kept) == 2, kept)
lt = [{"kind": "graphic", "gk": "lower_third", "params": {"title": "a"}, "start": 0.0, "dur": 3.0, "opacity": 1.0, "fade": 0.4, "track": 1} for _ in range(2)]
chk("lower thirds are never merged (they carry text)", len(live._merge_decor(lt)) == 2)
# the track left empty by a merge must not leave a hole in the multitrack
p, tr, mm, tot = live.build(STACK)
chk("a merge leaves contiguous MLT tracks (no segfault)", tot == live.layout(STACK)["total_f"])

print(f"\n{len(SCENARIOS)+1+extra-bad} passed, {bad} failed"); sys.exit(1 if bad else 0)
