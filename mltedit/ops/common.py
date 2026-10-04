"""Validation helpers shared by op plugins."""
import os

from .. import anim as animmod
from .. import themes
from ..render import text as textrender

POS_PIP = ("top-right", "top-left", "bottom-right", "bottom-left")
POS_IMG = POS_PIP + ("center",)
_IMG_CACHE = {}


def callout_presets():
    """Animation presets a callout accepts (the preset plugins flagged callout=True)."""
    return tuple(n for n in animmod.PRESETS if animmod.preset(n).callout)


def off_video(spec):
    """Presets in an anim spec that cannot run on a video layer (on_video=False, e.g. crop reveals of a still)."""
    return [v for v in (spec or {}).values() if isinstance(v, str) and v in animmod.PRESETS and not animmod.preset(v).on_video]


def need_time(o, where):
    if not (o.get("start", -1) >= 0 and 0 < o.get("dur", 0) <= 3600):
        raise ValueError(f"{where}: needs start>=0 and 0 < dur <= 3600")


def clean(text, where, style="classic"):
    try:
        return textrender.clean(text, style)
    except ValueError as e:
        raise ValueError(f"{where}: {e}")


def anim(o, where, ctx, callout=False):
    """Normalised animation spec of an op (None = static: the original fade ramps). With the project's motion on, an op that names no
    anim (None) gets its template's own; anim={} still means "no animation". The template's times are squeezed to fit short items.
    callout=True: the op is a callout (only presets flagged callout, no keys/rotate/scale)."""
    dur = o.get("dur") if isinstance(o.get("dur"), (int, float)) else None
    spec = o.get("anim")
    if spec is None and ctx.MOTION:
        spec = dict(ctx.THEME.motion.get(o.get("op"), {})) or None
        if spec and dur:
            tot = (spec.get("in_s") or 0) + (spec.get("out_s") or 0)
            if tot > 0.8 * dur:
                k = 0.8 * dur / tot
                spec = {**spec, **{x: round(spec[x] * k, 3) for x in ("in_s", "out_s") if spec.get(x)}}
    spec = animmod.validate(spec, where, dur)
    if spec and callout:
        allowed = callout_presets()
        bad = [v for v in (spec["in"], spec["out"]) if v not in allowed]
        if bad or spec["keys"] or spec["rotate"] or spec["scale"]:
            raise ValueError(f"{where}: a callout takes in/out among {allowed} (no keys, rotate or scale: it follows its own track)")
    elif spec and any(animmod.preset(v).callout_only for v in (spec["in"], spec["out"])):
        only = next(v for v in (spec["in"], spec["out"]) if animmod.preset(v).callout_only)
        raise ValueError(f"{where}: anim '{only}' is for callouts only")
    return spec


def theme_key(o, where, ctx):
    """{"name","accent"} of the template a graphic/lower third/callout is drawn in: the op's own `theme` if given, else the project's."""
    t = o.get("theme")
    if t in (None, "", "auto"):
        return {"name": ctx.THEME.name, "accent": ctx.THEME.accent}
    if t not in themes.THEMES:
        raise ValueError(f"{where}: unknown template '{t}'; choose one of {themes.NAMES}")
    return {"name": t, "accent": ctx.THEME.accent if t == ctx.THEME.name else themes.THEMES[t].accent}


def resolve_style(o, ctx):
    """Concrete textrender style of a text/subtitles op: an explicit name wins; None/"auto" follows the project's template
    (its subtitle style for ops that set `subtitle_style`, else its title style)."""
    style = o.get("style")
    if style in (None, "", "auto"):
        from . import get_op
        plug = get_op(o.get("op"))
        return ctx.THEME.subtitle_style if getattr(plug, "subtitle_style", False) else ctx.THEME.title_style
    return style


def text_style(o, where, ctx):
    pos, size, color = o.get("pos", "bottom"), o.get("size", 0.06), o.get("color") or None
    style, upper, orn = resolve_style(o, ctx), o.get("uppercase"), o.get("ornament") or None
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
    if box is None:                                       # the style decides (template panels), else the op's default (subtitles on, titles off)
        from . import get_op
        box = textrender.STYLES[style].get("box_default", getattr(get_op(o.get("op")), "box_default", False))
    return {"pos": pos, "size": float(size), "color": color, "box": bool(box), "fade": fade,
            "style": style, "uppercase": upper, "ornament": orn}


def image_aspect(path, where):
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


def gfx_layer(n, o, gk, params, where, ctx):
    need_time(o, where)
    if not 0 <= o.get("opacity", 1.0) <= 1 or o.get("fade", 0.4) < 0:
        raise ValueError(f"{where}: opacity must be in [0,1] and fade >= 0")
    return {"kind": "graphic", "op": n, "start": float(o["start"]), "dur": float(o["dur"]), "gk": gk, "params": params,
            "opacity": float(o.get("opacity", 1.0)), "fade": float(o.get("fade", 0.4)), "anim": anim(o, where, ctx)}


def check_text_fits(texts, style, up, ctx, cue_prefix=False):
    for i, (t, size) in enumerate(texts):
        try:
            textrender.layout_text(textrender.clean(t, style), ctx.W, ctx.H, size, strict=True, style=style, uppercase=up)
        except ValueError as e:
            raise ValueError(f"{'cue ' + str(i) + ': ' if cue_prefix else ''}{e}")
