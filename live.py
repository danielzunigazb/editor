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

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(HERE, "out", "live")
STATE = os.path.join(LIVE, "state.json")
PREVIEW = os.path.join(LIVE, "preview.mp4")
VIEWER = os.path.join(LIVE, "viewer.html")
CLIPS = {k: os.path.join(HERE, "media", f"clip_{k.lower()}.mp4") for k in "ABC"}
CLIP_LEN = {"A": 6.0, "B": 5.0, "C": 4.0}
W, H, FPS = 640, 360, 25

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
    entries, xfades, fade, pip = [], {}, None, None
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
            if o.get("pos", "top-right") not in ("top-right", "top-left", "bottom-right", "bottom-left"):
                raise ValueError(f"{where}: pos must be top-right|top-left|bottom-right|bottom-left")
            if not 0 < o.get("scale", 0.3) <= 1 or not 0 <= o.get("opacity", 1.0) <= 1:
                raise ValueError(f"{where}: scale must be in (0,1] and opacity in [0,1]")
            if o["start"] < 0 or o["dur"] <= 0 or o.get("in", 0.0) < 0 or o.get("in", 0.0) + o["dur"] > CLIP_LEN[src] + 1e-6:
                raise ValueError(f"{where}: needs start>=0, dur>0 and in+dur <= source length {CLIP_LEN[src]:g}s")
            pip = dict(o)
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
    if pip and pip["start"] >= total:
        raise ValueError(f"pip starts at {pip['start']:g}s but the timeline ends at {total:g}s")
    return {"entries": entries, "xfades": xfades, "fade": fade, "pip": pip, "total": total}


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

    if m["pip"]:
        o = m["pip"]
        clip = mlt7.Producer(p, CLIPS[o["src"]])
        lay = mlt7.Playlist(p)
        lay.blank(fr(o["start"]))
        lay.append(clip, fr(o.get("in", 0.0)), fr(o.get("in", 0.0)) + fr(o["dur"]) - 1)
        mt.connect(lay, 1)
        s = o.get("scale", 0.3); mg = 0.04
        w, h = W * s, H * s
        x = W * mg if "left" in o["pos"] else W * (1 - mg) - w
        y = H * mg if "top" in o["pos"] else H * (1 - mg) - h
        op, n, ramp = o.get("opacity", 1.0), fr(o["dur"]) - 1, 6
        rect = lambda a: f"{x:.0f} {y:.0f} {w:.0f} {h:.0f} {a}"
        c = mlt7.Transition(p, "qtblend")   # composite/affine ignore opacity; qtblend honours it (needs X11)
        assert c.is_valid(), "qtblend unavailable: run under xvfb-run"
        c.set("rect", f"0={rect(0)};{ramp}={rect(op)};{n-ramp}={rect(op)};{n}={rect(0)}")  # keyframes are relative to `in`
        c.set_in_and_out(fr(o["start"]), fr(o["start"]) + n)
        tr.plant_transition(c, 0, 1)
        mix = mlt7.Transition(p, "mix"); mix.set("sum", 1)
        mix.set_in_and_out(fr(o["start"]), fr(o["start"]) + n)
        tr.plant_transition(mix, 0, 1)

    if m["fade"]:
        fi, fo = fr(m["fade"]["in"]), fr(m["fade"]["out"])
        for service, lo, hi in (("brightness", 0, 1), ("volume", -60, 0)):   # volume.level is dB
            f = mlt7.Filter(p, service)
            f.set("level", f"0={lo};{max(fi,1)}={hi};{total-max(fo,1)}={hi};{total-1}={lo}")
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
    return (f"Picture-in-picture: clip {o['src']} de {o['start']:g} a {o['start']+o['dur']:g} s, arriba a la derecha",
            f"pip {o['src']} @ {o['start']:g}s {o['dur']:g}s")


def svg_timeline(m):
    total = max(m["total"], 1.0)
    pad_l, pad_r, width = 44, 12, 800
    sx = lambda t: pad_l + (width - pad_l - pad_r) * t / total
    rows = {"V1": 30, "V2": 66, "FX": 102}
    out = [f'<svg class="tl" viewBox="0 0 {width} 140" role="img" aria-label="Línea de tiempo">']
    for t in range(int(total) + 1):
        x = sx(t)
        out.append(f'<line class="grid" x1="{x:.1f}" y1="18" x2="{x:.1f}" y2="124"/>'
                   f'<text class="tick" x="{x:.1f}" y="12" text-anchor="middle">{t}s</text>')
    for name, y in rows.items():
        out.append(f'<text class="lane" x="6" y="{y+20}">{name}</text>'
                   f'<rect class="laneBg" x="{pad_l}" y="{y}" width="{width-pad_l-pad_r}" height="28"/>')
    for i, e in enumerate(m["entries"]):
        x0, x1 = sx(e["start"]), sx(e["start"] + e["dur"])
        out.append(f'<rect class="clip c{e["src"].lower()}" x="{x0:.1f}" y="{rows["V1"]}" width="{x1-x0:.1f}" height="28" rx="3"/>'
                   f'<text class="clipT" x="{x0+7:.1f}" y="{rows["V1"]+18}">{i} · {e["src"]} · {e["dur"]:g}s</text>')
    for a, d in m["xfades"].items():
        e = m["entries"][a + 1]
        x0, x1 = sx(e["start"]), sx(e["start"] + d)
        out.append(f'<rect class="xf" x="{x0:.1f}" y="{rows["V1"]}" width="{x1-x0:.1f}" height="28"/>')
    if m["pip"]:
        o = m["pip"]; x0, x1 = sx(o["start"]), sx(o["start"] + o["dur"])
        out.append(f'<rect class="clip c{o["src"].lower()}" x="{x0:.1f}" y="{rows["V2"]}" width="{x1-x0:.1f}" height="28" rx="3"/>'
                   f'<text class="clipT" x="{x0+7:.1f}" y="{rows["V2"]+18}">{o["src"]} · PiP</text>')
    if m["fade"]:
        f = m["fade"]
        if f["in"]:
            out.append(f'<polygon class="fx" points="{sx(0):.1f},{rows["FX"]+28} {sx(0):.1f},{rows["FX"]+2} {sx(f["in"]):.1f},{rows["FX"]+28}"/>')
        if f["out"]:
            out.append(f'<polygon class="fx" points="{sx(total):.1f},{rows["FX"]+28} {sx(total):.1f},{rows["FX"]+2} {sx(total-f["out"]):.1f},{rows["FX"]+28}"/>')
    out.append(f'<line id="ph" class="ph" x1="{pad_l}" y1="16" x2="{pad_l}" y2="128"/></svg>')
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
