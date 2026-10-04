"""Animation of overlays: entrance/exit presets, free keyframes, constant tilt/scale. Pure functions (no MLT), so they are unit-testable.

An `anim` spec (all keys optional):
  in / out        preset for the entrance / exit: none | fade | slide-left | slide-right | slide-top | slide-bottom | pop | zoom | spin | drop | wipe | rise | draw
                  (draw = callouts only: unfolds from the ring along the staff to the flag; callouts also take none | fade | pop | zoom, scaled about the ring.
                  wipe = revealed left to right like typing; as an exit it is erased left to right. Not for pip: it crops the item with MLT's qtcrop)
                  (slide-* name the SIDE OF THE SCREEN: as an entrance the item comes from that side, as an exit it leaves toward it)
  in_s / out_s    how long they take (default: fade = the item's own fade, others 0.5 s in / 0.4 s out)
  ease_in/ease_out  linear | in | out | inout | back | bounce  (defaults suit each preset)
  keys            free motion between the entrance and the exit: [{"t": s_from_item_start, "x": 0-1, "y": 0-1 (centre), "scale": k, "rotate": deg, "opacity": 0-1}, ...]
  keys_ease       easing between keys (default inout)
  rotate / scale  constants applied all the time (a tilted label, a bigger icon)
The result is sampled per frame and written as MLT keyframes, so every easing is exact (no reliance on MLT's interpolation modes)."""
import math

from . import registry
from .config import S

KEY_FIELDS = {"t", "x", "y", "scale", "rotate", "opacity"}
SPEC_KEYS = {"in", "out", "in_s", "out_s", "ease_in", "ease_out", "keys", "keys_ease", "rotate", "scale"}
MAX_KEYS = 60


class Preset:
    """An entrance/exit preset (plugins/anim). transform(p, x, y, w, h, W, H) -> (dx, dy, scale, rotation, opacity multiplier) at appearance
    progress p (0 = hidden, 1 = at rest).
      timing   "item" = takes the item's own fade time by default, else in_s/out_s default to 0.5 / 0.4 s
      reveal   "" | "wipe" | "draw": the preset is a crop the engine animates (wipe_keys / draw_keys), not a transform
      ease_in / ease_out  default easings (an exit never uses an overshooting easing);  still: True for 'none' (nothing to sample)
      callout  usable on callouts (scaled about the ring);  callout_only: only on callouts;  on_video: usable on picture-in-picture"""
    def __init__(self, transform, timing="", reveal="", ease_in="out", ease_out="in", still=False, callout=False, callout_only=False, on_video=True):
        self.transform, self.timing, self.reveal, self.ease_in, self.ease_out, self.still = transform, timing, reveal, ease_in, ease_out, still
        self.callout, self.callout_only, self.on_video = callout, callout_only, on_video


class Easing:
    """fn(p) for p in [0, 1]; overshoots = may leave 0..1 on the way (not used for exits, which must end exactly hidden)."""
    def __init__(self, fn, overshoots=False):
        self.fn, self.overshoots = fn, overshoots


def __getattr__(name):                       # PRESETS / EASES follow the registered plugins
    if name == "PRESETS":
        return registry.names("anim_preset")
    if name == "EASES":
        return registry.names("easing")
    raise AttributeError(name)


def preset(name):
    return registry.get("anim_preset", name)


def _exit_ease(name, pre):
    return name if name is not None and not registry.get("easing", name).overshoots else pre.ease_out


def _times(a, fade_s):
    in_s = a["in_s"] if a["in_s"] is not None else (fade_s if preset(a["in"]).timing == "item" else 0.5)
    out_s = a["out_s"] if a["out_s"] is not None else (fade_s if preset(a["out"]).timing == "item" else 0.4)
    return in_s, out_s


def ease(name, p):
    """Easing of progress p in [0, 1] -> [0, 1] (some, e.g. back/bounce, overshoot 1 on the way)."""
    if not registry.has("easing", name):
        raise ValueError(f"unknown easing '{name}'; choose one of {registry.names('easing')}")
    return registry.get("easing", name).fn(min(max(p, 0.0), 1.0))


def validate(spec, where, dur):
    """Check an anim spec and return it normalised (or None for no animation). Raises ValueError with a message to show verbatim."""
    if spec is None or spec == {}:
        return None
    if not isinstance(spec, dict):
        raise ValueError(f"{where}: anim must be an object like {{\"in\": \"slide-left\", \"out\": \"fade\"}}")
    extra = set(spec) - SPEC_KEYS
    if extra:
        raise ValueError(f"{where}: unknown anim key(s) {sorted(extra)}; allowed: {sorted(SPEC_KEYS)}")
    out = {}
    for k in ("in", "out"):
        v = spec.get(k, S.anim_default_preset)
        if not registry.has("anim_preset", v):
            raise ValueError(f"{where}: anim.{k} must be one of {registry.names('anim_preset')}")
        out[k] = v
    for k in ("in_s", "out_s"):
        v = spec.get(k)
        if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or not 0.05 <= v <= 10):
            raise ValueError(f"{where}: anim.{k} must be between 0.05 and 10 seconds")
        out[k] = None if v is None else float(v)
    for k in ("ease_in", "ease_out", "keys_ease"):
        v = spec.get(k)
        if v is not None and not registry.has("easing", v):
            raise ValueError(f"{where}: anim.{k} must be one of {registry.names('easing')}")
        out[k] = v
    for k, lo, hi in (("rotate", -720, 720), ("scale", 0.05, 10)):
        v = spec.get(k)
        if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or not lo <= v <= hi):
            raise ValueError(f"{where}: anim.{k} must be a number between {lo} and {hi}")
        out[k] = None if v is None else float(v)
    keys = spec.get("keys") or []
    if not isinstance(keys, list) or len(keys) > MAX_KEYS:
        raise ValueError(f"{where}: anim.keys must be a list of up to {MAX_KEYS} keyframes")
    norm, prev = [], -1.0
    for i, kf in enumerate(keys):
        if not isinstance(kf, dict) or "t" not in kf or set(kf) - KEY_FIELDS:
            raise ValueError(f"{where}: anim.keys[{i}] needs t (seconds from the item's start) and any of x, y, scale, rotate, opacity")
        for f_, lo, hi in (("t", 0, 3600), ("x", -1, 2), ("y", -1, 2), ("scale", 0.05, 10), ("rotate", -720, 720), ("opacity", 0, 1)):
            if f_ in kf and (not isinstance(kf[f_], (int, float)) or isinstance(kf[f_], bool) or not lo <= kf[f_] <= hi):
                raise ValueError(f"{where}: anim.keys[{i}].{f_} must be a number between {lo} and {hi}")
        if kf["t"] <= prev:
            raise ValueError(f"{where}: anim.keys times must increase (key {i})")
        prev = kf["t"]
        norm.append({k: float(v) for k, v in kf.items()})
    out["keys"] = norm
    if dur is not None:
        if (out["in_s"] or 0) + (out["out_s"] or 0) > dur + 1e-6:
            raise ValueError(f"{where}: anim in+out ({(out['in_s'] or 0) + (out['out_s'] or 0):g}s) is longer than the item ({dur:g}s)")
        if norm and norm[-1]["t"] > dur + 1e-6:
            raise ValueError(f"{where}: the last anim key (t={norm[-1]['t']:g}s) is after the item ends ({dur:g}s)")
    return out


def _preset(name, p, x, y, w, h, W, H):
    """(dx, dy, scale, rotation, opacity multiplier) of a preset at appearance progress p (0 = hidden, 1 = at rest)."""
    return preset(name).transform(p, x, y, w, h, W, H)


def _keys_at(keys, t, ease_name):
    """Interpolate the free keyframes at time t -> dict of the fields present (held before the first / after the last key)."""
    fields = {}
    for f_ in ("x", "y", "scale", "rotate", "opacity"):
        pts = [(k["t"], k[f_]) for k in keys if f_ in k]
        if not pts:
            continue
        if t <= pts[0][0]:
            fields[f_] = pts[0][1]
        elif t >= pts[-1][0]:
            fields[f_] = pts[-1][1]
        else:
            for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
                if t <= t1:
                    fields[f_] = v0 + (v1 - v0) * ease(ease_name, (t - t0) / (t1 - t0))
                    break
    return fields


def transform_at(a, f, n, fps, rect, W, H, fade_s):
    """Rect, opacity multiplier and rotation of an animated item at frame f (0..n-1) of its n frames."""
    x, y, w, h = rect
    t, total = f / fps, (n - 1) / fps
    in_s, out_s = _times(a, fade_s)
    if in_s + out_s > total > 0:                                  # never longer than the item: shrink both
        k = total / (in_s + out_s); in_s, out_s = in_s * k, out_s * k
    dx = dy = rot = 0.0
    sc, op = 1.0, 1.0
    if in_s > 0 and t < in_s and not preset(a["in"]).still:
        p = ease(a["ease_in"] or preset(a["in"]).ease_in, t / in_s)      # back/bounce may overshoot 1: that is the point
        d = _preset(a["in"], p, x, y, w, h, W, H)
        dx, dy, sc, rot, op = dx + d[0], dy + d[1], sc * d[2], rot + d[3], op * d[4]
    tail = total - t
    if out_s > 0 and tail < out_s and not preset(a["out"]).still:
        q = max(tail, 0.0) / out_s                                # 1 at the start of the exit, 0 at the very end
        p = 1 - ease(_exit_ease(a["ease_out"], preset(a["out"])), 1 - q)
        d = _preset(a["out"], p, x, y, w, h, W, H)
        dx, dy, sc, rot, op = dx + d[0], dy + d[1], sc * d[2], rot + d[3], op * d[4]
    cx, cy = x + w / 2 + dx, y + h / 2 + dy
    if a["keys"]:
        k = _keys_at(a["keys"], t, a["keys_ease"] or S.anim_keys_ease)
        if "x" in k:
            cx += k["x"] * W - (x + w / 2)
        if "y" in k:
            cy += k["y"] * H - (y + h / 2)
        sc *= k.get("scale", 1.0); rot += k.get("rotate", 0.0); op *= k.get("opacity", 1.0)
    sc *= a["scale"] if a["scale"] is not None else 1.0
    rot += a["rotate"] if a["rotate"] is not None else 0.0
    sc = max(sc, 0.01)
    return (cx - w * sc / 2, cy - h * sc / 2, w * sc, h * sc), min(max(op, 0.0), 1.0), rot


def sample(a, rect, W, H, n, fps, fade_s, base_op=1.0):
    """Keyframes for an animated item of n frames: [(frame, (x, y, w, h), opacity, rotation_deg)], every frame inside the entrance/exit
    windows and the keys' span, just the ends elsewhere. Linear interpolation between samples then reproduces the easing."""
    total = (n - 1) / fps
    in_s, out_s = _times(a, fade_s)
    if in_s + out_s > total > 0:
        k = total / (in_s + out_s); in_s, out_s = in_s * k, out_s * k
    frames = {0, max(0, n - 1)}
    if not preset(a["in"]).still:
        frames |= set(range(0, min(n, int(math.ceil(in_s * fps)) + 1)))
    if not preset(a["out"]).still:
        frames |= set(range(max(0, n - 1 - int(math.ceil(out_s * fps))), n))
    if a["keys"]:
        f0, f1 = int(round(a["keys"][0]["t"] * fps)), min(n - 1, int(round(a["keys"][-1]["t"] * fps)))
        frames |= set(range(max(0, f0), f1 + 1, 2)) | {max(0, f0), f1}
    out = []
    for f in sorted(frames):
        r, o, rot = transform_at(a, f, n, fps, rect, W, H, fade_s)
        out.append((f, pivot_fix(r, rot), o * base_op, rot))
    return out


def pivot_fix(rect, rot):
    """qtblend rotates about the TOP-LEFT corner of its rect (measured), not its centre. Move the rect so the picture turns about its own
    centre: place the top-left where the rotated centre lands on the wanted centre. (Positive = clockwise on screen.)"""
    if not rot:
        return rect
    x, y, w, h = rect
    cx, cy, th = x + w / 2, y + h / 2, math.radians(rot)
    c, s = math.cos(th), math.sin(th)
    return (cx - (w / 2 * c - h / 2 * s), cy - (w / 2 * s + h / 2 * c), w, h)


def _reveal(a, kind):
    """(entrance is a `kind` reveal, exit is a `kind` reveal) for kind 'wipe' / 'draw'."""
    return preset(a["in"]).reveal == kind, preset(a["out"]).reveal == kind


def wipe_keys(a, n, fps, fade_s):
    """Crop keyframes for an item whose entrance and/or exit is a 'wipe' reveal: [(frame, lo, hi)], the visible span as fractions (0-1) of the
    item's width. Entrance: hi grows 0 -> 1 (revealed left to right). Exit: lo grows 0 -> 1 (erased left to right). [] when nothing wipes.
    Sampled on every frame of the two windows, plus the ends, so MLT's linear interpolation between keys reproduces the easing."""
    win, wout = _reveal(a, "wipe")
    if not (win or wout):
        return []
    total = (n - 1) / fps
    in_s, out_s = _times(a, fade_s)
    if in_s + out_s > total > 0:
        k = total / (in_s + out_s); in_s, out_s = in_s * k, out_s * k
    e_in = a["ease_in"] or preset(a["in"]).ease_in
    e_out = _exit_ease(a["ease_out"], preset(a["out"]))

    def span(f):
        t, lo, hi = f / fps, 0.0, 1.0
        if win and in_s > 0 and t < in_s:
            hi = min(max(ease(e_in, t / in_s), 0.0), 1.0)
        tail = total - t
        if wout and out_s > 0 and tail < out_s:
            lo = min(max(ease(e_out, 1 - max(tail, 0.0) / out_s), 0.0), 1.0)
        lo = min(lo, 0.998)
        return lo, min(max(hi, lo + 0.002), 1.0)                    # a zero-width crop is not valid for MLT

    frames = {0, max(0, n - 1)}
    if win:
        frames |= set(range(0, min(n, int(math.ceil(in_s * fps)) + 2)))
    if wout:
        frames |= set(range(max(0, n - 2 - int(math.ceil(out_s * fps))), n))
    return [(f, *span(f)) for f in sorted(frames)]


def _windows(a, n, fps, fade_s):
    total = (n - 1) / fps
    in_s, out_s = _times(a, fade_s)
    if in_s + out_s > total > 0:
        k = total / (in_s + out_s); in_s, out_s = in_s * k, out_s * k
    return in_s, out_s


def draw_keys(a, n, fps, fade_s):
    """Callout 'draw' reveal: [(frame, p)] with p = how much of the callout has unfolded from its ring (0.02-1). Entrance grows 0 -> 1, exit
    shrinks 1 -> 0. [] without a draw reveal."""
    din, dout = _reveal(a, "draw")
    if not (din or dout):
        return []
    in_s, out_s = _windows(a, n, fps, fade_s)
    total = (n - 1) / fps
    e_in = a["ease_in"] or preset(a["in"]).ease_in
    e_out = _exit_ease(a["ease_out"], preset(a["out"]))

    def p_at(f):
        t, p = f / fps, 1.0
        if din and in_s > 0 and t < in_s:
            p = ease(e_in, t / in_s)
        tail = total - t
        if dout and out_s > 0 and tail < out_s:
            p = min(p, 1 - ease(e_out, 1 - max(tail, 0.0) / out_s))
        return min(max(p, 0.02), 1.0)

    frames = {0, max(0, n - 1)}
    if din:
        frames |= set(range(0, min(n, int(math.ceil(in_s * fps)) + 2)))
    if dout:
        frames |= set(range(max(0, n - 2 - int(math.ceil(out_s * fps))), n))
    return [(f, p_at(f)) for f in sorted(frames)]


def callout_keys(a, n, fps, fade_s):
    """Callout pop/zoom/fade: [(frame, scale, opacity)] to apply about the ring on every frame of the entrance/exit windows (+ both ends)."""
    in_s, out_s = _windows(a, n, fps, fade_s)
    frames = {0, max(0, n - 1)}
    if not preset(a["in"]).still:
        frames |= set(range(0, min(n, int(math.ceil(in_s * fps)) + 1)))
    if not preset(a["out"]).still:
        frames |= set(range(max(0, n - 1 - int(math.ceil(out_s * fps))), n))
    out = []
    for f in sorted(frames):
        r, o, _ = transform_at(a, f, n, fps, (0.0, 0.0, 100.0, 100.0), 1000, 1000, fade_s)
        out.append((f, r[2] / 100.0, o))
    return out
