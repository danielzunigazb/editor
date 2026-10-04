#!/usr/bin/python3.12
"""Live-edit engine: an LLM drives the editor by sending ONE declarative op at a time.

  live.py reset                      start a new session
  live.py apply '<json op>'          append op, rebuild timeline with MLT, render preview, write viewer.html
  live.py viewer                     only regenerate viewer.html from the saved state

Ops (times in seconds):
  {"op":"add","src":"A"}                                   append clip A (A/B/C) to the base track
  {"op":"cut","clip":0,"at":3.0}                           keep the first `at` s of base entry `clip`, drop the rest
  {"op":"crossfade","between":[0,1],"dur":1.0}             luma dissolve + audio crossfade between entries
  {"op":"fade","in":0.5,"out":1.0}                         fade from/to black (video) and silence (audio)
  {"op":"pip","src":"C","start":2.0,"dur":3.0,"pos":"top-right","scale":0.3,"opacity":0.9}

The timeline is always rebuilt by replaying the op list (declarative, so any op can be edited/removed later).
Needs X11 for the qtblend transition: run under xvfb-run.
"""
import base64, html, json, os, subprocess, sys, time

import anim as animmod
import graphics
import icons
import textrender
import themes

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(HERE, "out", "live")
STATE = os.path.join(LIVE, "state.json")
PREVIEW = os.path.join(LIVE, "preview.mp4")
VIEWER = os.path.join(LIVE, "viewer.html")
CLIPS = {k: os.path.join(HERE, "media", f"clip_{k.lower()}.mp4") for k in "ABC"}
CLIP_LEN = {"A": 6.0, "B": 5.0, "C": 4.0}
W, H, FPS = 640, 360, 25
CACHE = os.path.join(LIVE, "cache")     # rendered text PNGs (server points this at the project dir)
MAX_LAYER_TRACKS = 6
THEME = themes.get(None)                 # active template (server.bind sets it from the project); luxury = the original look
_BBOX = {}                              # cropped-overlay cache: png path -> (cropped path, x, y, w, h) or None


def _opt(name):                         # evaluated per build so tests can toggle them
    return os.environ.get(name, "1") == "1"


def _crop_to_content(path, W_, H_):
    """Crop a full-frame RGBA overlay to the bounding box of its visible pixels. qtblend then composites only that
    rectangle instead of a whole 4K frame. Returns (path, x, y, w, h), or None when cropping would not pay off.
    The result is also stored in a `<png>_crop.json` sidecar, so a later process (or a rebuild after the in-memory memo
    is gone) skips decoding the full-size PNG (0.37 s per 4K overlay)."""
    if path in _BBOX:
        return _BBOX[path]
    out, side = path[:-4] + "_crop.png", path[:-4] + "_crop.json"
    try:                                                    # sidecar is only trusted if it is newer than the source PNG
        if os.path.getmtime(side) >= os.path.getmtime(path):
            with open(side) as f:
                d = json.load(f)
            if d is None:
                _BBOX[path] = None
                return None
            if os.path.exists(out):
                _BBOX[path] = res = (out, *[int(d[k]) for k in ("x", "y", "w", "h")])
                return res
    except (OSError, ValueError, KeyError, TypeError):
        pass
    from PIL import Image
    res = None
    with Image.open(path) as im:
        im = im.convert("RGBA")
        bb = im.getchannel("A").getbbox()
        if bb is not None:
            pad = 2
            x0, y0 = max(0, bb[0] - pad), max(0, bb[1] - pad)
            x1, y1 = min(im.width, bb[2] + pad), min(im.height, bb[3] + pad)
            if (x1 - x0) * (y1 - y0) < 0.6 * im.width * im.height:        # a near-full-frame overlay gains nothing
                if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(path):
                    tmp = out + f".{os.getpid()}.tmp"
                    im.crop((x0, y0, x1, y1)).save(tmp, format="PNG")
                    os.replace(tmp, out)
                res = (out, x0, y0, x1 - x0, y1 - y0)
    tmp = side + f".{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(None if res is None else dict(zip("xywh", res[1:])), f)
    os.replace(tmp, side)
    _BBOX[path] = res
    return res


def _merge_decor(layers):
    """Layers that are static decoration shown at exactly the same time (e.g. vignette + frame for the whole video)
    are drawn by ONE qtblend: their PNGs are pre-composited. Returns the list of layers to build."""
    groups, order = {}, []
    for L in layers:
        if L["kind"] == "graphic" and L["gk"] in graphics.KINDS and not L.get("anim"):
            key = (round(L["start"], 4), round(L["dur"], 4), L["opacity"], L["fade"])
            if key not in groups:
                groups[key] = []; order.append(key)
            groups[key].append(L)
    drop = set()
    merged = {}
    for key in order:
        grp = groups[key]
        if len(grp) > 1:
            first = grp[0]
            merged[id(first)] = dict(first, gk="merged", params={"parts": [(g["gk"], g["params"]) for g in grp]})
            drop.update(id(g) for g in grp[1:])
    return [merged.get(id(L), L) for L in layers if id(L) not in drop]


POS_PIP = ("top-right", "top-left", "bottom-right", "bottom-left")
POS_IMG = POS_PIP + ("center",)
_IMG_CACHE = {}

# What the session intends to do, shown as "pending" in the viewer until each step lands.
PLAN = [
    {"op": "add", "src": "A"},
    {"op": "cut", "clip": 0, "at": 3.0},
    {"op": "add", "src": "B"},
    {"op": "crossfade", "between": [0, 1], "dur": 1.0},
    {"op": "fade", "in": 0.5, "out": 1.0},
    {"op": "pip", "src": "C", "start": 2.0, "dur": 3.0, "pos": "top-right", "scale": 0.3, "opacity": 0.9},
]


# ---------------------------------------------------------------- timeline model (pure python)
def _check_finite(v, where):
    """NaN/inf compare False with everything, so they slip through range checks (`not 0 <= x < y`) and then poison the
    frame grid; reject them up front. Walks nested dicts/lists (subtitle cues)."""
    if isinstance(v, float) and v != v or isinstance(v, float) and v in (float("inf"), float("-inf")):
        raise ValueError(f"{where}: numbers must be finite (got {v})")
    if isinstance(v, dict):
        for x in v.values():
            _check_finite(x, where)
    elif isinstance(v, list):
        for x in v:
            _check_finite(x, where)


ANIMATABLE = ("text", "image", "pip", "graphic", "lower_third")
MAX_AUDIOS = 8                          # simultaneous/total audio ops (each one is an MLT track)
DUCK_RAMP_S = 0.3                       # how fast the music dips/recovers around speech
MAX_LAYERS = 1000                       # overlays in one project (a 300-cue subtitle file counts 300)


def gain_curve(a):
    """Keyframes [(frame, dB)] for an audio op (absolute timeline frames): fade in, fade out and speech ducking, as ONE piecewise-linear
    curve in dB (linear in dB = a smooth, natural-sounding ramp). `a` has start_f, n_f, vol, fi_f, fo_f, duck_f (frame intervals),
    duck_db, ramp_f. Returns [] when the level is constant (the caller then sets a plain level)."""
    s, e = a["start_f"], a["start_f"] + a["n_f"] - 1
    vol, lo, fi, fo, ramp = a["vol"], -60.0, a["fi_f"], a["fo_f"], max(1, a["ramp_f"])
    base_pts = [(s, lo if fi else vol)] + ([(s + fi, vol)] if fi else []) + ([(e - fo, vol)] if fo else []) + [(e, lo if fo else vol)]
    iv = []
    for d0, d1 in sorted(a["duck_f"]):                            # clip to the audio, merge gaps shorter than two ramps (no crossing curves)
        d0, d1 = max(d0, s), min(d1, e)
        if d1 <= d0:
            continue
        if iv and d0 - iv[-1][1] < 2 * ramp:
            iv[-1][1] = max(iv[-1][1], d1)
        else:
            iv.append([d0, d1])
    if not iv and fi == 0 and fo == 0:
        return []
    def interp(pts, f):
        if f <= pts[0][0]:
            return pts[0][1]
        for (f0, v0), (f1, v1) in zip(pts, pts[1:]):
            if f <= f1:
                return v0 if f1 == f0 else v0 + (v1 - v0) * (f - f0) / (f1 - f0)
        return pts[-1][1]
    dips = []
    for d0, d1 in iv:
        dips.append([(d0 - ramp, 0.0), (d0, a["duck_db"]), (d1, a["duck_db"]), (d1 + ramp, 0.0)])
    def dip(f):
        return min([interp(p, f) for p in dips if p[0][0] <= f <= p[-1][0]] + [0.0])
    frames = sorted({f for f, _ in base_pts} | {min(max(f, s), e) for p in dips for f, _ in p})
    keys = [(f, round(interp(base_pts, f) + dip(f), 3)) for f in frames]          # every breakpoint is needed: dropping 'equal' ones would bend the ramps
    return keys


def layout(ops):
    """Compute entry durations/starts and effects from the op list (for validation + the SVG).
    Raises ValueError with a message the caller can show verbatim."""
    for n, o in enumerate(ops):
        _check_finite(o, f"op {n} ({o.get('op')})")
    # Everything on the base track lives on the FRAME GRID: MLT rounds each clip to whole frames, so computing in raw
    # seconds drifted (40 entries of 0.1 s: layout said 100 frames, MLT built 80) and overlays landed on the wrong frame.
    fps = FPS
    fr = lambda s_: int(round(s_ * fps))
    entries, xfades, fade, layers, audios = [], {}, None, [], []
    for n, o in enumerate(ops):
        k = o.get("op")
        where = f"op {n} ({k})"
        if o.get("anim") and k not in ANIMATABLE:
            raise ValueError(f"{where}: anim is not supported on '{k}' (use it on {', '.join(ANIMATABLE)}); a silently ignored animation would be worse")
        if k == "pip" and "wipe" in (o.get("anim") or {}).values():
            raise ValueError(f"{where}: anim 'wipe' is not available on pip (it crops a still layer); use slide, pop, zoom or fade")
        if k == "add":
            src = o.get("src")
            if src not in CLIP_LEN:
                raise ValueError(f"{where}: unknown source '{src}'; known: {sorted(CLIP_LEN)}")
            start, end = float(o.get("in", 0.0)), float(o.get("end", CLIP_LEN[src]))
            if not (0 <= start < end <= CLIP_LEN[src] + 1e-6):
                raise ValueError(f"{where}: range {start:g}-{end:g}s outside source '{src}' (0-{CLIP_LEN[src]:g}s)")
            in_f, dur_f, src_f = fr(start), fr(end - start), fr(CLIP_LEN[src])
            if in_f + dur_f > src_f:                 # rounding may overrun the last frame by one
                dur_f = src_f - in_f
            if dur_f < 1:
                raise ValueError(f"{where}: range {start:g}-{end:g}s is shorter than one frame ({1 / fps:g}s)")
            entries.append({"src": src, "in_f": in_f, "dur_f": dur_f})
        elif k == "cut":
            i = o.get("clip")
            if not isinstance(i, int) or not 0 <= i < len(entries):
                raise ValueError(f"{where}: no timeline entry {i} (have {len(entries)})")
            e = entries[i]
            at_f = fr(o["at"])
            if not 0 < o["at"] < e["dur_f"] / fps:
                raise ValueError(f"{where}: cut at {o['at']:g}s is outside entry {i} (length {e['dur_f'] / fps:g}s)")
            if not 1 <= at_f < e["dur_f"]:
                raise ValueError(f"{where}: cut at {o['at']:g}s leaves nothing or a sub-frame piece (frame = {1 / fps:g}s)")
            e["dur_f"] = at_f
        elif k == "crossfade":
            a_, b_ = o["between"]
            if b_ != a_ + 1 or a_ < 0 or b_ >= len(entries):
                raise ValueError(f"{where}: needs two adjacent existing entries (have {len(entries)})")
            if o["dur"] <= 0:
                raise ValueError(f"{where}: dur must be > 0")
            if fr(o["dur"]) < 1:
                raise ValueError(f"{where}: dur {o['dur']:g}s is shorter than one frame ({1 / fps:g}s)")
            xfades[a_] = fr(o["dur"])               # frames
        elif k == "fade":
            fade = {"in": float(o.get("in", 0.0)), "out": float(o.get("out", 0.0))}
            if fade["in"] < 0 or fade["out"] < 0:
                raise ValueError(f"{where}: fade times must be >= 0")
        elif k == "pip":
            src = o.get("src")
            if src not in CLIP_LEN:
                raise ValueError(f"{where}: unknown source '{src}'; known: {sorted(CLIP_LEN)}")
            if o.get("pos", "top-right") not in POS_PIP:
                raise ValueError(f"{where}: pos must be top-right|top-left|bottom-right|bottom-left")
            if not 0 < o.get("scale", 0.3) <= 1 or not 0 <= o.get("opacity", 1.0) <= 1:
                raise ValueError(f"{where}: scale must be in (0,1] and opacity in [0,1]")
            if o["start"] < 0 or o["dur"] <= 0 or o.get("in", 0.0) < 0 or o.get("in", 0.0) + o["dur"] > CLIP_LEN[src] + 1e-6:
                raise ValueError(f"{where}: needs start>=0, dur>0 and in+dur <= source length {CLIP_LEN[src]:g}s")
            layers.append({"kind": "pip", "op": n, "start": float(o["start"]), "dur": float(o["dur"]), "src": src,
                           "in": float(o.get("in", 0.0)), "pos": o.get("pos", "top-right"),
                           "scale": float(o.get("scale", 0.3)), "opacity": float(o.get("opacity", 1.0)), "anim": _anim(o, where)})
        elif k == "text":
            style = _text_style(o, where)
            if not (o.get("start", -1) >= 0 and 0 < o.get("dur", 0) <= 3600):
                raise ValueError(f"{where}: needs start>=0 and 0 < dur <= 3600")
            layers.append({"kind": "text", "op": n, "start": float(o["start"]), "dur": float(o["dur"]), **style,
                           "text": _clean(o.get("text"), where, style["style"]), "anim": _anim(o, where)})
        elif k == "subtitles":
            cues = o.get("cues")
            if not isinstance(cues, list) or not 1 <= len(cues) <= 300:
                raise ValueError(f"{where}: needs 1-300 cues")
            style = _text_style({"pos": "bottom", "size": 0.05, "fade": 0.0, "style": "auto",
                                 "ornament": "none", **{k: v for k, v in o.items() if v is not None}}, where)
            for ci, c in enumerate(cues):
                try:
                    st_, en_ = float(c["start"]), float(c["end"])
                except (KeyError, TypeError, ValueError):
                    raise ValueError(f"{where}: cue {ci} needs numeric start and end")
                if st_ < 0 or en_ <= st_:
                    raise ValueError(f"{where}: cue {ci} has an invalid time range {st_:g}-{en_:g}s")
                layers.append({"kind": "text", "op": n, "sub": ci, "start": st_, "dur": en_ - st_, **style,
                               "text": _clean(c.get("text"), f"{where} cue {ci}", style["style"])})
        elif k == "graphic":
            gk, par = o.get("kind"), {"amount": o.get("amount"), "theme": _theme_key(o, where)}
            try:
                graphics.validate(gk, par)
            except ValueError as e:
                raise ValueError(f"{where}: {e}")
            layers.append(_gfx_layer(n, o, gk, par, where))
        elif k == "lower_third":
            if o.get("align", "left") not in ("left", "right"):
                raise ValueError(f"{where}: align must be left or right")
            tk = _theme_key(o, where)
            th_ = themes.get(tk)
            title, sub = _clean(o.get("title"), where, th_.title_style), (_clean(o["subtitle"], where, th_.caption_style) if o.get("subtitle") else "")
            if "\n" in title or "\n" in sub or len(title) > 60 or len(sub) > 80:
                raise ValueError(f"{where}: lower third needs single-line text (title max 60, subtitle max 80 characters)")
            layers.append(_gfx_layer(n, o, "lower_third", {"title": title, "subtitle": sub, "align": o.get("align", "left"), "theme": tk}, where))
        elif k == "image":
            if o.get("pos", "center") not in POS_IMG:
                raise ValueError(f"{where}: pos must be one of {POS_IMG}")
            if not 0 < o.get("scale", 0.3) <= 1 or not 0 <= o.get("opacity", 1.0) <= 1:
                raise ValueError(f"{where}: scale must be in (0,1] and opacity in [0,1]")
            if not (o.get("start", -1) >= 0 and 0 < o.get("dur", 0) <= 3600):
                raise ValueError(f"{where}: needs start>=0 and 0 < dur <= 3600")
            xy = o.get("at")
            if xy is not None:
                if not (isinstance(xy, (list, tuple)) and len(xy) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 for v in xy)):
                    raise ValueError(f"{where}: at must be [x, y], two fractions of the frame between 0 and 1 (0,0 = top-left)")
                xy = (float(xy[0]), float(xy[1]))
            icon, path = o.get("icon") or "", o.get("path") or ""
            if bool(icon) == bool(path):
                raise ValueError(f"{where}: give exactly one of icon (a name from list_assets) or path (an image or .svg file)")
            color = o.get("color") or None
            if color is not None and not textrender.COLOR_RE.match(str(color)):
                raise ValueError(f"{where}: color must look like #RRGGBB")
            base = {"kind": "image", "op": n, "start": float(o["start"]), "dur": float(o["dur"]), "pos": o.get("pos", "center"), "xy": xy,
                    "scale": float(o.get("scale", 0.3)), "opacity": float(o.get("opacity", 1.0)), "anim": _anim(o, where)}
            if icon or path.lower().endswith(".svg"):
                try:
                    if icon:
                        svg = icons.icon_path(icon)
                    else:
                        svg = os.path.abspath(os.path.expanduser(path))
                        if not os.path.isfile(svg):
                            raise ValueError(f"image not found: {svg}")
                        icons.check_svg(svg)
                    aspect = 1.0 if (icon or svg is None) else icons.svg_aspect(svg)
                except ValueError as e:
                    raise ValueError(f"{where}: {e}")
                tk = _theme_key(o, where)
                plate = o.get("plate", True if icon else False)
                if not isinstance(plate, bool):
                    raise ValueError(f"{where}: plate must be true or false")
                default_color = icons.plate_style(themes.get(tk))[3] if plate else tk["accent"]
                layers.append({**base, "icon": icon or None, "svg": svg, "path": path or "", "aspect": aspect, "color": color or default_color, "plate": plate, "theme": tk})
            else:
                layers.append({**base, "path": path, "aspect": _image_aspect(path, where)})
        elif k == "audio":
            if not isinstance(o.get("loop", False), bool):
                raise ValueError(f"{where}: loop must be true or false")
            nums = {"start": o.get("start"), "in": o.get("in", 0.0), "src_dur": o.get("src_dur"), "volume_db": o.get("volume_db", -14.0),
                    "fade_in": o.get("fade_in") if o.get("fade_in") is not None else 0.0, "fade_out": o.get("fade_out") if o.get("fade_out") is not None else 0.0,
                    "duck_db": o.get("duck_db", -12.0)}
            for nk, nv in nums.items():
                if not isinstance(nv, (int, float)) or isinstance(nv, bool):
                    raise ValueError(f"{where}: {nk} must be a number")
            if o.get("dur") is not None and (not isinstance(o["dur"], (int, float)) or isinstance(o["dur"], bool) or not 0 < o["dur"] <= 3600):
                raise ValueError(f"{where}: dur must be between 0 and 3600 seconds (or omitted: as long as the audio / the timeline)")
            if nums["start"] < 0 or nums["in"] < 0 or nums["fade_in"] < 0 or nums["fade_out"] < 0:
                raise ValueError(f"{where}: start, in and the fades must be >= 0")
            if nums["src_dur"] <= 0 or nums["in"] >= nums["src_dur"]:
                raise ValueError(f"{where}: 'in' ({nums['in']:g}s) is past the end of the audio ({nums['src_dur']:g}s)")
            if not -60 <= nums["volume_db"] <= 6:
                raise ValueError(f"{where}: volume_db must be between -60 and 6")
            if not -40 <= nums["duck_db"] <= 0:
                raise ValueError(f"{where}: duck_db must be between -40 and 0 (how much quieter during speech)")
            duck = o.get("duck") or []
            if not isinstance(duck, list) or len(duck) > 400 or not all(isinstance(iv, (list, tuple)) and len(iv) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in iv) and 0 <= iv[0] < iv[1] for iv in duck):
                raise ValueError(f"{where}: duck must be a list of up to 400 [start_s, end_s] pairs (timeline seconds, start < end)")
            if len(audios) >= MAX_AUDIOS:
                raise ValueError(f"{where}: at most {MAX_AUDIOS} audio ops")
            audios.append({"op": n, "path": o.get("path"), "start": float(nums["start"]), "in": float(nums["in"]), "src_dur": float(nums["src_dur"]), "dur": o.get("dur"),
                           "vol": float(nums["volume_db"]), "fade_in": None if o.get("fade_in") is None else float(nums["fade_in"]),
                           "fade_out": None if o.get("fade_out") is None else float(nums["fade_out"]), "loop": bool(o.get("loop", False)),
                           "duck": [(float(a_), float(b_)) for a_, b_ in duck], "duck_db": float(nums["duck_db"]), "name": o.get("name") or os.path.basename(str(o.get("path")))})
        elif k == "callout":
            if o.get("side", "auto") not in graphics.CALLOUT_SIDES:
                raise ValueError(f"{where}: side must be one of {graphics.CALLOUT_SIDES}")
            if not (o.get("start", -1) >= 0 and 0 < o.get("dur", 0) <= 3600):
                raise ValueError(f"{where}: needs start>=0 and 0 < dur <= 3600")
            path, pts = o.get("path"), []
            if not isinstance(path, list) or not 1 <= len(path) <= 600:
                raise ValueError(f"{where}: path must be a list of 1-600 points [t_s, x, y] (x, y = fractions 0-1 of the frame)")
            for i_, pt in enumerate(path):
                if not (isinstance(pt, (list, tuple)) and len(pt) == 3 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in pt)):
                    raise ValueError(f"{where}: path point {i_} must be [t_s, x, y] with numbers")
                if not (0 <= pt[1] <= 1 and 0 <= pt[2] <= 1):
                    raise ValueError(f"{where}: path point {i_} x,y must be between 0 and 1 (fractions of the frame; 0,0 = top-left)")
                if pts and pt[0] <= pts[-1][0]:
                    raise ValueError(f"{where}: path times must increase (point {i_})")
                pts.append((float(pt[0]), float(pt[1]), float(pt[2])))
            tk = _theme_key(o, where)
            th_ = themes.get(tk)
            title, sub = _clean(o.get("title"), where, th_.title_style), (_clean(o["subtitle"], where, th_.caption_style) if o.get("subtitle") else "")
            if not title or "\n" in title or "\n" in sub or len(title) > graphics.CALLOUT_TITLE_MAX or len(sub) > graphics.CALLOUT_SUB_MAX:
                raise ValueError(f"{where}: callout needs a single-line title (1-{graphics.CALLOUT_TITLE_MAX} characters) and a subtitle of at most {graphics.CALLOUT_SUB_MAX}")
            layers.append({"kind": "callout", "op": n, "start": float(o["start"]), "dur": float(o["dur"]), "title": title, "sub": sub,
                           "side": o.get("side", "auto"), "path": pts, "fade": float(o.get("fade", 0.3)), "theme": tk})
        else:
            raise ValueError(f"{where}: unknown op")
    for i, e in enumerate(entries):   # a clip must be long enough for the dissolves on both of its sides
        need = xfades.get(i - 1, 0) + xfades.get(i, 0)
        if need > e["dur_f"]:
            raise ValueError(f"entry {i} is {e['dur_f'] / fps:g}s long but its crossfades need {need / fps:g}s")
    t = 0
    for i, e in enumerate(entries):
        e["start_f"] = t
        t += e["dur_f"] - xfades.get(i, 0)   # next entry starts `dur` earlier
        e["in"], e["dur"], e["start"] = e["in_f"] / fps, e["dur_f"] / fps, e["start_f"] / fps
    total_f = max((e["start_f"] + e["dur_f"] for e in entries), default=0)
    total = total_f / fps
    xfades_f, xfades = dict(xfades), {a: x / fps for a, x in xfades.items()}   # seconds for callers, frames for build()
    if fade and fade["in"] + fade["out"] > total + 1e-6:
        raise ValueError(f"fade in+out ({fade['in']+fade['out']:g}s) is longer than the timeline ({total:g}s)")
    # Overlays live in TIMELINE time: they do not move when earlier clips are edited. Anything now beyond the end
    # (e.g. after a cut) is clipped or dropped with a warning instead of rejecting the edit.
    if len(layers) > MAX_LAYERS:
        raise ValueError(f"{len(layers)} overlays (subtitle cues count one each); the limit is {MAX_LAYERS}")
    warnings, kept = [], []
    for L in layers:
        if L["start"] >= total - 1e-6:
            warnings.append(f"{_label(L)} starts at {L['start']:g}s, after the timeline end ({total:g}s): not shown")
            continue
        if L["start"] + L["dur"] > total + 1e-6:
            warnings.append(f"{_label(L)} runs past the timeline end ({total:g}s): trimmed")
            L["dur"] = total - L["start"]
        kept.append(L)
    ends = []                                   # first-fit track allocation: overlays that overlap get their own track
    for L in sorted(kept, key=lambda L: (L["start"], L["op"], L.get("sub", 0))):
        for ti, e in enumerate(ends):
            if e <= L["start"] + 1e-6:
                L["track"] = ti + 1; ends[ti] = L["start"] + L["dur"]; break
        else:
            if len(ends) >= MAX_LAYER_TRACKS:
                raise ValueError(f"more than {MAX_LAYER_TRACKS} overlays at the same time ({_label(L)} at {L['start']:g}s); "
                                 f"stagger them or remove some")
            ends.append(L["start"] + L["dur"]); L["track"] = len(ends)
    heard = []                                                   # audio ops: clip to the timeline, resolve frames
    for a in audios:
        if a["start"] >= total - 1e-6:
            warnings.append(f"audio {a['name']!r} starts at {a['start']:g}s, after the timeline end ({total:g}s): not heard")
            continue
        room = total - a["start"]
        have = float("inf") if a["loop"] else a["src_dur"] - a["in"]
        want = a["dur"] if a["dur"] is not None else min(have, room)
        if a["dur"] is not None and not a["loop"] and a["dur"] > have + 1e-6:
            warnings.append(f"audio {a['name']!r} is only {have:g}s long from {a['in']:g}s but {a['dur']:g}s were asked: it ends early (use loop=true to repeat it)")
            want = have
        if want > room + 1e-6:
            warnings.append(f"audio {a['name']!r} runs past the timeline end ({total:g}s): trimmed")
            want = room
        if a["fade_in"] is None:                                  # not asked for: the usual 1 s / 2 s, shortened for short sounds
            a["fade_in"] = min(1.0, want * 0.25)
        if a["fade_out"] is None:
            a["fade_out"] = min(2.0, want * 0.35)
        if a["fade_in"] + a["fade_out"] > want + 1e-6:
            raise ValueError(f"op {a['op']} (audio): fade in+out ({a['fade_in'] + a['fade_out']:g}s) is longer than the audio on the timeline ({want:g}s)")
        a.update(dur_eff=want, start_f=fr(a["start"]), in_f=fr(a["in"]), n_f=max(1, fr(want)), fi_f=fr(a["fade_in"]), fo_f=fr(a["fade_out"]),
                 duck_f=[(fr(x), fr(y)) for x, y in a["duck"]], ramp_f=fr(DUCK_RAMP_S))
        a["n_f"] = min(a["n_f"], total_f - a["start_f"])
        heard.append(a)
    heard.sort(key=lambda a: (a["start"], a["op"]))
    kept.sort(key=lambda L: (L["start"], L["op"], L.get("sub", 0)))
    warnings.extend(_collisions(kept))
    first_pip = next((L for L in kept if L["kind"] == "pip"), None)
    return {"entries": entries, "xfades": xfades, "xfades_f": xfades_f, "fade": fade, "layers": kept, "pip": first_pip, "audios": heard,
            "warnings": warnings, "total": total, "total_f": total_f}


def _zone(L):
    """Rough on-screen rectangle (x0, y0, x1, y1 as fractions of the frame) a layer occupies, or None for full-frame
    decoration (frame, vignette, letterbox), which is meant to sit under everything. A heuristic from character counts
    and sizes, not a measurement: it errs toward 'may overlap'."""
    fw, fh = max(W, 1), max(H, 1)
    k = L["kind"]
    if k == "text":
        lines_w = len(L["text"].split("\n")) and max(len(x) for x in L["text"].split("\n"))
        px = L["size"] * fh
        width = min(0.9, max(0.1, lines_w * 0.52 * px / fw * (1.0 + (0.16 if L["style"] in ("noir", "modern") else 0.03))))
        n_lines = max(1, -(-int(len(L["text"]) * 0.52 * px) // int(0.9 * fw))) if "\n" not in L["text"] else len(L["text"].split("\n"))
        h = (n_lines * 1.12 + 0.6) * L["size"] + (0.8 * L["size"] if L["box"] else 0)
        y0 = {"top": 0.08, "center": 0.5 - h / 2, "bottom": 0.92 - h}[L["pos"]]
        return (0.5 - width / 2, y0, 0.5 + width / 2, y0 + h)
    if k == "graphic" and L["gk"] == "lower_third":
        p = L["params"]
        width = min(0.92, max(len(p["title"]) * 0.5 * 0.046 * fh / fw * 1.1, len(p.get("subtitle", "")) * 0.7 * 0.0185 * fh / fw * 1.3) + 0.08)
        return (0.06, 0.78, 0.06 + width, 0.91) if p.get("align", "left") == "left" else (0.94 - width, 0.78, 0.94, 0.91)
    if k in ("pip", "image"):
        sc = L["scale"]
        hf = sc if k == "pip" else sc * fw / (L["aspect"] * fh)
        x0 = 0.04 if "left" in L["pos"] else (0.5 - sc / 2 if L["pos"] == "center" else 0.96 - sc)
        y0 = 0.04 if "top" in L["pos"] else (0.5 - hf / 2 if L["pos"] == "center" else 0.96 - hf)
        if L.get("xy"):
            x0, y0 = min(max(L["xy"][0] - sc / 2, 0), 1 - sc), min(max(L["xy"][1] - hf / 2, 0), 1 - hf)
        return (x0, y0, x0 + sc, y0 + hf)
    return None


def _collisions(layers, max_warnings=4):
    """Warnings for overlays from DIFFERENT edits that are on screen at the same time in overlapping places
    (e.g. subtitles at the bottom while a lower third is shown), so the editor hears about it right after the edit
    instead of only by looking at a frame."""
    zoned = [(L, _zone(L)) for L in layers]
    zoned = [(L, z) for L, z in zoned if z]
    seen, out = set(), []
    for i, (a, za) in enumerate(zoned):
        for b, zb in zoned[i + 1:]:
            if a["op"] == b["op"] or (a["op"], b["op"]) in seen:
                continue
            t0, t1 = max(a["start"], b["start"]), min(a["start"] + a["dur"], b["start"] + b["dur"])
            if t1 - t0 < 0.1:
                continue
            ox, oy = min(za[2], zb[2]) - max(za[0], zb[0]), min(za[3], zb[3]) - max(za[1], zb[1])
            if ox > -0.015 and oy > 0.01:          # touching or within 1.5% of the frame counts as crowding
                seen.add((a["op"], b["op"]))
                out.append(f"{_label(a)} and {_label(b)} may overlap or touch on screen from {t0:g}s to {t1:g}s; "
                           f"move one (position) or change its timing")
    if len(out) > max_warnings:
        out = out[:max_warnings] + [f"... and {len(out) - max_warnings} more overlapping pairs"]
    return out


def _gfx_layer(n, o, gk, params, where):
    if not (o.get("start", -1) >= 0 and 0 < o.get("dur", 0) <= 3600):
        raise ValueError(f"{where}: needs start>=0 and 0 < dur <= 3600")
    if not 0 <= o.get("opacity", 1.0) <= 1 or o.get("fade", 0.4) < 0:
        raise ValueError(f"{where}: opacity must be in [0,1] and fade >= 0")
    return {"kind": "graphic", "op": n, "start": float(o["start"]), "dur": float(o["dur"]), "gk": gk, "params": params,
            "opacity": float(o.get("opacity", 1.0)), "fade": float(o.get("fade", 0.4)), "anim": _anim(o, where)}


def _label(L):
    if L["kind"] == "text":
        return f"{'subtitle' if 'sub' in L else 'text'} {L['text'][:24]!r}"
    if L["kind"] == "graphic":
        return f"graphic {L['gk']}"
    if L["kind"] == "callout":
        return f"callout {L['title'][:24]!r}"
    return f"{L['kind']} {L.get('src') or L.get('icon') or os.path.basename(L.get('path', ''))}"


def _clean(text, where, style="classic"):
    try:
        return textrender.clean(text, style)
    except ValueError as e:
        raise ValueError(f"{where}: {e}")


def _anim(o, where):
    """Normalised animation spec of an op (None = static: the original fade ramps)."""
    return animmod.validate(o.get("anim"), where, o.get("dur") if isinstance(o.get("dur"), (int, float)) else None)


def _theme_key(o, where):
    """{"name","accent"} of the template a graphic/lower third/callout is drawn in: the op's own `theme` if given, else the project's."""
    t = o.get("theme")
    if t in (None, "", "auto"):
        return {"name": THEME.name, "accent": THEME.accent}
    if t not in themes.THEMES:
        raise ValueError(f"{where}: unknown template '{t}'; choose one of {themes.NAMES}")
    return {"name": t, "accent": THEME.accent if t == THEME.name else themes.THEMES[t].accent}


def resolve_style(o):
    """Concrete textrender style of a text/subtitles op: an explicit name wins; None/"auto" follows the project's template."""
    style = o.get("style")
    if style in (None, "", "auto"):
        return THEME.subtitle_style if o.get("op") == "subtitles" else THEME.title_style
    return style


def _text_style(o, where):
    pos, size, color = o.get("pos", "bottom"), o.get("size", 0.06), o.get("color") or None
    style, upper, orn = resolve_style(o), o.get("uppercase"), o.get("ornament") or None
    fade = float(o.get("fade", 0.15))
    try:
        textrender.validate_style(style)
    except ValueError as e:
        raise ValueError(f"{where}: {e}")
    if upper is not None and not isinstance(upper, bool):
        raise ValueError(f"{where}: uppercase must be true, false or omitted")
    if orn not in (None,) + textrender.ORNAMENTS:
        raise ValueError(f"{where}: ornament must be one of {textrender.ORNAMENTS}")
    if pos not in textrender.POSITIONS:
        raise ValueError(f"{where}: pos must be one of {textrender.POSITIONS}")
    if not isinstance(size, (int, float)) or not 0.02 <= size <= 0.2:
        raise ValueError(f"{where}: size is a fraction of the frame height, between 0.02 and 0.2")
    if color is not None and not textrender.COLOR_RE.match(str(color)):
        raise ValueError(f"{where}: color must look like #RRGGBB (or omit it to use the style's own colour)")
    if fade < 0:
        raise ValueError(f"{where}: fade must be >= 0")
    box = o.get("box")
    if box is None:                                       # the style decides (template panels), else the old defaults: subtitles on, titles off
        box = textrender.STYLES[style].get("box_default", o.get("op") == "subtitles")
    return {"pos": pos, "size": float(size), "color": color, "box": bool(box), "fade": fade,
            "style": style, "uppercase": upper, "ornament": orn}


def _image_aspect(path, where):
    from PIL import Image
    p = os.path.abspath(os.path.expanduser(str(path)))
    if not os.path.isfile(p):
        raise ValueError(f"{where}: image not found: {p}")
    key = (p, os.path.getmtime(p))
    if key not in _IMG_CACHE:
        if os.path.getsize(p) > 25_000_000:
            raise ValueError(f"{where}: image is larger than 25 MB")
        try:
            with Image.open(p) as im:
                im.verify()
            with Image.open(p) as im:
                w, h = im.size
        except Exception:
            raise ValueError(f"{where}: {p} is not a readable image (PNG/JPG/WebP)")
        if max(w, h) > 8000:
            raise ValueError(f"{where}: image is {w}x{h}; the limit is 8000 px per side")
        _IMG_CACHE[key] = w / h
    return _IMG_CACHE[key]


def check_new_op(op):
    """Extra validation for a freshly added op that needs the frame size (call with live.W/H bound to the
    EXPORT resolution): text must fit on screen. Raises ValueError."""
    k = op.get("op")
    if k == "lower_third":
        try:
            graphics.lower_third(W, H, op["title"], op.get("subtitle", ""), op.get("align", "left"), strict=True, theme=_theme_key(op, "lower_third"))
        except ValueError as e:
            raise ValueError(str(e))
        return
    if k == "callout":
        try:
            graphics.callout(W, H, op["title"], op.get("subtitle", ""), "ne", strict=True, theme=_theme_key(op, "callout"))
        except ValueError as e:
            raise ValueError(str(e))
        return
    if k == "text":
        texts, style, up = [(op.get("text"), op.get("size", 0.06))], resolve_style(op), op.get("uppercase")
    elif k == "subtitles":
        texts = [(c.get("text"), op.get("size", 0.05)) for c in op.get("cues", [])]
        style, up = resolve_style(op), op.get("uppercase")
    else:
        return
    for i, (t, size) in enumerate(texts):
        try:
            textrender.layout_text(textrender.clean(t, style), W, H, size, strict=True, style=style, uppercase=up)
        except ValueError as e:
            raise ValueError(f"{'cue ' + str(i) + ': ' if k == 'subtitles' else ''}{e}")


# ---------------------------------------------------------------- MLT build (the actual engine)
def _callout_target(L, t):
    """Pinned point (pixels) at timeline time t: linear between the path points, held before the first and after the last."""
    p = L["path"]
    if t <= p[0][0] or len(p) == 1:
        return p[0][1] * W, p[0][2] * H
    for (t0, x0, y0), (t1, x1, y1) in zip(p, p[1:]):
        if t <= t1:
            u = (t - t0) / (t1 - t0)
            return (x0 + (x1 - x0) * u) * W, (y0 + (y1 - y0) * u) * H
    return p[-1][1] * W, p[-1][2] * H


def _callout_pos(L, t, ax, ay, w, h):
    """Top-left of the callout image so that its ring sits exactly on the pinned point; kept inside the frame."""
    px, py = _callout_target(L, t)
    return min(max(px - ax, 0), W - w), min(max(py - ay, 0), H - h)


def _callout_source(L):
    """Render (cached) the callout image. side='auto' picks the first of ne/nw/se/sw whose flag stays fully inside the
    frame along the whole path (so the ring never has to move off the point to keep the flag on screen); if none does,
    the one that overflows least. Returns (png, w, h, ax, ay)."""
    sides = ("ne", "nw", "se", "sw") if L["side"] == "auto" else (L["side"],)
    best = None
    for s in sides:
        png, w, h, ax, ay = graphics.render_callout(W, H, L["title"], L["sub"], s, CACHE, L.get("theme"))
        over = 0.0
        for t_, x_, y_ in L["path"]:
            px, py = x_ * W, y_ * H
            x0, y0 = px - ax, py - ay
            over = max(over, -x0, x0 + w - W, -y0, y0 + h - H, 0)
        if best is None or over < best[0]:
            best = (over, (png, w, h, ax, ay))
        if over == 0:
            break
    return best[1]


def build(ops):
    import mlt7
    m = layout(ops)
    mlt7.Factory.init()
    p = mlt7.Profile()
    p.set_width(W); p.set_height(H); p.set_frame_rate(FPS, 1)
    p.set_sample_aspect(1, 1); p.set_display_aspect(W, H); p.set_progressive(1); p.set_explicit(1)
    fr = lambda s: int(round(s * FPS))

    base = mlt7.Playlist(p)
    for e in m["entries"]:
        prod = mlt7.Producer(p, CLIPS[e["src"]])        # default loader: do NOT use "avformat" directly
        assert prod.is_valid(), f"cannot open {e['src']}"
        base.append(prod, e["in_f"], e["in_f"] + e["dur_f"] - 1)
    # crossfades, ascending; each earlier mix inserts one extra playlist entry before later clips
    done = 0
    for a in sorted(m["xfades_f"]):
        n = m["xfades_f"][a]
        base.mix(a + done, n, mlt7.Transition(p, "luma"))
        base.mix_add(a + done, mlt7.Transition(p, "mix"))
        done += 1

    tr = mlt7.Tractor(p)
    mt = tr.multitrack()
    mt.connect(base, 0)
    total = base.get_playtime()
    assert total == m["total_f"], f"timeline model ({m['total_f']} frames) and MLT ({total}) disagree"

    layers_to_build = _merge_decor(m["layers"]) if _opt("MLT_OPT_MERGE") else m["layers"]
    by_track = {}
    for L in layers_to_build:
        by_track.setdefault(L["track"], []).append(L)
    for ti, track_key in enumerate(sorted(by_track), 1):   # MLT needs CONTIGUOUS track numbers: a gap (e.g. after merging
        lay = mlt7.Playlist(p)                             # two layers into one) segfaults the multitrack
        mt.connect(lay, ti)                  # connect first, then fill: transitions refer to this track index
        cursor, plan = 0, []
        for L in by_track[track_key]:
            s0 = max(fr(L["start"]), cursor)             # frame rounding must never overlap two layers on a track
            n = min(max(1, fr(L["dur"])), total - s0)
            if n < 1:
                continue
            plan.append((L, s0, n))
            cursor = s0 + n
        if not plan:
            continue
        cursor, kfs, has_pip, rkf, any_rot = 0, [], False, [], False
        t0 = plan[0][1]                                  # keyframe positions are relative to the transition's `in`
        for L, s0, n in plan:
            if L["kind"] == "text":
                src = textrender.render_text_png(L["text"], W, H, L["pos"], L["size"], L["color"], L["box"],
                                                 cache_dir=CACHE, strict=False, style=L["style"],
                                                 uppercase=L["uppercase"], ornament=L["ornament"])
            elif L["kind"] == "graphic":
                src = (graphics.render_merged(L["params"]["parts"], W, H, CACHE) if L["gk"] == "merged"
                       else graphics.render(L["gk"], W, H, CACHE, **L["params"]))
            elif L["kind"] == "image":
                src = icons.render_layer(L, W, CACHE) if (L.get("icon") or L.get("svg")) else os.path.abspath(os.path.expanduser(L["path"]))
            elif L["kind"] == "callout":
                src, cw, ch, cax, cay = _callout_source(L)
            else:
                src = CLIPS[L["src"]]; has_pip = True
            crop = None
            if L["kind"] in ("text", "graphic") and _opt("MLT_OPT_CROP"):
                crop = _crop_to_content(src, W, H)         # draw only the visible rectangle, 1:1 (full-frame PNGs cost ~2.4 s/s at 4K)
                if crop:
                    src = crop[0]
            prod = mlt7.Producer(p, src)
            if not prod.is_valid():
                raise RuntimeError(f"cannot open overlay source {src}")
            first = fr(L.get("in", 0.0)) if L["kind"] == "pip" else 0
            wk = animmod.wipe_keys(L["anim"], n, FPS, min(L.get("fade", 0.24), n / FPS / 2)) if L.get("anim") and L["kind"] != "callout" else []
            if wk:                                            # wipe: qtcrop pads everything outside the animated rect with transparency (image size unchanged)
                wf = mlt7.Filter(p, "qtcrop")
                wf.set("rect", ";".join(f"{f_}={lo * 100:.3f}%/0%:{(hi - lo) * 100:.3f}%x100%" for f_, lo, hi in wk))
                prod.attach(wf)
            if s0 > cursor:
                lay.blank(s0 - cursor - 1)   # Playlist.blank(out) takes the OUT POINT: it creates out+1 frames
            lay.append(prod, first, first + n - 1)
            cursor = s0 + n
            if L["kind"] == "callout":
                w, h, op, ramp = cw, ch, 1.0, min(fr(L["fade"]), (n - 1) // 2)
                lo, hi = s0 + ramp, s0 + n - 1 - ramp
                keys = sorted({s0, lo, hi, s0 + n - 1} | {f for f in (int(round(t_ * FPS)) for t_, _, _ in L["path"]) if lo < f < hi})
                alpha = lambda f_: 0 if ramp and f_ in (s0, s0 + n - 1) else 1
                pts = [(f_, "{:.2f} {:.2f} {} {} {}".format(*_callout_pos(L, f_ / FPS, cax, cay, w, h), w, h, alpha(f_))) for f_ in keys]
            elif L["kind"] in ("text", "graphic"):
                x, y, w, h = crop[1:] if crop else (0, 0, W, H)
                op, ramp = L.get("opacity", 1.0), min(fr(L["fade"]), (n - 1) // 2)
            else:
                mg = 0.04
                w = W * L["scale"]
                h = H * L["scale"] if L["kind"] == "pip" else w / L["aspect"]
                x = W * mg if "left" in L["pos"] else (W - w) / 2 if L["pos"] == "center" else W * (1 - mg) - w
                y = H * mg if "top" in L["pos"] else (H - h) / 2 if L["pos"] == "center" else H * (1 - mg) - h
                if L.get("xy"):                                   # centred on an exact point of the frame (kept inside it)
                    x, y = min(max(W * L["xy"][0] - w / 2, 0), W - w), min(max(H * L["xy"][1] - h / 2, 0), H - h)
                op, ramp = L["opacity"], min(6, (n - 1) // 2)
            rots = None
            if L.get("anim") and L["kind"] != "callout":      # animated: per-frame samples (position, size, opacity, rotation)
                smp = animmod.sample(L["anim"], (x, y, w, h), W, H, n, FPS, min(L.get("fade", 0.24), n / FPS / 2), op)
                pts = [(s0 + f_, "{:.2f} {:.2f} {:.2f} {:.2f} {:.3f}".format(*r_, o_)) for f_, r_, o_, _ in smp]
                rots = [(s0 + f_, round(rt_, 3)) for f_, _, _, rt_ in smp]
                any_rot = any_rot or any(v_ for _, v_ in rots)
            elif L["kind"] != "callout":
                rect = lambda a: f"{x:.0f} {y:.0f} {w:.0f} {h:.0f} {a}"
                pts = [(s0, rect(0 if ramp else op)), (s0 + ramp, rect(op)), (s0 + n - 1 - ramp, rect(op)),
                       (s0 + n - 1, rect(0 if ramp else op))]
            seen = set()
            for k, (pos, val) in enumerate(pts):         # drop duplicate positions (very short layers)
                if pos in seen:
                    continue
                seen.add(pos)
                last = k == len(pts) - 1
                kfs.append(f"{pos - t0}{'|' if last else ''}={val}")   # `|=` = discrete: hold until the next layer's first key
            rots = rots or [(s0, 0.0)]                       # a layer without rotation holds 0 until the next layer's first key
            for k, (pos, val) in enumerate(rots):
                rkf.append(f"{pos - t0}{'|' if k == len(rots) - 1 else ''}={val}")
        end = cursor - 1
        c = mlt7.Transition(p, "qtblend")   # composite/affine ignore opacity; qtblend honours it (needs X11)
        assert c.is_valid(), "qtblend unavailable: run under xvfb-run / with DISPLAY"
        c.set("rect", ";".join(kfs))
        if any_rot:                                                    # rotation keyframes only when some layer on this track rotates
            c.set("rotation", ";".join(rkf))
        c.set_in_and_out(t0, end)
        tr.plant_transition(c, 0, ti)    # ONE qtblend per overlay track, covering all of its layers (per-layer keyframes)
        if has_pip:                                      # only video clips carry audio; text/image tracks are silent
            mix = mlt7.Transition(p, "mix"); mix.set("sum", 1)
            mix.set_in_and_out(t0, end)
            tr.plant_transition(mix, 0, ti)

    ti_audio = len(by_track) + 1                       # audio tracks follow the visual ones with CONTIGUOUS numbers (a gap segfaults MLT)
    for a in m.get("audios", []):
        lay = mlt7.Playlist(p)
        mt.connect(lay, ti_audio)
        prod = mlt7.Producer(p, a["path"])
        if not prod.is_valid():
            raise RuntimeError(f"cannot open audio {a['path']}")
        length = prod.get_length()
        if a["start_f"] > 0:
            lay.blank(a["start_f"] - 1)                  # Playlist.blank(out) takes the OUT POINT: it creates out+1 frames
        left, first = a["n_f"], min(a["in_f"], max(0, length - 1))
        while left > 0:                                  # a looped track repeats the source (first pass from `in`, then from its start)
            span = min(left, length - first)
            if span < 1:
                break
            lay.append(prod, first, first + span - 1)
            left -= span
            first = 0
            if not a["loop"]:
                break
        keys = gain_curve(a)
        vf = mlt7.Filter(p, "volume")
        vf.set("level", ";".join(f"{f_}={v_}" for f_, v_ in keys) if keys else str(a["vol"]))   # volume.level is dB; keyframes are absolute timeline frames
        vf.set_in_and_out(0, total - 1)
        lay.attach(vf)
        mix = mlt7.Transition(p, "mix"); mix.set("sum", 1)
        mix.set_in_and_out(a["start_f"], a["start_f"] + a["n_f"] - 1)
        tr.plant_transition(mix, 0, ti_audio)
        ti_audio += 1

    if m["fade"]:
        fi, fo = fr(m["fade"]["in"]), fr(m["fade"]["out"])
        for service, lo, hi in (("brightness", 0, 1), ("volume", -60, 0)):   # volume.level is dB
            keys = {0: lo if fi > 0 else hi}                       # no fade-in => frame 0 must stay at full level
            if fi > 0:
                keys[fi] = hi
            if fo > 0:
                keys.setdefault(total - fo, hi)                    # a repeated position (fi == total - fo) glitched the level
                keys[total - 1] = lo
            ordered = sorted(keys.items())
            f = mlt7.Filter(p, service)
            f.set("level", ";".join(f"{pos}={val}" for pos, val in ordered))
            f.set_in_and_out(0, total - 1)          # attached filters default to in=out=0
            tr.attach(f)
    return p, tr, m, total


def render(p, tr, out, preset="ultrafast", crf="30", abr="64k"):
    """MLT composes -> NUT over a FIFO -> ffmpeg CLI encodes. Raises RuntimeError (with ffmpeg's message) if the encode
    fails, and never leaves a FIFO, a stray ffmpeg or a half-written output behind.
    A built timeline can be rendered ONCE (a second render of the same tractor emits no frames, verified); build a new
    one per render, as the server does. A fresh build after a failed render works."""
    import mlt7, shutil, tempfile
    fifo_dir = tempfile.mkdtemp(prefix="mltfifo_")           # private (0700) dir: no predictable name next to `out`
    fifo = os.path.join(fifo_dir, "pipe.nut")                # for another user/process to pre-create or race on
    os.mkfifo(fifo, 0o600)
    errlog = tempfile.TemporaryFile()
    ff = None
    try:
        ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-i", fifo, "-c:v", "libx264", "-preset", preset,
                               "-crf", str(crf), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", abr,
                               "-movflags", "+faststart", out], stderr=errlog, stdin=subprocess.DEVNULL)
        c = mlt7.Consumer(p, "avformat", fifo)
        # real_time=-N renders N frames in parallel (no frame dropping). Measured at 4K with 5 overlay layers: 65 s -> 35.6 s
        # (N=2) with bit-identical luma on all 312 frames; N=4 only reached 33.7 s but used 4.1 GB instead of 2.9 GB.
        threads = int(os.environ.get("MLT_RENDER_THREADS", "2"))
        for k, v in dict(f="nut", vcodec="rawvideo", acodec="pcm_s16le", real_time=str(-threads) if threads > 1 else "0").items():
            c.set(k, v)
        c.connect(tr); c.run(); c.stop()
        try:
            rc = ff.wait(timeout=120)
        except subprocess.TimeoutExpired:
            ff.kill(); rc = ff.wait()
            raise RuntimeError("ffmpeg did not finish within 120 s after the render ended")
        if rc != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
            errlog.seek(0)
            tail = errlog.read().decode(errors="replace").strip().splitlines()[-3:]
            raise RuntimeError(f"ffmpeg failed to write {out} (exit code {rc}): " + " | ".join(tail))
    except BaseException:
        if os.path.isfile(out) and not (ff is not None and ff.returncode == 0):
            os.remove(out)                                   # never leave a partial file that looks like a result
        raise
    finally:
        if ff is not None and ff.poll() is None:
            ff.kill(); ff.wait()
        errlog.close()
        shutil.rmtree(fifo_dir, ignore_errors=True)


# ---------------------------------------------------------------- state + viewer
def load():
    return json.load(open(STATE)) if os.path.exists(STATE) else {"ops": [], "steps": []}


def describe(o):
    k = o["op"]
    if k == "add":
        return f"Agregar clip {o['src']}", f"add {o['src']}"
    if k == "cut":
        return f"Cortar la entrada {o['clip']} en {o['at']:g} s", f"cut #{o['clip']} @ {o['at']:g}s"
    if k == "crossfade":
        return f"Fundido cruzado de {o['dur']:g} s entre {o['between'][0]} y {o['between'][1]}", f"xfade {o['between'][0]}-{o['between'][1]} {o['dur']:g}s"
    if k == "fade":
        return f"Fade desde negro ({o['in']:g} s) y a negro ({o['out']:g} s)", f"fade in {o['in']:g} out {o['out']:g}"
    if k == "text":
        return f"Texto \u201c{o['text'][:30]}\u201d de {o['start']:g} a {o['start']+o['dur']:g} s", f"text @ {o['start']:g}s {o['dur']:g}s"
    if k == "subtitles":
        return f"Subtítulos ({len(o['cues'])} líneas)", f"subtitles x{len(o['cues'])}"
    if k == "graphic":
        return f"Gráfico {o['kind']} de {o['start']:g} a {o['start']+o['dur']:g} s", f"graphic {o['kind']} @ {o['start']:g}s"
    if k == "lower_third":
        return f"Tercio inferior \u201c{o['title'][:24]}\u201d", f"lower_third @ {o['start']:g}s"
    if k == "image":
        return f"Imagen {os.path.basename(o['path'])} de {o['start']:g} a {o['start']+o['dur']:g} s", f"image @ {o['start']:g}s {o['dur']:g}s"
    return (f"Picture-in-picture: clip {o['src']} de {o['start']:g} a {o['start']+o['dur']:g} s, arriba a la derecha",
            f"pip {o['src']} @ {o['start']:g}s {o['dur']:g}s")


def svg_timeline(m):
    total = max(m["total"], 1.0)
    pad_l, pad_r, width = 44, 12, 800
    sx = lambda t: pad_l + (width - pad_l - pad_r) * t / total
    ntr = max((L["track"] for L in m["layers"]), default=0)
    rows = {"V1": 30}
    rows.update({f"L{t}": 30 + 36 * t for t in range(1, ntr + 1)})
    rows["FX"] = 30 + 36 * (ntr + 1)
    height = rows["FX"] + 38
    out = [f'<svg class="tl" viewBox="0 0 {width} {height}" role="img" aria-label="Línea de tiempo">']
    for t in range(int(total) + 1):
        x = sx(t)
        out.append(f'<line class="grid" x1="{x:.1f}" y1="18" x2="{x:.1f}" y2="{height-16}"/>'
                   f'<text class="tick" x="{x:.1f}" y="12" text-anchor="middle">{t}s</text>')
    for name, y in rows.items():
        out.append(f'<text class="lane" x="6" y="{y+20}">{name}</text>'
                   f'<rect class="laneBg" x="{pad_l}" y="{y}" width="{width-pad_l-pad_r}" height="28"/>')
    cls = lambda src: f"c{src.lower()}" if src.lower() in "abc" and len(src) == 1 else "ca"
    for i, e in enumerate(m["entries"]):
        x0, x1 = sx(e["start"]), sx(e["start"] + e["dur"])
        out.append(f'<rect class="clip {cls(e["src"])}" x="{x0:.1f}" y="{rows["V1"]}" width="{x1-x0:.1f}" height="28" rx="3"/>'
                   f'<text class="clipT" x="{x0+7:.1f}" y="{rows["V1"]+18}">{i} · {html.escape(e["src"])} · {e["dur"]:g}s</text>')
    for a, d in m["xfades"].items():
        e = m["entries"][a + 1]
        x0, x1 = sx(e["start"]), sx(e["start"] + d)
        out.append(f'<rect class="xf" x="{x0:.1f}" y="{rows["V1"]}" width="{x1-x0:.1f}" height="28"/>')
    for L in m["layers"]:
        x0, x1 = sx(L["start"]), sx(L["start"] + L["dur"])
        y = rows[f"L{L['track']}"]
        label = {"pip": f"{L.get('src', '')} · PiP", "text": L.get("text", "").replace("\n", " ")[:22],
                 "image": "imagen", "graphic": L.get("gk", "graphic"), "callout": L.get("title", "")[:22]}[L["kind"]]
        c = cls(L["src"]) if L["kind"] == "pip" else "ctext"
        out.append(f'<rect class="clip {c}" x="{x0:.1f}" y="{y}" width="{max(x1-x0, 2):.1f}" height="28" rx="3"/>'
                   f'<text class="clipT" x="{x0+7:.1f}" y="{y+18}">{html.escape(label)}</text>')
    if m["fade"]:
        f, fy = m["fade"], rows["FX"]
        if f["in"]:
            out.append(f'<polygon class="fx" points="{sx(0):.1f},{fy+28} {sx(0):.1f},{fy+2} {sx(f["in"]):.1f},{fy+28}"/>')
        if f["out"]:
            out.append(f'<polygon class="fx" points="{sx(total):.1f},{fy+28} {sx(total):.1f},{fy+2} {sx(total-f["out"]):.1f},{fy+28}"/>')
    out.append(f'<line id="ph" class="ph" x1="{pad_l}" y1="16" x2="{pad_l}" y2="{height-12}"/></svg>')
    return "".join(out)


def write_viewer(state):
    ops, steps = state["ops"], state["steps"]
    m = layout(ops)
    b64 = base64.b64encode(open(PREVIEW, "rb").read()).decode() if os.path.exists(PREVIEW) else ""
    items = []
    for i, step_op in enumerate(PLAN):
        done = i < len(steps)
        if done:
            s = steps[i]
            title, code = describe(ops[i])
            cur = " now" if i == len(steps) - 1 else ""
            meta = f'<span class="ms">render {s["render_ms"]/1000:.1f} s</span>'
            cls = f"op done{cur}"
        else:
            title, code = describe(step_op)
            meta, cls = '<span class="ms">pendiente</span>', "op todo"
        items.append(f'<li class="{cls}"><span class="n">{i+1}</span><div><div class="t">{html.escape(title)}</div>'
                     f'<code>{html.escape(code)}</code></div>{meta}</li>')
    last = steps[-1] if steps else None
    stats = (f'<div><dt>Duración</dt><dd>{m["total"]:.2f} s</dd></div>'
             f'<div><dt>Construir timeline</dt><dd>{last["build_ms"]:.0f} ms</dd></div>'
             f'<div><dt>Render del preview</dt><dd>{last["render_ms"]/1000:.1f} s</dd></div>'
             f'<div><dt>Resolución</dt><dd>{W}×{H}</dd></div>') if last else ""
    page = TEMPLATE
    for k, v in {"@@ITEMS@@": "".join(items), "@@SVG@@": svg_timeline(m), "@@STATS@@": stats,
                 "@@VIDEO@@": b64, "@@STEP@@": str(len(steps)), "@@TOTAL@@": str(len(PLAN)),
                 "@@DUR@@": f"{m['total']:.3f}"}.items():
        page = page.replace(k, v)
    open(VIEWER, "w").write(page)


def cmd_apply(op_json):
    os.makedirs(LIVE, exist_ok=True)
    st = load()
    ops = st["ops"] + [json.loads(op_json)]
    t0 = time.perf_counter()
    p, tr, m, total = build(ops)              # raises on invalid op; state is only saved on success
    build_ms = (time.perf_counter() - t0) * 1000
    t1 = time.perf_counter()
    render(p, tr, PREVIEW)
    render_ms = (time.perf_counter() - t1) * 1000
    st["ops"] = ops
    st["steps"].append({"build_ms": round(build_ms, 1), "render_ms": round(render_ms, 1), "frames": total})
    json.dump(st, open(STATE, "w"), indent=1)
    write_viewer(st)
    print(json.dumps({"step": len(ops), "frames": total, "seconds": round(total / FPS, 2),
                      "build_ms": round(build_ms, 1), "render_ms": round(render_ms, 1),
                      "preview_kb": os.path.getsize(PREVIEW) // 1024}))


TEMPLATE = open(os.path.join(HERE, "viewer_template.html")).read() if os.path.exists(os.path.join(HERE, "viewer_template.html")) else ""

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "reset":
        for f in (STATE, PREVIEW, VIEWER):
            if os.path.exists(f):
                os.remove(f)
        print("reset")
    elif cmd == "apply":
        cmd_apply(sys.argv[2])
    elif cmd == "viewer":
        write_viewer(load())
    else:
        sys.exit(__doc__)
