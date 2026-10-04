"""Animation of overlays: entrance/exit presets, free keyframes, constant tilt/scale. Pure functions (no MLT), so they are unit-testable.

An `anim` spec (all keys optional):
  in / out        preset for the entrance / exit: none | fade | slide-left | slide-right | slide-top | slide-bottom | pop | zoom | spin | drop | wipe
                  (wipe = revealed left to right like typing; as an exit it is erased left to right. Not for pip: it crops the item with MLT's qtcrop)
                  (slide-* name the SIDE OF THE SCREEN: as an entrance the item comes from that side, as an exit it leaves toward it)
  in_s / out_s    how long they take (default: fade = the item's own fade, others 0.5 s in / 0.4 s out)
  ease_in/ease_out  linear | in | out | inout | back | bounce  (defaults suit each preset)
  keys            free motion between the entrance and the exit: [{"t": s_from_item_start, "x": 0-1, "y": 0-1 (centre), "scale": k, "rotate": deg, "opacity": 0-1}, ...]
  keys_ease       easing between keys (default inout)
  rotate / scale  constants applied all the time (a tilted label, a bigger icon)
The result is sampled per frame and written as MLT keyframes, so every easing is exact (no reliance on MLT's interpolation modes)."""
import math

EASES = ("linear", "in", "out", "inout", "back", "bounce")
PRESETS = ("none", "fade", "slide-left", "slide-right", "slide-top", "slide-bottom", "pop", "zoom", "spin", "drop", "wipe")
KEY_FIELDS = {"t", "x", "y", "scale", "rotate", "opacity"}
SPEC_KEYS = {"in", "out", "in_s", "out_s", "ease_in", "ease_out", "keys", "keys_ease", "rotate", "scale"}
DEFAULT_EASE_IN = {"pop": "back", "drop": "bounce", "spin": "back", "wipe": "linear"}
MAX_KEYS = 60


def ease(name, p):
    """Easing of progress p in [0, 1] -> [0, 1] (back/bounce may overshoot 1 on the way)."""
    p = min(max(p, 0.0), 1.0)
    if name == "linear":
        return p
    if name == "in":
        return p * p
    if name == "out":
        return 1 - (1 - p) ** 2
    if name == "inout":
        return 3 * p * p - 2 * p ** 3
    if name == "back":
        c1 = 1.70158; c3 = c1 + 1
        return 1 + c3 * (p - 1) ** 3 + c1 * (p - 1) ** 2
    if name == "bounce":
        n1, d1 = 7.5625, 2.75
        if p < 1 / d1:
            return n1 * p * p
        if p < 2 / d1:
            p -= 1.5 / d1; return n1 * p * p + 0.75
        if p < 2.5 / d1:
            p -= 2.25 / d1; return n1 * p * p + 0.9375
        p -= 2.625 / d1; return n1 * p * p + 0.984375
    raise ValueError(f"unknown easing '{name}'; choose one of {EASES}")


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
        v = spec.get(k, "fade")
        if v not in PRESETS:
            raise ValueError(f"{where}: anim.{k} must be one of {PRESETS}")
        out[k] = v
    for k in ("in_s", "out_s"):
        v = spec.get(k)
        if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or not 0.05 <= v <= 10):
            raise ValueError(f"{where}: anim.{k} must be between 0.05 and 10 seconds")
        out[k] = None if v is None else float(v)
    for k in ("ease_in", "ease_out", "keys_ease"):
        v = spec.get(k)
        if v is not None and v not in EASES:
            raise ValueError(f"{where}: anim.{k} must be one of {EASES}")
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
    q = 1 - p
    if name == "none":
        return 0.0, 0.0, 1.0, 0.0, 1.0
    if name == "fade":
        return 0.0, 0.0, 1.0, 0.0, min(max(p, 0.0), 1.0)
    if name == "slide-left":
        return -q * (x + w), 0.0, 1.0, 0.0, min(1.0, p * 4)
    if name == "slide-right":
        return q * (W - x), 0.0, 1.0, 0.0, min(1.0, p * 4)
    if name == "slide-bottom":                                    # names are SIDES OF THE SCREEN: in = comes from that side, out = leaves toward it
        return 0.0, q * (H - y), 1.0, 0.0, min(1.0, p * 4)
    if name in ("slide-top", "drop"):
        return 0.0, -q * (y + h), 1.0, 0.0, min(1.0, p * 4)
    if name == "wipe":                                            # position/size/opacity stay put: the reveal is a crop (wipe_keys)
        return 0.0, 0.0, 1.0, 0.0, 1.0
    if name == "pop":
        return 0.0, 0.0, 0.55 + 0.45 * p, 0.0, min(1.0, max(p, 0.0) * 3)
    if name == "zoom":
        return 0.0, 0.0, 1.6 - 0.6 * p, 0.0, min(max(p, 0.0), 1.0)
    if name == "spin":
        return 0.0, 0.0, 0.4 + 0.6 * p, -200.0 * q, min(1.0, max(p, 0.0) * 2)
    raise ValueError(name)


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
    in_s = a["in_s"] if a["in_s"] is not None else (fade_s if a["in"] == "fade" else 0.5)
    out_s = a["out_s"] if a["out_s"] is not None else (fade_s if a["out"] == "fade" else 0.4)
    if in_s + out_s > total > 0:                                  # never longer than the item: shrink both
        k = total / (in_s + out_s); in_s, out_s = in_s * k, out_s * k
    dx = dy = rot = 0.0
    sc, op = 1.0, 1.0
    if in_s > 0 and t < in_s and a["in"] != "none":
        p = ease(a["ease_in"] or DEFAULT_EASE_IN.get(a["in"], "out"), t / in_s)      # back/bounce may overshoot 1: that is the point
        d = _preset(a["in"], p, x, y, w, h, W, H)
        dx, dy, sc, rot, op = dx + d[0], dy + d[1], sc * d[2], rot + d[3], op * d[4]
    tail = total - t
    if out_s > 0 and tail < out_s and a["out"] != "none":
        q = max(tail, 0.0) / out_s                                # 1 at the start of the exit, 0 at the very end
        p = 1 - ease(a["ease_out"] if a["ease_out"] not in (None, "back", "bounce") else "in", 1 - q)
        d = _preset(a["out"], p, x, y, w, h, W, H)
        dx, dy, sc, rot, op = dx + d[0], dy + d[1], sc * d[2], rot + d[3], op * d[4]
    cx, cy = x + w / 2 + dx, y + h / 2 + dy
    if a["keys"]:
        k = _keys_at(a["keys"], t, a["keys_ease"] or "inout")
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
    in_s = a["in_s"] if a["in_s"] is not None else (fade_s if a["in"] == "fade" else 0.5)
    out_s = a["out_s"] if a["out_s"] is not None else (fade_s if a["out"] == "fade" else 0.4)
    if in_s + out_s > total > 0:
        k = total / (in_s + out_s); in_s, out_s = in_s * k, out_s * k
    frames = {0, max(0, n - 1)}
    if a["in"] != "none":
        frames |= set(range(0, min(n, int(math.ceil(in_s * fps)) + 1)))
    if a["out"] != "none":
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


def wipe_keys(a, n, fps, fade_s):
    """Crop keyframes for an item whose entrance and/or exit is 'wipe': [(frame, lo, hi)], the visible span as fractions (0-1) of the item's
    width. Entrance: hi grows 0 -> 1 (revealed left to right). Exit: lo grows 0 -> 1 (erased left to right). [] when nothing wipes.
    Sampled on every frame of the two windows, plus the ends, so MLT's linear interpolation between keys reproduces the easing."""
    if "wipe" not in (a["in"], a["out"]):
        return []
    total = (n - 1) / fps
    in_s = a["in_s"] if a["in_s"] is not None else (fade_s if a["in"] == "fade" else 0.5)
    out_s = a["out_s"] if a["out_s"] is not None else (fade_s if a["out"] == "fade" else 0.4)
    if in_s + out_s > total > 0:
        k = total / (in_s + out_s); in_s, out_s = in_s * k, out_s * k
    e_in = a["ease_in"] or "linear"
    e_out = a["ease_out"] if a["ease_out"] not in (None, "back", "bounce") else "linear"

    def span(f):
        t, lo, hi = f / fps, 0.0, 1.0
        if a["in"] == "wipe" and in_s > 0 and t < in_s:
            hi = min(max(ease(e_in, t / in_s), 0.0), 1.0)
        tail = total - t
        if a["out"] == "wipe" and out_s > 0 and tail < out_s:
            lo = min(max(ease(e_out, 1 - max(tail, 0.0) / out_s), 0.0), 1.0)
        lo = min(lo, 0.998)
        return lo, min(max(hi, lo + 0.002), 1.0)                    # a zero-width crop is not valid for MLT

    frames = {0, max(0, n - 1)}
    if a["in"] == "wipe":
        frames |= set(range(0, min(n, int(math.ceil(in_s * fps)) + 2)))
    if a["out"] == "wipe":
        frames |= set(range(max(0, n - 2 - int(math.ceil(out_s * fps))), n))
    return [(f, *span(f)) for f in sorted(frames)]
