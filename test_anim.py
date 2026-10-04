#!/usr/bin/env python3
"""Pure-python tests of anim.py (easings, validation, sampling). Run: python3 test_anim.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anim
ok = bad = 0
def chk(name, cond, detail=""):
    global ok, bad
    ok += bool(cond); bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if not cond and detail else ""))

W, H, FPS = 1280, 720, 25
RECT = (400.0, 300.0, 300.0, 100.0)                                # a 300x100 item in the middle-left
def spec(**k): return anim.validate(k, "t", 4.0)
def at(a, f, n=100, rect=RECT): return anim.transform_at(a, f, n, FPS, rect, W, H, 0.2)

for e in anim.EASES:
    chk(f"ease {e}: starts at 0 and ends at 1", abs(anim.ease(e, 0)) < 1e-9 and abs(anim.ease(e, 1) - 1) < 1e-9, (anim.ease(e, 0), anim.ease(e, 1)))
for e in ("linear", "in", "out", "inout"):
    xs = [anim.ease(e, i / 50) for i in range(51)]
    chk(f"ease {e}: never goes backwards", all(b >= a - 1e-12 for a, b in zip(xs, xs[1:])))
chk("ease back overshoots 1 on the way (that is what makes a pop bounce)", max(anim.ease("back", i / 50) for i in range(51)) > 1.05)
chk("ease bounce stays within [0, 1]", all(0 <= anim.ease("bounce", i / 50) <= 1.0001 for i in range(51)))
try: anim.ease("wobble", 0.5); e_ = None
except ValueError as ex: e_ = str(ex)
chk("an unknown easing is rejected", e_ and "unknown easing" in e_, e_)

# presets
a = spec(**{"in": "slide-left", "in_s": 0.5, "out": "none"})
r0, o0, rot0 = at(a, 0)
chk("slide-left starts completely off the left edge", r0[0] + r0[2] <= 0.5, r0)
rm, om, _ = at(a, 50)
chk("...and at rest (after the entrance) sits exactly on its place, fully opaque", rm == RECT and om == 1.0, (rm, om))
a = spec(**{"in": "none", "out": "slide-right", "out_s": 0.5})
re, oe, _ = at(a, 99)
chk("slide-right as an exit ends completely off the right edge", re[0] >= W - 0.5, re)
a = spec(**{"in": "pop", "out": "none"})
sizes = [at(a, f)[0][2] / RECT[2] for f in range(0, 14)]
chk("pop starts small (< 0.65 of its size), overshoots 1 at some point, and settles at 1", sizes[0] < 0.65 and max(sizes) > 1.01 and abs(sizes[-1] - 1) < 0.02, (sizes[0], max(sizes), sizes[-1]))
rp, op_, _ = at(a, 0)
chk("pop grows around its centre (the centre does not move)", abs((rp[0] + rp[2] / 2) - 550) < 1e-6 and abs((rp[1] + rp[3] / 2) - 350) < 1e-6, rp)
a = spec(**{"in": "spin", "out": "none"})
chk("spin starts rotated and at rest has no rotation", at(a, 0)[2] < -100 and abs(at(a, 60)[2]) < 1e-9, (at(a, 0)[2], at(a, 60)[2]))
a = spec(**{"in": "slide-top", "out": "slide-bottom", "in_s": 0.5, "out_s": 0.5})
rt0, rb0 = at(a, 0)[0], at(a, 99)[0]
chk("slide-top as an entrance starts above the frame; slide-bottom as an exit ends below it (names are sides of the screen)", rt0[1] + rt0[3] <= 0.5 and rb0[1] >= H - 0.5, (rt0, rb0))
a = spec(**{"in": "none", "out": "slide-top", "out_s": 0.5})
chk("slide-top as an EXIT leaves upward (the word names where it goes)", at(a, 99)[0][1] + at(a, 99)[0][3] <= 0.5, at(a, 99)[0])
a = spec(**{"in": "drop", "out": "none"})
chk("drop starts above the frame and lands on its place", at(a, 0)[0][1] + at(a, 0)[0][3] <= 0.5 and at(a, 60)[0] == RECT)
a = spec(**{"in": "zoom", "out": "none"})
chk("zoom starts bigger than rest and settles", at(a, 0)[0][2] > RECT[2] * 1.5 and at(a, 60)[0] == RECT)
a = spec(**{"in": "fade", "out": "fade"})
chk("default (fade in, fade out) keeps position and fades opacity from 0 to 1 and back", at(a, 0)[1] == 0.0 and at(a, 50)[1] == 1.0 and at(a, 99)[1] == 0.0 and at(a, 50)[0] == RECT)
a = spec(**{"in": "none", "out": "none"})
chk("in=none/out=none is the identity", all(at(a, f) == (RECT, 1.0, 0.0) for f in (0, 30, 99)))

# constants + keys
a = spec(**{"in": "none", "out": "none", "rotate": 12, "scale": 2})
r, o, rot = at(a, 40)
chk("constant rotate and scale apply all the time, around the centre", rot == 12 and abs(r[2] - 600) < 1e-6 and abs((r[0] + r[2] / 2) - 550) < 1e-6, (r, rot))
a = spec(**{"in": "none", "out": "none", "keys": [{"t": 0, "x": 0.2, "y": 0.5}, {"t": 2, "x": 0.8, "y": 0.5}], "keys_ease": "linear"})
c0, c1, c2 = (at(a, f)[0] for f in (0, 25, 50))
cx = lambda r: r[0] + r[2] / 2
chk("keys glide the CENTRE between the given fractions of the frame", abs(cx(c0) - 0.2 * W) < 1e-6 and abs(cx(c1) - 0.5 * W) < 1e-6 and abs(cx(c2) - 0.8 * W) < 1e-6, (cx(c0), cx(c1), cx(c2)))
chk("after the last key the item holds its final place", abs(cx(at(a, 90)[0]) - 0.8 * W) < 1e-6)
a = spec(**{"in": "none", "out": "none", "keys": [{"t": 0, "scale": 1.0, "opacity": 1.0}, {"t": 1, "scale": 2.0, "opacity": 0.0}], "keys_ease": "linear"})
r, o, _ = at(a, 12)
chk("keys interpolate scale and opacity too (halfway: 1.5x, 50%)", abs(r[2] - 450) < 12 and abs(o - 0.52) < 0.05, (r, o))

# sampling
a = spec(**{"in": "slide-left", "out": "fade", "in_s": 0.5, "out_s": 0.4})
smp = anim.sample(a, RECT, W, H, 500, FPS, 0.2)
chk("a long item with an entrance and an exit is sampled only around them (not 500 keyframes)", 20 <= len(smp) <= 40 and smp[0][0] == 0 and smp[-1][0] == 499, len(smp))
chk("sample frames strictly increase", all(x[0] < y[0] for x, y in zip(smp, smp[1:])))
smp1 = anim.sample(spec(**{"in": "pop", "out": "pop"}), RECT, W, H, 1, FPS, 0.2)
chk("a one-frame item does not crash", len(smp1) == 1)
smp2 = anim.sample(a, RECT, W, H, 8, FPS, 0.2)
chk("an item shorter than its animation squeezes it instead of failing", smp2[0][0] == 0 and smp2[-1][0] == 7)
smp3 = anim.sample(spec(**{"in": "none", "out": "none", "keys": [{"t": 0, "x": 0.1}, {"t": 3, "x": 0.9}]}), RECT, W, H, 100, FPS, 0.2)
chk("free keys are sampled every 2 frames across their span", 30 <= len(smp3) <= 60, len(smp3))

# rotation pivot: qtblend turns the rect about its top-left corner, so sample() moves the rect to keep the picture turning about its centre
import math
for deg in (45, 90, -30, 180):
    a_ = spec(**{"in": "none", "out": "none", "rotate": deg})
    f_, r_, o_, rot_ = anim.sample(a_, RECT, W, H, 50, FPS, 0.2)[0]
    th_ = math.radians(rot_)
    landed = (r_[0] + r_[2] / 2 * math.cos(th_) - r_[3] / 2 * math.sin(th_), r_[1] + r_[2] / 2 * math.sin(th_) + r_[3] / 2 * math.cos(th_))
    chk(f"rotate {deg}: turning about the rect's top-left lands the centre exactly where it was wanted", abs(landed[0] - 550) < 1e-6 and abs(landed[1] - 350) < 1e-6, landed)
chk("no rotation: the rect is untouched by the pivot fix", anim.pivot_fix(RECT, 0) == RECT)

# validation
for label, kw, needle in [("an unknown preset", {"in": "explode"}, "anim.in"), ("a zero duration", {"in_s": 0}, "in_s"), ("an unknown easing", {"ease_in": "wobble"}, "ease_in"),
                          ("a rotation of 900 degrees", {"rotate": 900}, "rotate"), ("a scale of 0", {"scale": 0}, "scale"), ("an unknown key", {"spin": 3}, "unknown anim key"),
                          ("keys that go back in time", {"keys": [{"t": 2}, {"t": 1}]}, "increase"), ("a key without t", {"keys": [{"x": 0.5}]}, "needs t"),
                          ("a key field out of range", {"keys": [{"t": 0, "opacity": 3}]}, "opacity"), ("a key after the item ends", {"keys": [{"t": 9}]}, "after the item"),
                          ("entrance+exit longer than the item", {"in_s": 3, "out_s": 3}, "longer than the item"), ("more than 60 keys", {"keys": [{"t": i * 0.01} for i in range(61)]}, "up to"),
                          ("a string instead of an object", "slide-left", "anim must be an object")]:
    try: anim.validate(kw, "op 1", 4.0); e_ = None
    except ValueError as ex: e_ = str(ex)
    chk(f"anim rejects {label}", e_ is not None and needle in e_, e_)
# wipe: crop keyframes (visible span as fractions of the item's width)
aw = spec(**{"in": "wipe", "out": "wipe", "in_s": 1.0, "out_s": 1.0})
wk = anim.wipe_keys(aw, 100, FPS, 0.2)
chk("wipe_keys: first frame is (almost) fully hidden, then grows to the full width", wk and wk[0][0] == 0 and wk[0][2] < 0.01 and any(abs(h_ - 1.0) < 1e-9 and l_ == 0 for _, l_, h_ in wk))
chk("wipe_keys: the reveal is linear (halfway = half)", any(f_ == int(0.5 * FPS) and abs(h_ - 0.5 * int(0.5 * FPS) / (0.5 * FPS)) < 0.05 for f_, _, h_ in wk))
chk("wipe_keys: the exit erases from the left and ends fully hidden", wk[-1][0] == 99 and wk[-1][1] > 0.97 and wk[-1][2] >= wk[-1][1])
chk("wipe_keys: the span never inverts and stays inside 0-1", all(0 <= l_ < h_ <= 1.0 + 1e-9 for _, l_, h_ in wk))
chk("wipe_keys: frames increase", [f_ for f_, _, _ in wk] == sorted({f_ for f_, _, _ in wk}))
chk("wipe_keys: an animation without wipe has no crop", anim.wipe_keys(spec(**{"in": "pop"}), 100, FPS, 0.2) == [])
chk("wipe leaves position, size and opacity alone (the crop does the work)", all(r_ == RECT and o_ == 1.0 for _, r_, o_, _ in anim.sample(spec(**{"in": "wipe", "out": "none"}), RECT, W, H, 100, FPS, 0.2)[10:]))
# callout draw / pop keys
ad = spec(**{"in": "draw", "out": "draw", "in_s": 1.0, "out_s": 1.0})
dk = anim.draw_keys(ad, 100, FPS, 0.2)
chk("draw_keys: starts almost closed, opens to 1, closes again at the end", dk[0][1] <= 0.05 and any(p_ == 1.0 for _, p_ in dk) and dk[-1][1] <= 0.05, [dk[0], dk[-1]])
chk("draw_keys: progress stays inside 0.02-1 and frames increase", all(0.02 <= p_ <= 1.0 for _, p_ in dk) and [f_ for f_, _ in dk] == sorted({f_ for f_, _ in dk}))
chk("draw_keys: out of a callout without draw there is nothing", anim.draw_keys(spec(**{"in": "pop"}), 100, FPS, 0.2) == [])
ck = anim.callout_keys(spec(**{"in": "pop", "out": "zoom", "in_s": 0.6, "out_s": 0.5}), 100, FPS, 0.2)
chk("callout_keys: pop starts below full size and opacity and has reached 1/1 when the entrance ends", ck[0][1] < 0.7 and ck[0][2] < 0.5 and any(abs(s_ - 1.0) < 0.02 and abs(o_ - 1.0) < 1e-9 for f_, s_, o_ in ck if 15 <= f_ <= 17), ck[:3])
chk("callout_keys: the exit fades to nothing", ck[-1][2] < 0.05, ck[-1])
chk("anim=None and anim={} mean no animation", anim.validate(None, "t", 4.0) is None and anim.validate({}, "t", 4.0) is None)
print(f"\n{ok} passed, {bad} failed"); sys.exit(1 if bad else 0)
