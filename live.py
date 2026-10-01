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
import base64, html, json, os, re, subprocess, sys, time

import textrender

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
def layout(ops):
    """Compute entry durations/starts and effects from the op list (for validation + the SVG).
    Raises ValueError with a message the caller can show verbatim."""
    entries, xfades, fade, layers = [], {}, None, []
    for n, o in enumerate(ops):
        k = o.get("op")
        where = f"op {n} ({k})"
        if k == "add":
            src = o.get("src")
            if src not in CLIP_LEN:
                raise ValueError(f"{where}: unknown source '{src}'; known: {sorted(CLIP_LEN)}")
            start, end = float(o.get("in", 0.0)), float(o.get("end", CLIP_LEN[src]))
            if not (0 <= start < end <= CLIP_LEN[src] + 1e-6):
                raise ValueError(f"{where}: range {start:g}-{end:g}s outside source '{src}' (0-{CLIP_LEN[src]:g}s)")
            entries.append({"src": src, "in": start, "dur": end - start})
        elif k == "cut":
            i = o.get("clip")
            if not isinstance(i, int) or not 0 <= i < len(entries):
                raise ValueError(f"{where}: no timeline entry {i} (have {len(entries)})")
            e = entries[i]
            if not 0 < o["at"] < e["dur"]:
                raise ValueError(f"{where}: cut at {o['at']:g}s is outside entry {i} (length {e['dur']:g}s)")
            e["dur"] = o["at"]
        elif k == "crossfade":
            a_, b_ = o["between"]
            if b_ != a_ + 1 or a_ < 0 or b_ >= len(entries):
                raise ValueError(f"{where}: needs two adjacent existing entries (have {len(entries)})")
            if o["dur"] <= 0:
                raise ValueError(f"{where}: dur must be > 0")
            xfades[a_] = o["dur"]
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
                           "scale": float(o.get("scale", 0.3)), "opacity": float(o.get("opacity", 1.0))})
        elif k == "text":
            style = _text_style(o, where)
            if not (o.get("start", -1) >= 0 and 0 < o.get("dur", 0) <= 3600):
                raise ValueError(f"{where}: needs start>=0 and 0 < dur <= 3600")
            layers.append({"kind": "text", "op": n, "start": float(o["start"]), "dur": float(o["dur"]), **style,
                           "text": _clean(o.get("text"), where)})
        elif k == "subtitles":
            cues = o.get("cues")
            if not isinstance(cues, list) or not 1 <= len(cues) <= 300:
                raise ValueError(f"{where}: needs 1-300 cues")
            style = _text_style({"pos": "bottom", "size": 0.05, "box": True, "fade": 0.0, **o}, where)
            for ci, c in enumerate(cues):
                try:
                    st_, en_ = float(c["start"]), float(c["end"])
                except (KeyError, TypeError, ValueError):
                    raise ValueError(f"{where}: cue {ci} needs numeric start and end")
                if st_ < 0 or en_ <= st_:
                    raise ValueError(f"{where}: cue {ci} has an invalid time range {st_:g}-{en_:g}s")
                layers.append({"kind": "text", "op": n, "sub": ci, "start": st_, "dur": en_ - st_, **style,
                               "text": _clean(c.get("text"), f"{where} cue {ci}")})
        elif k == "image":
            if o.get("pos", "center") not in POS_IMG:
                raise ValueError(f"{where}: pos must be one of {POS_IMG}")
            if not 0 < o.get("scale", 0.3) <= 1 or not 0 <= o.get("opacity", 1.0) <= 1:
                raise ValueError(f"{where}: scale must be in (0,1] and opacity in [0,1]")
            if not (o.get("start", -1) >= 0 and 0 < o.get("dur", 0) <= 3600):
                raise ValueError(f"{where}: needs start>=0 and 0 < dur <= 3600")
            layers.append({"kind": "image", "op": n, "start": float(o["start"]), "dur": float(o["dur"]),
                           "path": o["path"], "aspect": _image_aspect(o["path"], where), "pos": o.get("pos", "center"),
                           "scale": float(o.get("scale", 0.3)), "opacity": float(o.get("opacity", 1.0))})
        else:
            raise ValueError(f"{where}: unknown op")
    for i, e in enumerate(entries):   # a clip must be long enough for the dissolves on both of its sides
        need = xfades.get(i - 1, 0.0) + xfades.get(i, 0.0)
        if need > e["dur"] + 1e-6:
            raise ValueError(f"entry {i} is {e['dur']:g}s long but its crossfades need {need:g}s")
    t = 0.0
    for i, e in enumerate(entries):
        e["start"] = t
        t += e["dur"] - xfades.get(i, 0.0)   # next entry starts `dur` earlier
    total = max((e["start"] + e["dur"] for e in entries), default=0.0)
    if fade and fade["in"] + fade["out"] > total + 1e-6:
        raise ValueError(f"fade in+out ({fade['in']+fade['out']:g}s) is longer than the timeline ({total:g}s)")
    # Overlays live in TIMELINE time: they do not move when earlier clips are edited. Anything now beyond the end
    # (e.g. after a cut) is clipped or dropped with a warning instead of rejecting the edit.
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
    kept.sort(key=lambda L: (L["start"], L["op"], L.get("sub", 0)))
    first_pip = next((L for L in kept if L["kind"] == "pip"), None)
    return {"entries": entries, "xfades": xfades, "fade": fade, "layers": kept, "pip": first_pip,
            "warnings": warnings, "total": total}


def _label(L):
    if L["kind"] == "text":
        return f"text {L['text'][:24]!r}"
    return f"{L['kind']} {L.get('src') or os.path.basename(L.get('path', ''))}"


def _clean(text, where):
    try:
        return textrender.clean(text)
    except ValueError as e:
        raise ValueError(f"{where}: {e}")


def _text_style(o, where):
    pos, size, color = o.get("pos", "bottom"), o.get("size", 0.06), o.get("color", "#ffffff")
    fade = float(o.get("fade", 0.15))
    if pos not in textrender.POSITIONS:
        raise ValueError(f"{where}: pos must be one of {textrender.POSITIONS}")
    if not isinstance(size, (int, float)) or not 0.02 <= size <= 0.2:
        raise ValueError(f"{where}: size is a fraction of the frame height, between 0.02 and 0.2")
    if not textrender.COLOR_RE.match(str(color)):
        raise ValueError(f"{where}: color must look like #RRGGBB")
    if fade < 0:
        raise ValueError(f"{where}: fade must be >= 0")
    return {"pos": pos, "size": float(size), "color": color, "box": bool(o.get("box", False)), "fade": fade}


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
    texts = []
    if op.get("op") == "text":
        texts = [(op.get("text"), op.get("size", 0.06))]
    elif op.get("op") == "subtitles":
        texts = [(c.get("text"), op.get("size", 0.05)) for c in op.get("cues", [])]
    for i, (t, size) in enumerate(texts):
        try:
            textrender.layout_text(textrender.clean(t), W, H, size, strict=True)
        except ValueError as e:
            raise ValueError(f"{'cue ' + str(i) + ': ' if op.get('op') == 'subtitles' else ''}{e}")


# ---------------------------------------------------------------- MLT build (the actual engine)
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
        base.append(prod, fr(e["in"]), fr(e["in"]) + fr(e["dur"]) - 1)
    # crossfades, ascending; each earlier mix inserts one extra playlist entry before later clips
    done = 0
    for a in sorted(m["xfades"]):
        n = fr(m["xfades"][a])
        base.mix(a + done, n, mlt7.Transition(p, "luma"))
        base.mix_add(a + done, mlt7.Transition(p, "mix"))
        done += 1

    tr = mlt7.Tractor(p)
    mt = tr.multitrack()
    mt.connect(base, 0)
    total = base.get_playtime()

    by_track = {}
    for L in m["layers"]:
        by_track.setdefault(L["track"], []).append(L)
    for ti in sorted(by_track):
        lay = mlt7.Playlist(p)
        mt.connect(lay, ti)                  # connect first, then fill: transitions refer to this track index
        cursor, plan = 0, []
        for L in by_track[ti]:
            s0 = max(fr(L["start"]), cursor)             # frame rounding must never overlap two layers on a track
            n = min(max(1, fr(L["dur"])), total - s0)
            if n < 1:
                continue
            plan.append((L, s0, n))
            cursor = s0 + n
        if not plan:
            continue
        cursor, kfs, has_pip = 0, [], False
        t0 = plan[0][1]                                  # keyframe positions are relative to the transition's `in`
        for L, s0, n in plan:
            if L["kind"] == "text":
                src = textrender.render_text_png(L["text"], W, H, L["pos"], L["size"], L["color"], L["box"],
                                                 cache_dir=CACHE, strict=False)
            elif L["kind"] == "image":
                src = os.path.abspath(os.path.expanduser(L["path"]))
            else:
                src = CLIPS[L["src"]]; has_pip = True
            prod = mlt7.Producer(p, src)
            if not prod.is_valid():
                raise RuntimeError(f"cannot open overlay source {src}")
            first = fr(L.get("in", 0.0)) if L["kind"] == "pip" else 0
            if s0 > cursor:
                lay.blank(s0 - cursor - 1)   # Playlist.blank(out) takes the OUT POINT: it creates out+1 frames
            lay.append(prod, first, first + n - 1)
            cursor = s0 + n
            if L["kind"] == "text":
                x, y, w, h = 0, 0, W, H
                op, ramp = 1.0, min(fr(L["fade"]), (n - 1) // 2)
            else:
                mg = 0.04
                w = W * L["scale"]
                h = H * L["scale"] if L["kind"] == "pip" else w / L["aspect"]
                x = W * mg if "left" in L["pos"] else (W - w) / 2 if L["pos"] == "center" else W * (1 - mg) - w
                y = H * mg if "top" in L["pos"] else (H - h) / 2 if L["pos"] == "center" else H * (1 - mg) - h
                op, ramp = L["opacity"], min(6, (n - 1) // 2)
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
        end = cursor - 1
        c = mlt7.Transition(p, "qtblend")   # composite/affine ignore opacity; qtblend honours it (needs X11)
        assert c.is_valid(), "qtblend unavailable: run under xvfb-run / with DISPLAY"
        c.set("rect", ";".join(kfs))
        c.set_in_and_out(t0, end)
        tr.plant_transition(c, 0, ti)    # ONE qtblend per overlay track, covering all of its layers (per-layer keyframes)
        if has_pip:                                      # only video clips carry audio; text/image tracks are silent
            mix = mlt7.Transition(p, "mix"); mix.set("sum", 1)
            mix.set_in_and_out(t0, end)
            tr.plant_transition(mix, 0, ti)

    if m["fade"]:
        fi, fo = fr(m["fade"]["in"]), fr(m["fade"]["out"])
        for service, lo, hi in (("brightness", 0, 1), ("volume", -60, 0)):   # volume.level is dB
            f = mlt7.Filter(p, service)
            kf = [f"0={lo};{fi}={hi}"] if fi > 0 else [f"0={hi}"]      # no fade-in => frame 0 must stay at full level
            if fo > 0:
                kf.append(f"{total-fo}={hi};{total-1}={lo}")
            f.set("level", ";".join(kf))
            f.set_in_and_out(0, total - 1)          # attached filters default to in=out=0
            tr.attach(f)
    return p, tr, m, total


def render(p, tr, out, preset="ultrafast", crf="30", abr="64k"):
    """MLT composes -> NUT over a FIFO -> ffmpeg CLI encodes the small preview."""
    import mlt7
    fifo = out + ".nut"
    if os.path.exists(fifo):
        os.remove(fifo)
    os.mkfifo(fifo)
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-i", fifo, "-c:v", "libx264", "-preset", preset,
                           "-crf", str(crf), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", abr,
                           "-movflags", "+faststart", out], stderr=subprocess.DEVNULL)
    c = mlt7.Consumer(p, "avformat", fifo)
    for k, v in dict(f="nut", vcodec="rawvideo", acodec="pcm_s16le", real_time="0").items():
        c.set(k, v)
    c.connect(tr); c.run(); c.stop()
    ff.wait(); os.remove(fifo)


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
                 "image": "imagen"}[L["kind"]]
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
