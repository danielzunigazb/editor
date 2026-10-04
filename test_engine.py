#!/usr/bin/env python3
"""Frame-by-frame engine test: overlays must never darken/flash a frame and must leave no residue after they end.
Compares EVERY frame of a timeline-with-overlays against the same timeline without overlays.
Run: xvfb-run -a .venv/bin/python test_engine.py"""
import os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import live, mlt7, icons

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
import glob
chk("render() uses a private temp dir for its FIFO and removes it (failed and successful renders)", not glob.glob(os.path.join(tempfile.gettempdir(), "mltfifo_*")))

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

# ---- hardening: crop sidecar, layer cap, non-finite numbers
live.CACHE = tempfile.mkdtemp(prefix="eng_hard_"); live._BBOX.clear()
big = os.path.join(live.CACHE, "big.png"); im = Image.new("RGBA", (640, 360), (0, 0, 0, 0)); im.paste((255, 255, 255, 255), (100, 50, 180, 90)); im.save(big)
r1 = live._crop_to_content(big, 640, 360)
chk("crop writes a sidecar next to the cropped PNG", r1 is not None and os.path.exists(big[:-4] + "_crop.json"), r1)
live._BBOX.clear()
real_open = Image.open
def _no_decode(*a, **k): raise AssertionError("PNG was decoded although a valid sidecar exists")
Image.open = _no_decode
try: r2 = live._crop_to_content(big, 640, 360); ok = r2 == r1
except AssertionError as ex: ok = False; r2 = str(ex)
finally: Image.open = real_open
chk("a later process/rebuild reuses the sidecar without decoding the full-size PNG", ok, r2)
time.sleep(0.02); im.paste((255, 255, 255, 255), (300, 200, 400, 300)); im.save(big); live._BBOX.clear()      # the PNG changes -> sidecar is stale
r3 = live._crop_to_content(big, 640, 360)
chk("a changed PNG invalidates its sidecar (new bounding box)", r3 is not None and r3 != r1 and r3[3] > r1[3], (r1, r3))
full = os.path.join(live.CACHE, "full.png"); Image.new("RGBA", (64, 36), (255, 0, 0, 255)).save(full); live._BBOX.clear()
chk("a near-full-frame overlay is remembered as 'not worth cropping' (sidecar says null)", live._crop_to_content(full, 64, 36) is None and
    open(full[:-4] + "_crop.json").read() == "null")
live._BBOX.clear(); open(big[:-4] + "_crop.json", "w").write("{broken")
chk("a corrupt sidecar is ignored and rebuilt", live._crop_to_content(big, 640, 360) == r3)

for label, badv in [("NaN", float("nan")), ("inf", float("inf")), ("-inf", float("-inf"))]:
    try: live.layout(BASE + [T("x", badv, 1.0)]); e = None
    except ValueError as ex: e = str(ex)
    chk(f"layout rejects a {label} start with a clear message", e is not None and "finite" in e, e)
try: live.layout(BASE + [cues([(float("nan"), 1.0)])]); e = None
except ValueError as ex: e = str(ex)
chk("layout rejects NaN inside nested subtitle cues", e is not None and "finite" in e, e)
too_many = BASE + [{"op": "subtitles", "cues": [{"start": 0.0, "end": 0.4, "text": f"c{i}"} for i in range(1)]} for _ in range(live.MAX_LAYERS + 1)]
try: live.layout(too_many); e = None
except ValueError as ex: e = str(ex)
chk("layout rejects more than MAX_LAYERS overlays", e is not None and "limit" in e, e)

# ---- callout: a label pinned to an x,y that moves; the ring must land on the requested point
from PIL import ImageChops
import graphics
_W, _H = live.W, live.H
live.W, live.H = 1280, 720          # native size of the test clips: no rescaling noise in the pixel diff
live.CACHE = tempfile.mkdtemp(prefix="eng_callout_")
def _still(ops_, t_):
    p_, tr_, m_, tot_ = live.build(ops_); tr_.seek(int(round(t_ * live.FPS)))
    return Image.frombytes("RGB", (live.W, live.H), bytes(tr_.get_frame().get_image(mlt7.mlt_image_rgb, live.W, live.H)))
def _bbox(ops_, t_):
    return ImageChops.difference(_still(ops_, t_), _still(BASE[:1], t_)).convert("L").point(lambda v: 255 if v > 20 else 0).getbbox()
CO = {"op": "callout", "title": "Arco monumental", "subtitle": "Entrada", "path": [[1.0, 0.2, 0.5], [3.0, 0.6, 0.3]], "start": 0.5, "dur": 3.5, "side": "ne", "fade": 0.2}
png_, cw_, ch_, cax_, cay_ = graphics.render_callout(live.W, live.H, "Arco monumental", "Entrada", "ne", live.CACHE)
for t_ in (1.0, 2.0, 3.0):
    u_ = min(max((t_ - 1) / 2, 0), 1); px_, py_ = (0.2 + 0.4 * u_) * live.W, (0.5 - 0.2 * u_) * live.H
    exp_ = (px_ - cax_, py_ - cay_, px_ - cax_ + cw_, py_ - cay_ + ch_); bb_ = _bbox(BASE[:1] + [CO], t_)
    chk(f"callout box at t={t_:g}s sits where the path says (+-3 px)", bb_ is not None and all(abs(a_ - b_) <= 3 for a_, b_ in zip(bb_, exp_)), (bb_, exp_))
chk("callout leaves no residue after it ends", _bbox(BASE[:1] + [{**CO, "dur": 2.0}], 4.0) is None)
edge = {**CO, "path": [[1.0, 0.97, 0.5]], "side": "auto", "dur": 2.0}
bb_ = _bbox(BASE[:1] + [edge], 1.5)
chk("side=auto flips the flag to the left near the right edge (flag fully on screen, ring still on the point)",
    bb_ is not None and bb_[2] <= live.W and bb_[0] < 0.97 * live.W - 100, bb_)
top = {**CO, "path": [[1.0, 0.5, 0.03]], "side": "auto", "dur": 2.0}
bb_ = _bbox(BASE[:1] + [top], 1.5)
chk("side=auto puts the flag below a point near the top edge", bb_ is not None and bb_[1] <= 0.03 * live.H + 3 and bb_[3] > 0.03 * live.H + 20, bb_)
for label_, patch_, needle_ in [("an empty path", {"path": []}, "path"), ("x outside 0-1", {"path": [[1.0, 1.2, 0.5]]}, "between 0 and 1"),
                                ("times that do not increase", {"path": [[2.0, 0.2, 0.5], [1.0, 0.3, 0.5]]}, "increase"),
                                ("a point with 2 numbers", {"path": [[1.0, 0.2]]}, "[t_s, x, y]"), ("a long title", {"title": "x" * 41}, "title"),
                                ("a NaN coordinate", {"path": [[1.0, float("nan"), 0.5]]}, "finite"), ("an unknown side", {"side": "up"}, "side")]:
    try: live.layout(BASE + [{**CO, **patch_}]); e_ = None
    except ValueError as ex: e_ = str(ex)
    chk(f"callout rejects {label_}", e_ is not None and needle_ in e_, e_)
# ---- templates in the engine: the same callout path in every theme must land on the requested point
import themes
for n_ in [n for n in themes.NAMES if n != "luxury"]:
    live.THEME = themes.get(n_)
    png_t, cw_t, ch_t, cax_t, cay_t = graphics.render_callout(live.W, live.H, "Arco monumental", "Entrada", "ne", live.CACHE, {"name": n_, "accent": live.THEME.accent})
    vis_ = Image.open(png_t).convert("RGBA").getchannel("A").point(lambda v: 255 if v > 40 else 0).getbbox()      # the VISIBLE part (the PNG has transparent margins; glass is translucent, hence 40)
    co_t = {**CO, "path": [[1.0, 0.3, 0.6], [3.0, 0.6, 0.45]], "side": "ne"}
    for t_ in (1.0, 2.0, 3.0):
        u_ = min(max((t_ - 1) / 2, 0), 1); px_, py_ = (0.3 + 0.3 * u_) * live.W, (0.6 - 0.15 * u_) * live.H
        exp_ = (px_ - cax_t + vis_[0], py_ - cay_t + vis_[1], px_ - cax_t + vis_[2], py_ - cay_t + vis_[3])
        bb_ = _bbox(BASE[:1] + [co_t], t_)
        chk(f"template {n_}: callout drawing at t={t_:g}s sits where the path says (+-6 px)", bb_ is not None and all(abs(a_ - b_) <= 6 for a_, b_ in zip(bb_, exp_)), (bb_, exp_))
    lt_ = {"op": "lower_third", "title": "Señor Muñoz", "subtitle": "Director", "start": 0.5, "dur": 2.0}
    chk(f"template {n_}: a lower third leaves no residue after it ends", _bbox(BASE[:1] + [lt_], 3.5) is None)
    chk(f"template {n_}: a lower third is visible while it is on", _bbox(BASE[:1] + [lt_], 1.5) is not None)
live.THEME = themes.get(None)
try: live.layout(BASE + [{**CO, "theme": "neon-pink"}]); e_ = None
except ValueError as ex: e_ = str(ex)
chk("an op naming an unknown template is rejected", e_ is not None and "unknown template" in e_, e_)
lay_ = live.layout(BASE + [{**CO, "theme": "tech"}, {"op": "lower_third", "title": "Hola", "start": 0.5, "dur": 2.0, "theme": "sketch"}])
_tn = lambda L: (L.get("theme") or L["params"]["theme"])["name"]            # callouts carry it on the layer, graphics inside params
chk("an op can use another template than the project's (per-item theme)", sorted(_tn(L) for L in lay_["layers"]) == ["sketch", "tech"], [_tn(L) for L in lay_["layers"]])
live.THEME = themes.get("playful")
lay2_ = live.layout(BASE + [T("Hola", 1.0, 2.0), cues([(1.0, 2.0)])])
chk("style=auto follows the project's template (text -> title style, subtitles -> subtitle style)", [L["style"] for L in lay2_["layers"]] == ["kids-title", "kids-body"], [L["style"] for L in lay2_["layers"]])
chk("the template's panel default applies (kids-title has no default panel, subtitles keep theirs)", [L["box"] for L in lay2_["layers"]] == [False, True], [L["box"] for L in lay2_["layers"]])
live.THEME = themes.get("corporate")
chk("corporate titles come with their navy panel by default", live.layout(BASE + [T("Hola", 1.0, 2.0)])["layers"][0]["box"] is True)
live.THEME = themes.get(None)
# ---- icons in the engine: an icon centred on an exact point
live.W, live.H = 1280, 720
live.CACHE = tempfile.mkdtemp(prefix="eng_icons_")
for th_name in ("luxury", "sketch", "tech"):
    live.THEME = themes.get(th_name)
    for at_ in ([0.25, 0.4], [0.7, 0.65]):
        ic_ = {"op": "image", "icon": "rocket", "start": 0.5, "dur": 2.0, "scale": 0.12, "at": at_}
        lay_i = live.layout(BASE + [ic_])["layers"][-1]
        png_i = icons.render_layer(lay_i, live.W, live.CACHE)
        vis_i = Image.open(png_i).convert("RGBA").getchannel("A").point(lambda v: 255 if v > 70 else 0).getbbox()
        w_i = live.W * 0.12
        x0_i, y0_i = live.W * at_[0] - w_i / 2, live.H * at_[1] - w_i / 2
        exp_i = (x0_i + vis_i[0], y0_i + vis_i[1], x0_i + vis_i[2], y0_i + vis_i[3])
        bb_i = _bbox(BASE[:1] + [ic_], 1.5)
        chk(f"{th_name}: an icon at {at_} is centred on that point (+-4 px)", bb_i is not None and all(abs(a_ - b_) <= 4 for a_, b_ in zip(bb_i, exp_i)), (bb_i, exp_i))
live.THEME = themes.get(None)
chk("an icon leaves no residue after it ends", _bbox(BASE[:1] + [{"op": "image", "icon": "star", "start": 0.5, "dur": 1.0, "scale": 0.1}], 3.0) is None)
edge_ = live.layout(BASE + [{"op": "image", "icon": "star", "start": 0.5, "dur": 1.0, "scale": 0.2, "at": [0.99, 0.01]}])["layers"][-1]
chk("an icon at a corner is kept inside the frame", _bbox(BASE[:1] + [{"op": "image", "icon": "star", "start": 0.5, "dur": 1.0, "scale": 0.2, "at": [0.99, 0.01]}], 1.0)[2] <= live.W)
for label_, patch_, needle_ in [("both icon and path", {"icon": "star", "path": "/x.png"}, "exactly one"), ("neither icon nor path", {}, "exactly one"), ("an unknown icon", {"icon": "starr"}, "unknown icon"),
                                ("at outside the frame", {"icon": "star", "at": [1.5, 0.5]}, "between 0 and 1"), ("at with one number", {"icon": "star", "at": [0.5]}, "[x, y]"),
                                ("a bad colour", {"icon": "star", "color": "red"}, "color"), ("a non-boolean plate", {"icon": "star", "plate": "yes"}, "plate")]:
    try: live.layout(BASE + [{"op": "image", "start": 0.5, "dur": 1.0, **patch_}]); e_ = None
    except ValueError as ex: e_ = str(ex)
    chk(f"an image op rejects {label_}", e_ is not None and needle_ in e_, e_)
# ---- gain_curve (pure): the keyframes that shape fades and ducking
_A = dict(start_f=50, n_f=250, vol=-14.0, fi_f=25, fo_f=50, duck_f=[(100, 150), (160, 170), (240, 260)], duck_db=-12.0, ramp_f=8)
_k = dict(live.gain_curve(_A))
chk("gain_curve: constant level (no fades, no ducking) needs no keyframes", live.gain_curve(dict(_A, fi_f=0, fo_f=0, duck_f=[])) == [])
chk("gain_curve: fade-in starts at -60 dB and reaches the level after the fade", _k[50] == -60.0 and _k[75] == -14.0, _k)
chk("gain_curve: ducking dips by duck_db inside the interval and starts its ramp BEFORE the speech (breakpoint kept)", _k[92] == -14.0 and _k[100] == -26.0 and _k[170] == -26.0, _k)
chk("gain_curve: close intervals (gap < 2 ramps) merge into one dip (no bounce back up between them)", 130 not in _k and all(v == -26.0 for f, v in _k.items() if 100 <= f <= 170), _k)
chk("gain_curve: the level recovers a ramp after the speech ends", _k[178] == -14.0, _k)
chk("gain_curve: fade-out ends at -60 dB on the last frame", _k[299] == -60.0, _k)
chk("gain_curve: a ducked interval outside the audio is ignored", live.gain_curve(dict(_A, fi_f=0, fo_f=0, duck_f=[(900, 950)])) == [])
_ks = live.gain_curve(_A); chk("gain_curve: frames are strictly increasing", all(a_[0] < b_[0] for a_, b_ in zip(_ks, _ks[1:])), _ks)

# ---- audio op: levels measured on a real render (1 kHz tone over a silent video)
import subprocess, re as _re
live.W, live.H = 320, 180
live.CACHE = tempfile.mkdtemp(prefix="eng_audio_")
adir = tempfile.mkdtemp(prefix="eng_audio_files_")
silent_v = os.path.join(adir, "silent.mp4")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=25:d=8", "-pix_fmt", "yuv420p", silent_v], check=True)
tone = os.path.join(adir, "tone10.wav"); tone1 = os.path.join(adir, "tone1.wav")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=1000:duration=10:sample_rate=48000", "-c:a", "pcm_s16le", tone], check=True)
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=1000:duration=1:sample_rate=48000", "-c:a", "pcm_s16le", tone1], check=True)
live.CLIPS["S"], live.CLIP_LEN["S"] = silent_v, 8.0
SIL = [{"op": "add", "src": "S"}]
AU = lambda **k: {"op": "audio", "path": tone, "src_dur": 10.0, "start": 2.0, "dur": 4.0, "volume_db": -14.0, "fade_in": 0.0, "fade_out": 0.0, **k}
_n = [0]
def level_of(ops_, windows):
    """Render ops_ and return the mean volume (dB) of each (t0, t1) window of the exported audio."""
    _n[0] += 1
    out_ = os.path.join(adir, f"r{_n[0]}.mp4")
    p_, tr_, m_, tot_ = live.build(ops_); live.render(p_, tr_, out_, "ultrafast", 30, "128k")
    res_ = []
    for a_, b_ in windows:
        r_ = subprocess.run(["ffmpeg", "-v", "info", "-ss", str(a_), "-t", str(b_ - a_), "-i", out_, "-vn", "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True)
        mm_ = _re.search(r"mean_volume: (-?[\d.]+|-inf) dB", r_.stderr)
        res_.append(-120.0 if not mm_ or mm_.group(1) == "-inf" else float(mm_.group(1)))
    return res_
W3 = [(0.2, 1.8), (2.4, 5.6), (6.4, 7.8)]
ref0 = level_of(SIL + [AU(volume_db=0.0)], W3)
lv14 = level_of(SIL + [AU()], W3)
chk("audio: nothing is heard before it starts or after it ends (< -60 dB)", ref0[0] < -60 and ref0[2] < -60 and lv14[0] < -60 and lv14[2] < -60, (ref0, lv14))
chk("audio: the tone is audible while it plays", ref0[1] > -35, ref0)
chk("audio: volume_db=-14 is 14 dB below 0 dB (+-1 dB)", abs((lv14[1] - ref0[1]) - (-14.0)) <= 1.0, (lv14[1], ref0[1]))
lvm6 = level_of(SIL + [AU(volume_db=-6.0)], [(2.4, 5.6)])
chk("audio: volume_db=-6 is 6 dB below 0 dB (+-1 dB)", abs((lvm6[0] - ref0[1]) - (-6.0)) <= 1.0, (lvm6, ref0[1]))
fd = level_of(SIL + [AU(volume_db=0.0, fade_in=1.0, fade_out=1.0)], [(2.0, 2.3), (3.5, 4.5), (5.7, 6.0)])
chk("audio: fade in starts quiet and reaches full level (first 0.3 s >= 12 dB below the middle)", fd[0] < fd[1] - 12, fd)
chk("audio: fade out ends quiet (last 0.3 s >= 12 dB below the middle)", fd[2] < fd[1] - 12, fd)
dk = level_of(SIL + [AU(volume_db=0.0, duck=[(3.5, 4.5)], duck_db=-12.0)], [(2.3, 3.0), (3.7, 4.3), (5.0, 5.6)])
chk("audio: ducking lowers the level by the requested amount inside the interval (-12 dB +-2)", abs((dk[1] - dk[0]) - (-12.0)) <= 2.0, dk)
chk("audio: the level recovers after the ducked interval", abs(dk[2] - dk[0]) <= 1.5, dk)
lp = level_of(SIL + [AU(path=tone1, src_dur=1.0, volume_db=0.0, dur=3.0, loop=True)], [(2.2, 2.8), (3.2, 3.8), (4.2, 4.8), (5.2, 5.8)])
chk("audio: a 1 s file with loop=true keeps playing for the 3 s asked", lp[0] > -35 and lp[1] > -35 and lp[2] > -35 and lp[3] < -60, lp)
nl = level_of(SIL + [AU(path=tone1, src_dur=1.0, volume_db=0.0, dur=3.0)], [(2.2, 2.8), (3.5, 4.5)])
chk("audio: without loop a 1 s file stops after 1 s", nl[0] > -35 and nl[1] < -60, nl)
lay_a = live.layout(SIL + [AU(start=1.0, dur=20.0)])
chk("audio: default fades are shortened for a short sound instead of failing (1 s file -> 0.25 s in, 0.35 s out)", (lambda a_: abs(a_["fade_in"] - 0.25) < 1e-6 and abs(a_["fade_out"] - 0.35) < 1e-6)(live.layout(SIL + [{"op": "audio", "path": tone1, "src_dur": 1.0, "start": 1.0}])["audios"][0]))
chk("audio: a request longer than the timeline is trimmed with a warning", abs(lay_a["audios"][0]["dur_eff"] - 7.0) < 1e-6 and any("runs past" in w_ for w_ in lay_a["warnings"]), lay_a["warnings"])
lay_b = live.layout(SIL + [AU(path=tone1, src_dur=1.0, dur=3.0)])
chk("audio: asking for more than the file has (without loop) ends early with a warning", abs(lay_b["audios"][0]["dur_eff"] - 1.0) < 1e-6 and any("ends early" in w_ for w_ in lay_b["warnings"]), lay_b["warnings"])
lay_c = live.layout(SIL + [AU(start=9.0)])
chk("audio: one that starts after the timeline end is not built and warns", lay_c["audios"] == [] and any("after the timeline end" in w_ for w_ in lay_c["warnings"]), lay_c["warnings"])
for label_, patch_, needle_ in [("a volume above +6 dB", {"volume_db": 12.0}, "volume_db"), ("a negative start", {"start": -1.0}, ">= 0"), ("a start that is not a number", {"start": "x"}, "number"),
                                ("fades longer than the audio", {"fade_in": 3.0, "fade_out": 3.0}, "fade in+out"), ("'in' past the end of the file", {"in": 11.0}, "past the end"),
                                ("an unordered duck pair", {"duck": [[4.0, 3.0]]}, "duck"), ("a duck depth of +3 dB", {"duck_db": 3.0}, "duck_db"), ("loop as a string", {"loop": "yes"}, "loop"),
                                ("NaN volume", {"volume_db": float("nan")}, "finite")]:
    try: live.layout(SIL + [AU(**patch_)]); e_ = None
    except ValueError as ex: e_ = str(ex)
    chk(f"audio rejects {label_}", e_ is not None and needle_ in e_, e_)
try: live.layout(SIL + [AU() for _ in range(live.MAX_AUDIOS + 1)]); e_ = None
except ValueError as ex: e_ = str(ex)
chk("audio: more than the maximum number of audio ops is rejected", e_ is not None and "at most" in e_, e_)
two = level_of(SIL + [AU(volume_db=-6.0), AU(volume_db=-6.0, start=2.0)], [(2.4, 5.6)])
chk("audio: two overlapping audio ops are summed (louder than one, ~+6 dB for identical tones)", 4.0 <= two[0] - lvm6[0] <= 7.5, (two, lvm6))
# ---- animation on real frames (native 1280x720: pixel diffs are exact)
live.W, live.H = 1280, 720
live.CACHE = tempfile.mkdtemp(prefix="eng_anim_")
IMG = {"op": "image", "path": png, "start": 0.5, "dur": 3.0, "scale": 0.2, "pos": "center"}
A = lambda **a: {**IMG, "anim": a}
steady = _bbox(BASE[:1] + [IMG], 2.0)
sw_, sh_ = steady[2] - steady[0], steady[3] - steady[1]
ctr = lambda bb: ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)
bb_ = _bbox(BASE[:1] + [A(**{"in": "slide-left", "in_s": 0.6, "out": "none"})], 0.54)
chk("slide-left: just after the start the item is off-screen to the left", bb_ is None or bb_[2] <= steady[0], (bb_, steady))
bb_m = _bbox(BASE[:1] + [A(**{"in": "slide-left", "in_s": 0.6, "ease_in": "linear", "out": "none"})], 0.8)
chk("slide-left: halfway through the entrance it is between off-screen and its place", bb_m is not None and bb_m[0] < steady[0] - 20 and bb_m[2] > 0, (bb_m, steady))
bb_r = _bbox(BASE[:1] + [A(**{"in": "slide-left", "in_s": 0.6, "out": "none"})], 1.6)
chk("slide-left: afterwards it sits exactly where the static item sits (+-3 px)", bb_r is not None and all(abs(a_ - b_) <= 3 for a_, b_ in zip(bb_r, steady)), (bb_r, steady))
bb_o = _bbox(BASE[:1] + [A(**{"in": "none", "out": "slide-right", "out_s": 0.6})], 3.46)
chk("slide-right exit: in the last frames the item has left to the right", bb_o is None or bb_o[0] >= steady[2], (bb_o, steady))
bb_rot = _bbox(BASE[:1] + [A(**{"in": "none", "out": "none", "rotate": 45})], 2.0)
chk("constant rotate=45: a 2:1 picture's box grows (rotation really reaches MLT) and stays centred (+-4 px)",
    bb_rot is not None and (bb_rot[3] - bb_rot[1]) > 1.6 * sh_ and abs(ctr(bb_rot)[0] - ctr(steady)[0]) <= 4 and abs(ctr(bb_rot)[1] - ctr(steady)[1]) <= 4, (bb_rot, steady))
sizes_ = []
for k_ in range(1, 16):
    bb_p = _bbox(BASE[:1] + [A(**{"in": "pop", "in_s": 0.5, "out": "none"})], 0.5 + k_ * 0.04)
    sizes_.append(0 if bb_p is None else (bb_p[2] - bb_p[0]) / sw_)
chk("pop: starts smaller than rest, overshoots it, and settles (measured on 15 frames)", min(s_ for s_ in sizes_ if s_) < 0.92 and max(sizes_) > 1.015, sizes_)
chk_png = os.path.join(live.CACHE, "checker.png")                       # black/white checkerboard: contrasts with ANY background (a red box vanished against reddish footage)
_ck = Image.new("RGBA", (120, 60), (255, 255, 255, 255))
for _i in range(0, 120, 10):
    for _j in range(0, 60, 10):
        if (_i // 10 + _j // 10) % 2: _ck.paste((0, 0, 0, 255), (_i, _j, _i + 10, _j + 10))
_ck.save(chk_png)
KEYS = {**IMG, "path": chk_png, "anim": {"in": "none", "out": "none", "keys": [{"t": 0, "x": 0.2, "y": 0.5}, {"t": 2, "x": 0.8, "y": 0.5}], "keys_ease": "inout"}}
import anim as _anim
_a_norm = live.layout(BASE + [KEYS])["layers"][-1]["anim"]
for t_ in (0.5, 1.0, 1.5, 2.0, 3.0):                                     # frame-exact expectation: the item's own frame -> the easing
    f_rel_ = int(round(t_ * 25)) - int(round(0.5 * 25))
    exp_x = _anim.transform_at(_a_norm, f_rel_, 75, 25, (512, 296, 256, 128), 1280, 720, 0.2)[0]
    bb_k = _bbox(BASE[:1] + [KEYS], t_)
    chk(f"free keys: at t={t_:g}s the item's centre is where the easing puts it (+-8 px)", bb_k is not None and abs(ctr(bb_k)[0] - (exp_x[0] + exp_x[2] / 2)) <= 8, (bb_k, exp_x))
two = [{**IMG, "start": 0.5, "dur": 1.0, "anim": {"in": "none", "out": "none", "rotate": 45}}, {**IMG, "start": 1.6, "dur": 1.0}]
bb_two = _bbox(BASE[:1] + two, 2.0)
chk("a rotated item does not leak its rotation into the next item on the same track (that one stays upright)", bb_two is not None and abs((bb_two[3] - bb_two[1]) - sh_) <= 3, (bb_two, sh_))
lt_ = {"op": "lower_third", "title": "Señor Muñoz", "subtitle": "Director", "start": 0.5, "dur": 3.0}
lt_steady = _bbox(BASE[:1] + [lt_], 2.0)
lt_a = _bbox(BASE[:1] + [{**lt_, "anim": {"in": "slide-left", "in_s": 0.6, "ease_in": "linear", "out": "none"}}], 0.8)
chk("a lower third can slide in from the left (halfway: left of its place)", lt_a is not None and lt_a[0] < lt_steady[0] - 20, (lt_a, lt_steady))
lt_b = _bbox(BASE[:1] + [{**lt_, "anim": {"in": "slide-left", "in_s": 0.6, "out": "none"}}], 1.6)
chk("...and rests exactly where the static one does (+-3 px)", lt_b is not None and all(abs(a_ - b_) <= 3 for a_, b_ in zip(lt_b, lt_steady)), (lt_b, lt_steady))
tx_ = {"op": "text", "text": "Hola mundo", "start": 0.5, "dur": 3.0, "pos": "center", "size": 0.08, "fade": 0.0}
tx_s = _bbox(BASE[:1] + [tx_], 2.0)
tx_p = _bbox(BASE[:1] + [{**tx_, "anim": {"in": "zoom", "in_s": 0.6, "ease_in": "linear", "out": "none"}}], 0.8)
chk("a text can zoom in (halfway it is bigger than at rest)", tx_p is not None and (tx_p[2] - tx_p[0]) > (tx_s[2] - tx_s[0]) * 1.2, (tx_p, tx_s))
wp_ = {**tx_, "anim": {"in": "wipe", "in_s": 1.0, "out": "wipe", "out_s": 1.0}}
full_ = _bbox(BASE[:1] + [wp_], 2.0)
half_in = _bbox(BASE[:1] + [wp_], 1.0)                               # item starts at 0.5 s: halfway through a 1 s linear reveal
half_out = _bbox(BASE[:1] + [wp_], 3.0)                              # item ends at 3.5 s: halfway through the exit
start_ = _bbox(BASE[:1] + [wp_], 0.6)
fw_ = (full_[2] - full_[0]) if full_ else 0
chk("wipe in: at rest the whole text shows", full_ is not None and fw_ > 100, full_)
chk("wipe in: halfway through only the left half is visible and the left edge has not moved", half_in is not None and abs(half_in[0] - full_[0]) <= 3 and 0.35 * fw_ <= (half_in[2] - half_in[0]) <= 0.65 * fw_, (half_in, full_))
chk("wipe in: the start shows at most a sliver", start_ is None or (start_[2] - start_[0]) < 0.3 * fw_, start_)
chk("wipe out: halfway through the left half is erased and the right edge has not moved", half_out is not None and abs(half_out[2] - full_[2]) <= 3 and 0.35 * fw_ <= (half_out[2] - half_out[0]) <= 0.65 * fw_, (half_out, full_))
chk("wipe leaves no residue after the item", _bbox(BASE[:1] + [wp_], 4.0) is None)
try: live.layout(BASE + [{"op": "pip", "src": "B", "start": 0.5, "dur": 2.0, "scale": 0.3, "pos": "top-right", "anim": {"in": "wipe"}}]); e_ = None
except ValueError as ex: e_ = str(ex)
chk("wipe is rejected on pip with a clear message", e_ is not None and "wipe" in e_ and "pip" in e_, e_)
static_ = live.layout(BASE + [tx_])["layers"][0]
chk("a layer without anim keeps exactly the old fade path (anim is None)", static_["anim"] is None)
for label_, op_, needle_ in [
                             ("an animation on subtitles", {"op": "subtitles", "cues": [{"start": 1.0, "end": 2.0, "text": "x"}], "anim": {"in": "pop"}}, "not supported"),
                             ("an unknown preset", A(**{"in": "explode"}), "anim.in"), ("keys going back in time", A(keys=[{"t": 2.0}, {"t": 1.0}]), "increase"),
                             ("entrance+exit longer than the item", {**IMG, "dur": 1.0, "anim": {"in_s": 0.8, "out_s": 0.8}}, "longer than the item")]:
    try: live.layout(BASE + [op_]); e_ = None
    except ValueError as ex: e_ = str(ex)
    chk(f"layout rejects {label_}", e_ is not None and needle_ in e_, e_)
# ---- audio clips that never overlap share one MLT track (many effects, few tracks)
def _au(s_, d_, v_=0.0, **k_): return AU(start=s_, dur=d_, volume_db=v_, fade_in=0.0, fade_out=0.0, **k_)
many_ = SIL + [_au(0.4 + i * 0.36, 0.25) for i in range(20)]            # the silent test clip is 8 s long
lay_ = live.layout(many_)
chk("20 short sounds in a row are accepted (the old cap was 8 tracks) and share ONE track", len(live.pack_audio(lay_["audios"])) == 1, len(live.pack_audio(lay_["audios"])))
wins_ = [(0.4 + i * 0.36 + 0.04, 0.4 + i * 0.36 + 0.2) for i in (0, 7, 19)] + [(0.4 + i * 0.36 + 0.28, 0.4 + i * 0.36 + 0.34) for i in (0, 7, 18)]
lv_ = level_of(many_, wins_)
chk("each of the 20 sounds is heard where it was put and the gaps between are silent", all(v_ > -35 for v_ in lv_[:3]) and all(v_ < -60 for v_ in lv_[3:]), lv_)
two_ = level_of(SIL + [_au(1.0, 1.0, -6.0), _au(3.0, 1.0, -20.0)], [(1.2, 1.8), (3.2, 3.8), (2.2, 2.8)])
chk("clips on a shared track keep their own level (-6 dB vs -20 dB differ by 14 dB +-2) and the gap is silent", abs((two_[0] - two_[1]) - 14.0) <= 2.0 and two_[2] < -60, two_)
chk("clips that overlap in time get separate tracks", len(live.pack_audio(live.layout(SIL + [_au(1.0, 3.0), _au(2.0, 3.0)])["audios"])) == 2)
try: live.layout(SIL + [_au(1.0, 3.0) for _ in range(live.MAX_AUDIO_TRACKS + 1)]); e_ = None
except ValueError as ex: e_ = str(ex)
chk(f"more than {live.MAX_AUDIO_TRACKS} clips playing at once is rejected with a clear message", e_ is not None and "at the same time" in e_, e_)
TXT_ = {"op": "text", "text": "Hola mundo", "start": 0.5, "dur": 3.0, "pos": "center", "size": 0.08}
# ---- callouts: size, and animations about the ring (draw / pop / zoom / fade)
live.THEME = themes.get(None)
CO2 = {"op": "callout", "title": "Arco monumental", "subtitle": "Entrada", "path": [[1.0, 0.4, 0.6]], "start": 1.0, "dur": 3.0, "side": "ne", "fade": 0.3}
PX_, PY_ = 0.4 * live.W, 0.6 * live.H
rest_ = _bbox(BASE[:1] + [CO2], 2.5)
inside = lambda bb, m=3: bb is not None and bb[0] - m <= PX_ <= bb[2] + m and bb[1] - m <= PY_ <= bb[3] + m
ar = lambda bb: (bb[2] - bb[0]) * (bb[3] - bb[1]) if bb else 0
big_ = _bbox(BASE[:1] + [{**CO2, "size": 1.5}], 2.5)
chk("callout size=1.5 makes the whole callout 1.5x as big (width within 6%)", rest_ and big_ and abs((big_[2] - big_[0]) / (rest_[2] - rest_[0]) - 1.5) < 0.09, (rest_, big_))
png_a = graphics.render_callout(live.W, live.H, "Arco monumental", "Entrada", "ne", live.CACHE)
png_b = graphics.render_callout(live.W, live.H, "Arco monumental", "Entrada", "ne", live.CACHE, None, 1.0)
chk("size=1 is the original callout (same cached file)", png_a == png_b)
pa_, pb_ = graphics.render_callout(live.W, live.H, "Arco monumental", "Entrada", "ne", live.CACHE, None, 1.5), png_a
chk("the ring of a size=1.5 callout still lands on the point (+-3 px)", inside(big_))
for name_, an_ in (("draw", {"in": "draw", "out": "draw", "in_s": 1.0, "out_s": 1.0}), ("pop", {"in": "pop", "out": "zoom", "in_s": 1.0, "out_s": 1.0}), ("fade", {"in": "fade", "out": "fade", "in_s": 1.0, "out_s": 1.0})):
    op_ = {**CO2, "anim": an_}
    r_ = _bbox(BASE[:1] + [op_], 2.5)
    chk(f"callout anim {name_}: at rest it is the static callout (+-2 px)", r_ is not None and all(abs(a_ - b_) <= 2 for a_, b_ in zip(r_, rest_)), (r_, rest_))
    if name_ == "fade":
        continue
    areas_ = [ar(_bbox(BASE[:1] + [op_], t_)) for t_ in (1.1, 1.3, 1.5, 1.8, 2.5)]
    if name_ == "draw":
        chk("callout anim draw: it unfolds during the entrance (area never shrinks, starts small)", all(b_ >= a_ - 40 for a_, b_ in zip(areas_, areas_[1:])) and areas_[0] < 0.5 * areas_[-1], areas_)
    else:                                                       # pop eases with 'back': it overshoots rest size on the way (that is the point)
        chk("callout anim pop: it starts smaller than at rest and overshoots at most 30%", areas_[0] < 0.75 * areas_[-1] and max(areas_) < 1.3 * areas_[-1], areas_)
    chk(f"callout anim {name_}: the ring stays on the pinned point while it moves", all(inside(_bbox(BASE[:1] + [op_], t_)) for t_ in (1.3, 1.5, 1.8, 3.2, 3.5)), [_bbox(BASE[:1] + [op_], t_) for t_ in (1.3, 1.5, 1.8)])
    if name_ == "draw":
        out_areas = [ar(_bbox(BASE[:1] + [op_], t_)) for t_ in (3.2, 3.5, 3.8)]
        chk("callout anim draw: it folds back into the ring during the exit", all(b_ <= a_ + 40 for a_, b_ in zip(out_areas, out_areas[1:])) and out_areas[-1] < 0.6 * ar(rest_), out_areas)
    chk(f"callout anim {name_}: nothing is left after it ends", _bbox(BASE[:1] + [op_], 4.3) is None)
for label_, op_, needle_ in [("an entrance that slides", {**CO2, "anim": {"in": "slide-left"}}, "in/out among"), ("rotate", {**CO2, "anim": {"rotate": 10}}, "in/out among"),
                             ("keys", {**CO2, "anim": {"keys": [{"t": 0, "x": 0.5}]}}, "in/out among"), ("size 3", {**CO2, "size": 3}, "size must be"), ("size 0.2", {**CO2, "size": 0.2}, "size must be"),
                             ("draw on a text", {**TXT_, "anim": {"in": "draw"}}, "callouts only")]:
    try: live.layout(BASE + [op_]); e_ = None
    except ValueError as ex: e_ = str(ex)
    chk(f"layout rejects {label_} on this op", e_ is not None and needle_ in e_, e_)
live.MOTION = True
for n_ in themes.NAMES:
    live.THEME = themes.get(n_)
    got_ = [L["anim"]["in"] for L in live.layout(BASE + [CO2])["layers"] if L["kind"] == "callout"]
    chk(f"motion on, {n_}: a callout enters with {live.THEME.motion['callout']['in']}", got_ == [live.THEME.motion["callout"]["in"]], got_)
live.MOTION, live.THEME = False, themes.get(None)

# ---- template motion (opt-in): ops that name no anim get the template's own; nothing changes while the project's motion is off
import themes  # noqa: E811
TXT_ = {"op": "text", "text": "Hola mundo", "start": 0.5, "dur": 3.0, "pos": "center", "size": 0.08}
LT_ = {"op": "lower_third", "title": "Ana", "subtitle": "Dirección", "start": 0.5, "dur": 3.0}
IM_ = {**IMG}
SUB_ = {"op": "subtitles", "cues": [{"start": 1.0, "end": 2.0, "text": "x"}]}
def _anims(ops_):
    return [L.get("anim") for L in live.layout(BASE + ops_)["layers"]]
live.MOTION = False
chk("motion off: text, lower third and image stay on the original fade path (anim None)", all(a_ is None for a_ in _anims([TXT_, LT_, IM_])), _anims([TXT_, LT_, IM_]))
live.MOTION = True
for n_ in themes.NAMES:
    live.THEME = themes.get(n_)
    got_ = _anims([TXT_, LT_, IM_])
    want_ = [live.THEME.motion["text"]["in"], live.THEME.motion["lower_third"]["in"], live.THEME.motion["image"]["in"]]
    chk(f"motion on, {n_}: text / lower third / image enter with {want_}", [a_ and a_["in"] for a_ in got_] == want_, got_)
live.THEME = themes.get("playful")
chk("motion on: an explicit anim wins over the template's", _anims([{**TXT_, "anim": {"in": "zoom"}}])[0]["in"] == "zoom")
chk("motion on: anim={} still means no animation", _anims([{**TXT_, "anim": {}}]) == [None])
chk("motion on: subtitles are never animated", all(a_ is None for a_ in _anims([SUB_])))
short_ = _anims([{**TXT_, "dur": 0.6}])[0]
chk("motion on: the template's times are squeezed to fit a short item (no 'longer than the item' error)", short_ is not None and (short_["in_s"] or 0) + (short_["out_s"] or 0) <= 0.8 * 0.6 + 1e-6, short_)
live.MOTION, live.THEME = False, themes.get(None)
live.W, live.H = _W, _H


print(f"\n{len(SCENARIOS)+1+extra-bad} passed, {bad} failed"); sys.exit(1 if bad else 0)
