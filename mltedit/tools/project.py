"""Project and timeline-wide tools: project/sources, templates, batches, undo."""
import json, os, re

from .. import assets as assets_lib
from .. import graphics
from .. import icons
from .. import themes
from .. import transitions
from .. import anim, cards, registry
from .. import engine as live
from .. import server as sv
from ..render import text as textrender
from . import tool

# ------------------------------------------------------------------ tools: project / sources
@tool
def new_project(width: int = 1280, height: int = 720, fps: int = 25, motion: bool = False) -> dict:
    """Start an empty project (discards the current timeline and imported sources).
    width/height/fps define the final export format; previews are rendered at half size.
    motion: switch on the template's own motion (default animations, transitions via style="auto"); off by default."""
    if not (64 <= width <= 7680 and 64 <= height <= 4320 and 1 <= fps <= 120):
        raise ValueError("width 64-7680, height 64-4320, fps 1-120")
    st = {**json.loads(json.dumps(sv.DEFAULT)), "width": width, "height": height, "fps": fps, **({"motion": True} if motion else {})}
    with sv.locked():
        sv.save(st)
    return {"ok": True, "format": f"{width}x{height}@{fps}", **({"motion": True} if motion else {})}


@tool
def import_clip(path: str, id: str = "") -> dict:
    """Register a video file as a source and return its id. Use the id in add_clip / add_pip.
    `id` is optional (letters/digits/_ ); default is S1, S2, ..."""
    path = sv._safe_path(path, "import_clip")
    if not os.path.isfile(path):
        raise ValueError(f"not a file: {path}")
    if id and not re.fullmatch(r"[A-Za-z0-9_]{1,32}", id):
        raise ValueError("id must be 1-32 letters, digits or underscore")
    info = sv._probe(path)                                     # slow (spawns ffprobe): done outside the lock
    with sv.locked():
        st = sv.load()
        sid = id or f"S{len(st['sources']) + 1}"
        if sid in st["sources"]:
            raise ValueError(f"source id '{sid}' already exists")
        if len(st["sources"]) >= sv.MAX_SOURCES:
            raise ValueError(f"the project already has {sv.MAX_SOURCES} sources (the limit)")
        st["sources"][sid] = {"path": path, **info}
        sv.save(st)
    return {"id": sid, **info}


@tool
def list_sources() -> dict:
    """List imported sources with duration, resolution and whether they have audio."""
    return sv.load()["sources"]


@tool
def list_styles() -> dict:
    """Text styles available for add_text / add_subtitles, and the graphic kinds for add_graphic."""
    sv.bind(sv.load())
    return {"templates": themes.describe(), "current_template": live.THEME.name,
            "text_styles": {k: v["label"] for k, v in textrender.STYLES.items()},
            "default_title_style": live.THEME.title_style, "default_subtitle_style": live.THEME.subtitle_style,
            "graphics": {k: f"amount = {graphics.AMOUNT[k][0]}, {graphics.AMOUNT[k][1]}-{graphics.AMOUNT[k][2]} "
                            f"(default {graphics.AMOUNT[k][3]})" for k in graphics.KINDS},
            "lower_third": "name + role panel in the template's own shape (add_lower_third)", "transitions": list(transitions.STYLES),
            "animation_presets": list(anim.PRESETS), "easings": list(anim.EASES), "card_layouts": list(cards.LAYOUTS),
            **({"problems": registry.problems()} if registry.problems() else {})}


@tool
def set_template(name: str, accent: str = "", motion: bool | None = None) -> dict:
    """Choose the project's design template: <<templates>> (see list_styles).
    Everything that does not name its own style (text, subtitles, lower thirds, labels, cards) follows it, so switching
    restyles the whole edit; edits are kept. accent: optional #RRGGBB brand colour replacing the template's signature colour.
    motion: true/false switches the template's own motion (default animations and the style="auto" transition) for the whole project; omit to keep it.
    Rejected, changing nothing, if an existing text would not fit in the new template's type."""
    spec = {"name": name, "accent": accent or None}
    base = themes.get(spec)                                # validates name and colour
    note = None
    if accent and base.options.get("accent_locked"):        # a template whose look is not one colour (e.g. a metallic gradient) keeps its own
        spec["accent"], note = None, base.options["accent_locked"]
    with sv.locked():
        st = sv.load()
        st["theme"] = spec
        if motion is not None:
            st["motion"] = bool(motion)
        sv.bind(st)
        for i, o in enumerate(st["ops"]):
            try:
                live.check_new_op(o)
            except ValueError as e:
                raise ValueError(f"op {i} would not fit in the '{name}' template: {e}; nothing was changed")
        live.layout(st["ops"])
        sv.save(st)
        return {"template": name, "accent": live.THEME.accent, **({"note": note} if note else {}), **sv.summary(st)}


@tool
def list_assets(kind: str = "icon", theme: str = "", mood: str = "", license: str = "", query: str = "") -> dict:
    """List bundled assets. kind: icon (names for add_image(icon=...)) | music (beds for add_audio(asset=...), 2-4 min) | sfx (short effects for
    add_audio(asset=..., volume_db -6..0)). Filters (music/sfx): theme (a template name: <<templates>>), mood (e.g. calming, bouncy, ding, whoosh, page-turn), license (CC0 | CC-BY), query (text in id/title). CC-BY pieces need a credit:
    the editor lists the required lines in get_timeline/export for you. Shows up to 40; narrow with filters."""
    if kind == "icon":
        names = [n for n in icons.list_icons() if query.lower() in n]
        return {"kind": kind, "count": len(names), "items": names}
    if kind not in ("music", "sfx"):
        raise ValueError("kind must be icon, music or sfx")
    items, total = assets_lib.listing(kind, theme, mood, license, query)
    return {"kind": kind, "count": total, "shown": len(items), "items": items}


@tool
def apply_ops(ops: list[dict]) -> dict:
    """Apply several edits in ONE call (all or nothing). Each item is {"tool": "<edit tool name>", ...that tool's
    arguments}, e.g. [{"tool":"add_clip","source":"A","end_s":3}, {"tool":"add_clip","source":"B"},
    {"tool":"crossfade","first_index":0,"dur_s":0.5}, {"tool":"add_text","text":"Hola","start_s":0.5,"dur_s":2}].
    Allowed tools: <<edit_tools>> (import_clip, new_project, add_card and duck_auto are separate calls). Items are validated in order against
    the timeline as the previous items leave it; if ANY item is invalid nothing is applied and the error names the
    item. Up to 50 items. Returns the final timeline (check `warnings`: it flags overlays that may overlap on screen).
    Prefer this to many single calls: it is the same result with far fewer round trips."""
    if not isinstance(ops, list) or not 1 <= len(ops) <= 50:
        raise ValueError("ops must be a list of 1-50 items")
    with sv.locked():
        return _apply_ops(ops)


def _apply_ops(ops):
    st = sv.load()
    if len(st["ops"]) + len(ops) > sv.MAX_OPS:
        raise ValueError(f"this batch would take the project past {sv.MAX_OPS} edits (it has {len(st['ops'])})")
    for i, spec in enumerate(ops):
        if not isinstance(spec, dict) or not isinstance(spec.get("tool"), str):
            raise ValueError(f"item {i}: needs a 'tool' key naming an edit tool; nothing was applied")
        tool = spec["tool"]
        fn = sv.BUILDERS.get(tool)
        if fn is None:
            raise ValueError(f"item {i}: unknown tool '{tool}'; allowed: {', '.join(sorted(sv.BUILDERS))}; nothing was applied")
        try:
            op = fn(**{k: v for k, v in spec.items() if k != "tool"})
            sv._validate(st, op)
        except TypeError as e:
            import inspect
            raise ValueError(f"item {i} ({tool}): bad arguments ({e}); expected {tool}{inspect.signature(fn)}; "
                             f"nothing was applied")
        except ValueError as e:
            raise ValueError(f"item {i} ({tool}): {e}; nothing was applied")
        st["ops"].append(op)                       # in memory only until every item has passed
    sv.save(st)
    return {"applied": len(ops), **sv.summary(st)}


@tool
def get_timeline() -> dict:
    """Current timeline: entries with start/end times, crossfades, fades, overlays (pip/text/subtitles/image, with their
    track), warnings (overlays trimmed or hidden by later cuts) and the full op list
    (each op has an index usable with remove_op)."""
    return sv.summary(sv.load(), full=True)


@tool
def undo() -> dict:
    """Remove the last edit."""
    with sv.locked():
        st = sv.load()
        if not st["ops"]:
            raise ValueError("nothing to undo")
        removed = st["ops"].pop()
        sv.save(st)
        return {"removed": removed, **sv.summary(st, full=True)}


@tool
def remove_op(index: int) -> dict:
    """Remove edit number `index` (see get_timeline). Rejected, with nothing changed, if later edits
    depend on it (e.g. removing an add_clip that a later cut refers to)."""
    with sv.locked():
        st = sv.load()
        if not 0 <= index < len(st["ops"]):
            raise ValueError(f"no op {index} (have {len(st['ops'])})")
        sv.bind(st)
        rest = st["ops"][:index] + st["ops"][index + 1:]
        live.layout(rest)          # raises if the remaining ops are no longer valid
        removed = st["ops"][index]
        st["ops"] = rest
        sv.save(st)
        return {"removed": removed, **sv.summary(st, full=True)}
