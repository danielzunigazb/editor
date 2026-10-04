"""Overlay edits: pip, text, subtitles, graphics, lower thirds, images, callouts, and animating them."""

from .. import ops as O
from .. import project as P
from .. import server as sv
from ..render import text as textrender
from . import builder, edit_tool

def _theme_arg(theme):
    return None if theme in (None, "", "auto") else theme


@builder("add_pip")
def _b_add_pip(source, start_s, dur_s, position="top-right", scale=0.3, opacity=1.0, source_in_s=0.0, anim=None):
    return {"op": "pip", "src": source, "start": start_s, "dur": dur_s, "pos": position, "scale": scale,
            "opacity": opacity, "in": source_in_s, "anim": anim or None}


@builder("add_text")
def _b_add_text(text, start_s, dur_s, position="bottom", size=0.06, style="auto", color="", box=None,
                uppercase=None, ornament="", fade_s=0.15, anim=None):
    return {"op": "text", "text": text, "start": start_s, "dur": dur_s, "pos": position, "size": size, "style": style,
            "color": color or None, "box": box, "uppercase": uppercase, "ornament": ornament or None, "fade": fade_s, "anim": anim or None}


@builder("add_subtitles")
def _b_add_subtitles(srt_path="", cues=None, offset_s=0.0, position="bottom", size=0.05, style="auto", color="",
                     box=None):
    if bool(srt_path) == bool(cues):
        raise ValueError("give exactly one of srt_path or cues")
    items = textrender.parse_srt(sv._safe_path(srt_path, "add_subtitles")) if srt_path else cues
    shifted = []
    for i, c in enumerate(items):
        if not isinstance(c, dict) or not {"start", "end", "text"} <= set(c):
            raise ValueError(f"cue {i} must be an object with start, end and text")
        if not all(isinstance(c[k], (int, float)) and not isinstance(c[k], bool) for k in ("start", "end")):
            raise ValueError(f"cue {i}: start and end must be numbers (seconds)")
        shifted.append({"start": c["start"] + offset_s, "end": c["end"] + offset_s, "text": c["text"]})
    return {"op": "subtitles", "cues": shifted, "pos": position, "size": size, "style": style,
            "color": color or None, "box": box, "fade": 0.0, "ornament": "none"}


@builder("add_graphic")
def _b_add_graphic(kind, start_s, dur_s, amount=None, opacity=1.0, fade_s=0.5, theme="auto"):
    return {"op": "graphic", "kind": kind, "start": start_s, "dur": dur_s, "amount": amount, "opacity": opacity,
            "fade": fade_s, "theme": _theme_arg(theme)}


@builder("add_lower_third")
def _b_add_lower_third(title, subtitle="", start_s=0.0, dur_s=4.0, align="left", fade_s=0.4, theme="auto", anim=None):
    return {"op": "lower_third", "title": title, "subtitle": subtitle, "start": start_s, "dur": dur_s, "align": align,
            "fade": fade_s, "theme": _theme_arg(theme), "anim": anim or None}


@builder("add_image")
def _b_add_image(start_s, dur_s, path="", position="center", scale=0.3, opacity=1.0, icon="", color="", at=None, plate=None, theme="auto", anim=None):
    op = {"op": "image", "path": sv._safe_path(path, "add_image") if path else "", "start": start_s, "dur": dur_s,
          "pos": position, "scale": scale, "opacity": opacity, "icon": icon or "", "color": color or None, "at": at, "theme": _theme_arg(theme), "anim": anim or None}
    if plate is not None:
        op["plate"] = plate
    return op


@builder("add_callout")
def _b_add_callout(title, track, subtitle="", start_s=None, dur_s=None, side="auto", fade_s=0.3, theme="auto", size=1.0, anim=None):
    if not isinstance(track, list) or not track or not all(isinstance(p, (list, tuple)) and len(p) == 3 for p in track):
        raise ValueError("track must be a list of [t_s, x, y] points (timeline seconds; x, y = fractions 0-1 of the frame)")
    t0 = track[0][0]
    start = t0 if start_s is None else start_s
    dur = (track[-1][0] - start if dur_s is None else dur_s)
    if dur_s is None and dur <= 0:
        dur = 2.0                                          # a single point: show it for a couple of seconds
    return {"op": "callout", "title": title, "subtitle": subtitle, "path": [list(p) for p in track], "start": start,
            "dur": dur, "side": side, "fade": fade_s, "theme": _theme_arg(theme),
            **({"size": size} if size != 1.0 else {}), **({"anim": anim} if anim else {})}


@edit_tool
def add_pip(source: str, start_s: float, dur_s: float, position: str = "top-right",
            scale: float = 0.3, opacity: float = 1.0, source_in_s: float = 0.0, anim: dict | None = None) -> dict:
    """Overlay a picture-in-picture video on its own layer from start_s for dur_s (TIMELINE time).
    position: top-right | top-left | bottom-right | bottom-left. scale: fraction of frame width (0-1].
    opacity 0-1 (fades in/out at the edges). Calls accumulate (several PiPs are allowed, up to 6 overlays
    at the same moment). Overlays are placed in timeline seconds and do NOT move if you later edit earlier clips."""
    return sv.commit(sv.BUILDERS["add_pip"](source, start_s, dur_s, position, scale, opacity, source_in_s, anim))


@edit_tool
def add_text(text: str, start_s: float, dur_s: float, position: str = "bottom", size: float = 0.06,
             style: str = "auto", color: str = "", box: bool | None = None, uppercase: bool | None = None,
             ornament: str = "", fade_s: float = 0.15, anim: dict | None = None) -> dict:
    """Show a title/caption from start_s for dur_s (TIMELINE time). Latin text with accents, ñ, ¿¡ is
    supported; use \\n for a line break. Long text is wrapped and shrunk to fit (max 4 lines, 200 chars);
    text that cannot fit, or characters the font lacks (CJK, newer emoji), are rejected with a message.
    style: "auto" (default) follows the project's template (set_template); or a name from list_styles
    .
    position: bottom | center | top. size: fraction of frame height (0.02-0.2).
    color: optional #RRGGBB; leave empty to keep the style's own colour.
    box: panel behind the text (default: the style decides). uppercase: force/forbid capitals (default per style).
    ornament: none | line | diamond (thin rule; default per style). All styles add a soft shadow for readability."""
    return sv.commit(sv.BUILDERS["add_text"](text, start_s, dur_s, position, size, style, color, box, uppercase, ornament, fade_s, anim))


@edit_tool
def add_subtitles(srt_path: str = "", cues: list[dict] | None = None, offset_s: float = 0.0,
                  position: str = "bottom", size: float = 0.05, style: str = "auto", color: str = "",
                  box: bool | None = None) -> dict:
    """Add subtitles from an .srt file (srt_path) OR a list of cues [{"start":1.0,"end":2.5,"text":"Hola"}]
    (seconds, timeline time). Give exactly one. offset_s shifts every cue (positive = later).
    style: "auto" (default) = the template's subtitle style, or any name from list_styles.
    Same text rules as add_text (accents/ñ fine; up to 300 cues). Cues after the timeline end are
    dropped with a warning. Calling it again ADDS another subtitle track; use remove_op to replace."""
    return sv.commit(sv.BUILDERS["add_subtitles"](srt_path, cues, offset_s, position, size, style, color, box))


@edit_tool
def add_graphic(kind: str, start_s: float, dur_s: float, amount: float | None = None, opacity: float = 1.0,
                fade_s: float = 0.5, theme: str = "auto") -> dict:
    """Add a graphic overlay (drawn to match the video size) from start_s for dur_s (TIMELINE time).
    kind: <<graphics>> (frame = the template's own border, letterbox = cinema bars, vignette = soft dark edges). amount (optional): frame inset 0.015-0.08 | letterbox bar height 0.04-0.25 |
    vignette strength 0.1-1. opacity 0-1; fade_s = fade in/out at the edges. theme: "auto" = the project's template, or name another one for this item only."""
    return sv.commit(sv.BUILDERS["add_graphic"](kind, start_s, dur_s, amount, opacity, fade_s, theme))


@edit_tool
def add_lower_third(title: str, subtitle: str = "", start_s: float = 0.0, dur_s: float = 4.0,
                    align: str = "left", fade_s: float = 0.4, theme: str = "auto", anim: dict | None = None) -> dict:
    """Name/role caption panel at the bottom, drawn in the project's template (e.g. title "Señor Muñoz", subtitle "Director de Proyecto"). Single lines only (title max 60 chars,
    subtitle max 80). align: left | right. Shown from start_s for dur_s (TIMELINE time)."""
    return sv.commit(sv.BUILDERS["add_lower_third"](title, subtitle, start_s, dur_s, align, fade_s, theme, anim))


@edit_tool
def add_image(start_s: float, dur_s: float, path: str = "", position: str = "center", scale: float = 0.3,
              opacity: float = 1.0, icon: str = "", color: str = "", at: list[float] | None = None,
              plate: bool | None = None, theme: str = "auto", anim: dict | None = None) -> dict:
    """Show a picture from start_s for dur_s (TIMELINE time). Give EITHER `icon` (a name from list_assets(kind='icon'): about 100
    line icons plus hand-drawn doodle-arrow/star/circle/underline/burst/check/cross/heart) OR `path` (PNG/JPG/WebP with
    transparency kept, or a plain .svg; max 25 MB / 8000 px). position: center | top-right | top-left | bottom-right | bottom-left,
    or at=[x, y] to centre it on an exact point of the frame (fractions 0-1, 0,0 = top-left). scale: fraction of frame width (0-1].
    Icons are tinted with the template's colour (color=#RRGGBB to override) and sit on a round plate that keeps them legible
    (plate=false for the bare glyph). Pair an icon with add_callout/add_text to label things."""
    return sv.commit(sv.BUILDERS["add_image"](start_s, dur_s, path, position, scale, opacity, icon, color, at, plate, theme, anim))


@edit_tool
def add_callout(title: str, track: list[list[float]], subtitle: str = "", start_s: float | None = None,
                dur_s: float | None = None, side: str = "auto", fade_s: float = 0.3, theme: str = "auto", size: float = 1.0,
                anim: dict | None = None) -> dict:
    """Pin a name label to a point of the picture: a ring on the exact spot, a thin staff and a flag in the template's style with
    `title` (and optional `subtitle`). `track` = [[t_s, x, y], ...]: where the point is at each moment (t_s in TIMELINE
    seconds, increasing; x, y = fractions of the frame, 0,0 = top-left, 1,1 = bottom-right). With several points the ring
    glides linearly between them (use it to follow a moving object); one point = fixed. start_s/dur_s default to
    the first/last point (dur 2 s for one point). side: auto (flag placed so it stays on screen) | ne | nw | se | sw.
    Title max 40 characters, subtitle 60, single lines. Coordinates usually come from detect.py (an external object
    detector), not from guessing. size 0.7-2.0 scales the whole callout (1 = normal; 1.3 reads better at 1080p and above).
    anim: {"in": ..., "out": ...} among <<callout_presets>> (zoom/pop scale about the ring; draw unfolds from the ring along the staff to the flag), plus
    in_s/out_s/ease_in/ease_out; the project's motion supplies one when it names none."""
    return sv.commit(sv.BUILDERS["add_callout"](title, track, subtitle, start_s, dur_s, side, fade_s, theme, size, anim))


@edit_tool
def animate(index: int = -1, anim: dict | None = None, op_id: str = "") -> dict:
    """Animate an existing text, image/icon, picture-in-picture, lower third or graphic: `index` is its edit number (get_timeline; -1 = the last
    edit) or give its stable `op_id`. anim=null removes the animation. The same `anim` object is accepted by add_text/add_image/add_lower_third/add_pip.
    anim = {"in": preset, "out": preset, "in_s": 0.5, "out_s": 0.4, "ease_in": e, "ease_out": e, "keys": [...], "keys_ease": e, "rotate": deg, "scale": k}
    presets: <<presets>> (slide-*: the SIDE of the screen: in = comes from it, out = leaves toward it; list_styles describes the rest).
    e: <<easings>>. keys = free motion between entrance and exit, each {"t": seconds from the item's start,
    "x": 0-1, "y": 0-1 (centre of the item in the frame), "scale": 1 = normal, "rotate": degrees clockwise, "opacity": 0-1}; omitted fields hold.
    rotate/scale = constant tilt / size multiplier. Examples: {"in": "slide-left", "out": "fade"}; {"in": "pop", "rotate": -6};
    {"keys": [{"t": 0, "x": 0.2, "y": 0.5}, {"t": 2, "x": 0.8, "y": 0.5}]} (glide across). Callouts accept only in/out among <<callout_presets>> (they follow their own track)."""
    with sv.locked():
        st = sv.load()
        sv.check_revision(st)
        n = len(st["ops"])
        if op_id:
            i = P.index_of(st, op_id)
        else:
            i = index + n if index < 0 else index
            if not 0 <= i < n:
                raise ValueError(f"no op {index} (have {n})")
        op = st["ops"][i]
        if op.get("op") not in O.animatable():
            raise ValueError(f"op {i} is a '{op.get('op')}': only {', '.join(O.animatable())} can be animated")
        new = dict(op)
        if anim:
            new["anim"] = anim
        else:
            new.pop("anim", None)
        sv.replace_op(st, i, new)                                    # raises, with a message naming the op, if the animation is not valid there
        sv.save(st, {"kind": "animate", "op": op["id"]})
        return {"animated": i, "op_id": op["id"], **sv.summary(st)}
