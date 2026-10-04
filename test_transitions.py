#!/usr/bin/env python3
"""Transition styles: map generation, validation, template defaults (motion opt-in) and what each style really does on screen.
Solid red -> solid blue clips make the geometry of every style measurable from a few pixels. Needs a display (run under xvfb-run).
Run: xvfb-run -a .venv/bin/python test_transitions.py"""
import os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mlt7  # noqa: E402
from PIL import Image  # noqa: E402

import live  # noqa: E402
import themes  # noqa: E402
import transitions  # noqa: E402

ok = bad = 0


def chk(name, cond, detail=""):
    global ok, bad
    ok += bool(cond); bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


W, H = 320, 180
live.W, live.H, live.FPS = W, H, 25
live.CACHE = tempfile.mkdtemp(prefix="trans_")
live.CLIPS = {"R": "color:#ff0000", "B": "color:#0000ff"}
live.CLIP_LEN = {"R": 4.0, "B": 4.0}
OPS = lambda style, dur=1.0: [{"op": "add", "src": "R"}, {"op": "add", "src": "B"}, {"op": "crossfade", "between": [0, 1], "dur": dur, **({"style": style} if style else {})}]


def still(ops, t):
    p, tr, m, total = live.build(ops); tr.seek(int(round(t * live.FPS)))
    return Image.frombytes("RGB", (W, H), bytes(tr.get_frame().get_image(mlt7.mlt_image_rgb, W, H)))


def kind(px):
    r, _, b = px
    return "b" if b > r + 80 else "r" if r > b + 80 else "m"


P = lambda fx, fy: (int(fx * (W - 1)), int(fy * (H - 1)))
# style -> {point: colour at the middle of the transition}: red = still the old clip, blue = already the new one
MID = {"wipe-right": {P(.1, .5): "b", P(.9, .5): "r"}, "wipe-left": {P(.9, .5): "b", P(.1, .5): "r"},
       "wipe-down": {P(.5, .1): "b", P(.5, .9): "r"}, "wipe-up": {P(.5, .9): "b", P(.5, .1): "r"},
       "iris-out": {P(.5, .5): "b", P(.03, .5): "r", P(.97, .5): "r"}, "iris-in": {P(.5, .5): "r", P(.03, .5): "b", P(.97, .5): "b"},
       "diagonal": {P(.05, .05): "b", P(.95, .95): "r"}, "clock": {P(.9, .5): "b", P(.1, .5): "r"},
       "slide-left": {P(.9, .5): "b", P(.1, .5): "r"}, "slide-right": {P(.1, .5): "b", P(.9, .5): "r"},
       "slide-up": {P(.5, .9): "b", P(.5, .1): "r"}, "slide-down": {P(.5, .1): "b", P(.5, .9): "r"},
       "blinds-v": {(int(W * (3.2 / 10)), H // 2): "b", (int(W * (3.8 / 10)), H // 2): "r"}, "blinds-h": {(W // 2, int(H * (2.2 / 8))): "b", (W // 2, int(H * (2.8 / 8))): "r"},
       "dissolve": {P(.5, .5): "m", P(.1, .1): "m"}}

# ---- pure checks
chk("15 styles + auto are accepted", all(transitions.validate(s, "x") for s in transitions.STYLES + ("auto",)) and len(transitions.STYLES) == 15)
try: transitions.validate("spin", "op 2"); e_ = None
except ValueError as ex: e_ = str(ex)
chk("an unknown style is rejected with the list", e_ is not None and "op 2" in e_ and "wipe-right" in e_, e_)
for s_ in transitions.MASKED:
    im_ = Image.open(transitions.mask_path(s_, 640, 360, live.CACHE))
    chk(f"mask {s_}: grey, project-sized, uses a wide range but never 255", im_.mode == "L" and im_.size == (640, 360) and im_.getextrema()[0] <= 5 and 200 <= im_.getextrema()[1] <= 246, (im_.mode, im_.size, im_.getextrema()))
chk("masks are cached (same file twice)", transitions.mask_path("clock", 640, 360, live.CACHE) == transitions.mask_path("clock", 640, 360, live.CACHE))
chk("slide geometry runs from off-screen to rest in n frames", transitions.slide_geometry("slide-left", 25) == "0=100%/0%:100%x100%:100;24=0%/0%:100%x100%:100")
chk("every template names a valid default transition", all(t.transition in transitions.STYLES for t in themes.THEMES.values()))

# ---- layout rules
for label_, ops_, needle_ in [("an unknown style", OPS("spin"), "unknown transition"), ("a 0.1 s wipe", OPS("wipe-right", 0.1), "at least 0.2")]:
    try: live.layout(ops_); e_ = None
    except ValueError as ex: e_ = str(ex)
    chk(f"layout rejects {label_}", e_ is not None and needle_ in e_, e_)
chk("a 0.1 s dissolve is still fine", live.layout(OPS("dissolve", 0.1))["total_f"] > 0)
live.MOTION = False
chk("auto without motion is a plain dissolve", live.layout(OPS("auto"))["xstyles"] == {0: "dissolve"})
live.MOTION = True
for n_ in ("corporate", "arcade", "saas"):
    live.THEME = themes.get(n_)
    chk(f"auto with motion follows the {n_} template ({themes.get(n_).transition})", live.layout(OPS("auto"))["xstyles"] == {0: themes.get(n_).transition}, live.layout(OPS("auto"))["xstyles"])
live.THEME, live.MOTION = themes.get(None), False
chk("the style does not change the duration (4 + 4 - 1 s)", all(abs(live.layout(OPS(s_))["total_f"] / 25 - 7.0) < 1e-9 for s_ in transitions.STYLES))

# ---- on screen
for s_ in transitions.STYLES:
    pre, post = still(OPS(s_), 2.9), still(OPS(s_), 4.2)
    chk(f"{s_}: before it, the old clip only; after it, the new one only", all(kind(pre.getpixel(P(x, y))) == "r" for x, y in ((.1, .1), (.5, .5), (.9, .9))) and all(kind(post.getpixel(P(x, y))) == "b" for x, y in ((.1, .1), (.5, .5), (.9, .9))))
    mid = still(OPS(s_), 3.5)
    got = {pt: kind(mid.getpixel(pt)) for pt in MID[s_]}
    chk(f"{s_}: halfway the picture is split the way the style says", got == MID[s_], (got, MID[s_]))
# monotonic: the share of new-clip pixels never goes down while a wipe runs
for s_ in ("wipe-right", "iris-out", "clock", "slide-left"):
    share = []
    for k in range(0, 11):
        im_ = still(OPS(s_), 3.0 + k * 0.1)
        share.append(sum(kind(im_.getpixel((x, y))) == "b" for x in range(0, W, 16) for y in range(0, H, 12)))
    chk(f"{s_}: the new clip's area only grows (no flicker back)", all(b_ >= a_ for a_, b_ in zip(share, share[1:])) and share[0] <= 2 and share[-1] >= 0.97 * (len(range(0, W, 16)) * len(range(0, H, 12))), share)

# ---- slides must TRAVEL (shift the picture), not squeeze it: compare the middle frame with the expected cover-slide built from the two clips alone
import statistics, subprocess  # noqa: E402
from PIL import ImageChops  # noqa: E402
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate=25:duration=5", "-pix_fmt", "yuv420p", os.path.join(live.CACHE, "ta.mp4")], check=True)
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"smptehdbars=size={W}x{H}:rate=25:duration=5", "-pix_fmt", "yuv420p", os.path.join(live.CACHE, "tb.mp4")], check=True)
live.CLIPS = {"R": os.path.join(live.CACHE, "ta.mp4"), "B": os.path.join(live.CACHE, "tb.mp4")}
live.CLIP_LEN = {"R": 5.0, "B": 5.0}
fr_mid = 4 * 25 - 25 + 12                                            # A is 4 s (100 frames); the 25-frame transition starts at frame 75; frame 12 of 0..24 is the middle
for s_ in ("slide-left", "slide-right", "slide-up", "slide-down"):
    ops_ = [{"op": "add", "src": "R", "end": 4.0}, {"op": "add", "src": "B", "end": 4.0}, {"op": "crossfade", "between": [0, 1], "dur": 1.0, "style": s_}]
    got = still(ops_, fr_mid / 25)
    A_, B_ = still([{"op": "add", "src": "R", "end": 4.0}], fr_mid / 25), still([{"op": "add", "src": "B", "end": 4.0}], 12 / 25)
    sh_x, sh_y = round((1 - 12 / 24) * W), round((1 - 12 / 24) * H)
    exp_ = A_.copy()
    if s_ == "slide-left":
        exp_.paste(B_.crop((0, 0, W - sh_x, H)), (sh_x, 0))
    elif s_ == "slide-right":
        exp_.paste(B_.crop((sh_x, 0, W, H)), (0, 0))
    elif s_ == "slide-up":
        exp_.paste(B_.crop((0, 0, W, H - sh_y)), (0, sh_y))
    else:
        exp_.paste(B_.crop((0, sh_y, W, H)), (0, 0))
    err_ = statistics.mean(ImageChops.difference(got, exp_).convert("L").tobytes())
    chk(f"{s_}: halfway equals the new clip slid over the old one (mean error {err_:.2f} < 4), i.e. shifted, not squeezed", err_ < 4.0, err_)

print(f"\n{ok} passed, {bad} failed"); sys.exit(1 if bad else 0)
