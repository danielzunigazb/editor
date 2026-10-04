#!/usr/bin/python3.12
"""Live-edit engine: an LLM drives the editor by sending ONE declarative op at a time.

  live.py reset                      start a new session
  live.py apply '<json op>'          append op, rebuild timeline with MLT, render preview, write viewer.html
  live.py viewer                     only regenerate viewer.html from the saved state

Ops are plugins (mltedit/plugins/ops, kind "op"); the overlays they make are layer plugins (mltedit/plugins/layers, kind "layer").
This module only orchestrates: layout() lets each op plugin validate its op and fill a LayoutState, core/timeline.py resolves it on the
frame grid, and build() turns it into an MLT tractor, asking each layer plugin for its source, placement and keyframes.
The timeline is always rebuilt by replaying the op list (declarative, so any op can be edited/removed later).
Needs X11 for the qtblend transition: run under xvfb-run.

State: one EngineContext (core/context.py), CTX. `live.W`, `live.THEME`, ... read and write its fields (module properties below).
"""
import base64, html, json, os, subprocess, sys, time, types

from . import anim as animmod
from . import errors
from . import ops as O
from . import transitions
from .config import S
from .core import overlay, timeline
from .core.context import EngineContext
from .core.render import render
from .ops import common as _common

CTX = EngineContext()
LIVE = S.live_dir
STATE = os.path.join(LIVE, "state.json")
PREVIEW = os.path.join(LIVE, "preview.mp4")
VIEWER = os.path.join(LIVE, "viewer.html")

# names kept for scripts and tests written against the single-file engine
_BBOX = overlay._BBOX
_crop_to_content = overlay.crop_to_content
_merge_decor = overlay.merge_layers
_check_finite = timeline.check_finite
pack_audio, gain_curve, DUCK_RAMP_S = timeline.pack_audio, timeline.gain_curve, timeline.DUCK_RAMP_S
POS_PIP, POS_IMG = _common.POS_PIP, _common.POS_IMG
callout_presets = _common.callout_presets
MAX_AUDIOS, MAX_AUDIO_TRACKS, MAX_LAYERS, MAX_LAYER_TRACKS = S.max_audios, S.max_audio_tracks, S.max_layers, S.max_layer_tracks
render = render

# What the CLI session intends to do, shown as "pending" in the viewer until each step lands.
PLAN = [
    {"op": "add", "src": "A"},
    {"op": "cut", "clip": 0, "at": 3.0},
    {"op": "add", "src": "B"},
    {"op": "crossfade", "between": [0, 1], "dur": 1.0},
    {"op": "fade", "in": 0.5, "out": 1.0},
    {"op": "pip", "src": "C", "start": 2.0, "dur": 3.0, "pos": "top-right", "scale": 0.3, "opacity": 0.9},
]


def _opt(name):                         # evaluated per build so tests can toggle them (MLT_OPT_CROP -> setting opt_crop)
    return getattr(S, name.lower().replace("mlt_", "", 1))


def _theme_key(o, where, ctx=None):
    return _common.theme_key(o, where, ctx or CTX)


def resolve_style(o, ctx=None):
    return _common.resolve_style(o, ctx or CTX)


# ---------------------------------------------------------------- timeline model (pure python)
def layout(ops, ctx=None):
    """Compute entry durations/starts and effects from the op list (for validation + the SVG), each op through its plugin.
    Raises EditError (a ValueError with a stable `code`) with a message the caller can show verbatim; a malformed op is an INVALID_ARGUMENT, never
    a KeyError or TypeError."""
    try:
        return _layout(ops, ctx)
    except ValueError as e:
        raise errors.as_edit_error(e) from None


def _layout(ops, ctx=None):
    ctx = ctx or CTX
    for n, o in enumerate(ops):
        timeline.check_finite(o, f"op {n} ({o.get('op')})")
    st = O.LayoutState(ctx)
    for n, o in enumerate(ops):
        k = o.get("op")
        where = f"op {n} ({k})"
        plug = O.get_op(k) if isinstance(k, str) else None
        if o.get("anim") and not (plug and plug.animatable):
            raise ValueError(f"{where}: anim is not supported on '{k}' (use it on {', '.join(O.animatable())}); a silently ignored animation would be worse")
        if plug is None:
            raise ValueError(f"{where}: unknown op; known: {', '.join(O.op_names())}")
        n_layers, n_audios = len(st.layers), len(st.audios)
        try:
            plug.layout(plug.normalize(o, ctx), n, where, st)
        except (KeyError, TypeError, AttributeError, IndexError) as e:
            raise errors.EditError("INVALID_ARGUMENT", where, f"malformed op ({type(e).__name__}: {e}); see list_styles for the fields each edit takes") from None
        if o.get("anchor"):                                  # everything this edit placed on the timeline follows its anchor (see core/timeline.resolve)
            for item in st.layers[n_layers:] + st.audios[n_audios:]:
                item["anchor"] = o["anchor"]
    return timeline.resolve(st)


def check_new_op(op, ctx=None):
    """Extra validation for a freshly added op that needs the frame size (call with the context bound to the EXPORT resolution): text must
    fit on screen. Raises ValueError."""
    plug = O.get_op(op.get("op"))
    if plug is not None:
        try:
            plug.check_new(op, ctx or CTX)
        except ValueError as e:
            raise errors.as_edit_error(e) from None


# ---------------------------------------------------------------- MLT build (the actual engine)
def build(ops, ctx=None):
    import mlt7
    ctx = ctx or CTX
    W, H, FPS = ctx.W, ctx.H, ctx.FPS
    m = layout(ops, ctx)
    mlt7.Factory.init()
    p = mlt7.Profile()
    p.set_width(W); p.set_height(H); p.set_frame_rate(FPS, 1)
    p.set_sample_aspect(1, 1); p.set_display_aspect(W, H); p.set_progressive(1); p.set_explicit(1)
    fr = ctx.fr

    base = mlt7.Playlist(p)
    for e in m["entries"]:
        prod = mlt7.Producer(p, ctx.CLIPS[e["src"]])    # default loader: do NOT use "avformat" directly
        assert prod.is_valid(), f"cannot open {e['src']}"
        base.append(prod, e["in_f"], e["in_f"] + e["dur_f"] - 1)
    # crossfades, ascending; each earlier mix inserts one extra playlist entry before later clips
    done = 0
    for a in sorted(m["xfades_f"]):
        n = m["xfades_f"][a]
        base.mix(a + done, n, transitions.apply(mlt7, p, m["xstyles"].get(a, S.default_transition), W, H, ctx.CACHE, n))
        base.mix_add(a + done, mlt7.Transition(p, "mix"))
        done += 1

    tr = mlt7.Tractor(p)
    mt = tr.multitrack()
    mt.connect(base, 0)
    total = base.get_playtime()
    assert total == m["total_f"], f"timeline model ({m['total_f']} frames) and MLT ({total}) disagree"

    layers_to_build = overlay.merge_layers(m["layers"]) if _opt("MLT_OPT_MERGE") else m["layers"]
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
        cursor, kfs, audible, rkf, any_rot = 0, [], False, [], False
        t0 = plan[0][1]                                  # keyframe positions are relative to the transition's `in`
        for L, s0, n in plan:
            plug = O.get_layer(L["kind"])
            src, extra = plug.source(L, ctx)
            audible = audible or plug.audible
            crop = None
            if plug.croppable and _opt("MLT_OPT_CROP"):
                crop = overlay.crop_to_content(src, W, H)  # draw only the visible rectangle, 1:1 (full-frame PNGs cost ~2.4 s/s at 4K)
                if crop:
                    src = crop[0]
            prod = mlt7.Producer(p, src)
            if not prod.is_valid():
                raise RuntimeError(f"cannot open overlay source {src}")
            first = plug.first_frame(L, ctx)
            reveal = plug.crop_rect(L, ctx, n, extra)
            if reveal:                                    # a reveal animation: qtcrop pads everything outside the animated rect with transparency
                cf = mlt7.Filter(p, "qtcrop")
                cf.set("rect", reveal)
                prod.attach(cf)
            if s0 > cursor:
                lay.blank(s0 - cursor - 1)   # Playlist.blank(out) takes the OUT POINT: it creates out+1 frames
            lay.append(prod, first, first + n - 1)
            cursor = s0 + n
            rots = None
            pts = plug.keys(L, ctx, s0, n, extra)
            if pts is None:
                x, y, w, h, op, ramp = plug.place(L, ctx, n, crop, extra)
                if L.get("anim"):                         # animated: per-frame samples (position, size, opacity, rotation)
                    smp = animmod.sample(L["anim"], (x, y, w, h), W, H, n, FPS, min(L.get("fade", plug.default_fade), n / FPS / 2), op)
                    pts = [(s0 + f_, "{:.2f} {:.2f} {:.2f} {:.2f} {:.3f}".format(*r_, o_)) for f_, r_, o_, _ in smp]
                    rots = [(s0 + f_, round(rt_, 3)) for f_, _, _, rt_ in smp]
                    any_rot = any_rot or any(v_ for _, v_ in rots)
                else:
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
        if audible:                                      # only video clips carry audio; text/image tracks are silent
            mix = mlt7.Transition(p, "mix"); mix.set("sum", 1)
            mix.set_in_and_out(t0, end)
            tr.plant_transition(mix, 0, ti)

    ti_audio = len(by_track) + 1                       # audio tracks follow the visual ones with CONTIGUOUS numbers (a gap segfaults MLT)
    for track in pack_audio(m.get("audios", [])):      # clips that never overlap share one track (one playlist, blanks between them)
        lay = mlt7.Playlist(p)
        mt.connect(lay, ti_audio)
        at, vkeys = 0, []
        for a in track:
            prod = mlt7.Producer(p, a["path"])
            if not prod.is_valid():
                raise RuntimeError(f"cannot open audio {a['path']}")
            length = prod.get_length()
            if a["start_f"] > at:
                lay.blank(a["start_f"] - at - 1)         # Playlist.blank(out) takes the OUT POINT: it creates out+1 frames
            left, first, used = a["n_f"], min(a["in_f"], max(0, length - 1)), 0
            while left > 0:                              # a looped clip repeats the source (first pass from `in`, then from its start)
                span = min(left, length - first)
                if span < 1:
                    break
                lay.append(prod, first, first + span - 1)
                left -= span
                used += span
                first = 0
                if not a["loop"]:
                    break
            at = a["start_f"] + used
            vkeys += gain_curve(a) or [(a["start_f"], a["vol"]), (a["start_f"] + max(0, used - 1), a["vol"])]
        seen_f, ordered = set(), []
        for f_, v_ in sorted(vkeys):                     # volume.level is dB; keyframes are absolute timeline frames; one curve per track (clips do not overlap)
            if f_ not in seen_f:
                seen_f.add(f_); ordered.append((f_, v_))
        vf = mlt7.Filter(p, "volume")
        vf.set("level", ";".join(f"{f_}={v_}" for f_, v_ in ordered) if len(ordered) > 1 else str(ordered[0][1]))
        vf.set_in_and_out(0, total - 1)
        lay.attach(vf)
        mix = mlt7.Transition(p, "mix"); mix.set("sum", 1)
        mix.set_in_and_out(track[0]["start_f"], at - 1)
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


# ---------------------------------------------------------------- state + viewer
def load():
    return json.load(open(STATE)) if os.path.exists(STATE) else {"ops": [], "steps": []}


def describe(o):
    """(Spanish title, short code) of an op for the viewer, from its plugin."""
    plug = O.get_op(o.get("op"))
    return plug.describe(o) if plug else (str(o.get("op")), str(o.get("op")))


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
        label, c = O.get_layer(L["kind"]).svg(L)
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
             f'<div><dt>Resolución</dt><dd>{CTX.W}×{CTX.H}</dd></div>') if last else ""
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
    print(json.dumps({"step": len(ops), "frames": total, "seconds": round(total / CTX.FPS, 2),
                      "build_ms": round(build_ms, 1), "render_ms": round(render_ms, 1),
                      "preview_kb": os.path.getsize(PREVIEW) // 1024}))


TEMPLATE = open(S.viewer_template).read() if os.path.exists(S.viewer_template) else ""


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else ""
    for k, p in CTX.CLIPS.items():                     # the CLI's demo sources (setting demo_clips): measure their lengths
        if k not in CTX.CLIP_LEN and os.path.exists(p):
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p], capture_output=True, text=True).stdout.strip()
            CTX.CLIP_LEN[k] = float(out or 0)
    if cmd == "reset":
        for f in (STATE, PREVIEW, VIEWER):
            if os.path.exists(f):
                os.remove(f)
        print("reset")
    elif cmd == "apply":
        cmd_apply(argv[1])
    elif cmd == "viewer":
        write_viewer(load())
    else:
        sys.exit(__doc__)


class _EngineModule(types.ModuleType):
    """`engine.W = 1920` / `live.THEME` read and write the shared EngineContext (CTX); ANIMATABLE is derived from the op plugins."""
    ANIMATABLE = property(lambda self: O.animatable())


for _f in EngineContext.FIELDS:
    setattr(_EngineModule, _f, property(lambda self, f=_f: getattr(CTX, f), lambda self, v, f=_f: setattr(CTX, f, v)))
sys.modules[__name__].__class__ = _EngineModule


if __name__ == "__main__":
    main()
