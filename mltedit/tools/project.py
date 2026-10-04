"""Project and timeline-wide tools: project/sources, templates, batches, undo."""
import inspect, json, os, re

from .. import assets as assets_lib
from .. import graphics
from .. import icons
from .. import themes
from .. import transitions
from .. import anim, cards, errors, registry
from .. import engine as live
from .. import server as sv
from ..render import text as textrender
from .. import ops as O
from .. import project as P
from ..media import proxy as proxies
from ..errors import EditError, as_edit_error
from . import edit_tool, tool

# ------------------------------------------------------------------ tools: project / sources
@edit_tool
def new_project(width: int = 1280, height: int = 720, fps: int = 25, motion: bool = False) -> dict:
    """Start an empty project (discards the current timeline and imported sources).
    width/height/fps define the final export format; previews are rendered at half size.
    motion: switch on the template's own motion (default animations, transitions via style="auto"); off by default."""
    if not (64 <= width <= 7680 and 64 <= height <= 4320 and 1 <= fps <= 120):
        raise ValueError("width 64-7680, height 64-4320, fps 1-120")
    st = P.migrate({**json.loads(json.dumps(sv.DEFAULT)), "width": width, "height": height, "fps": fps, **({"motion": True} if motion else {})})
    with sv.locked():
        try:
            prev = sv.load()
        except RuntimeError:                                   # an unreadable file is exactly what new_project is for
            prev = {}
        sv.check_revision(prev)
        st["revision"] = prev.get("revision", 0)               # revisions never go back, so a stale expected_revision still notices the reset
        if not sv.dry_run():
            P.rotate_journal(sv.HISTORY)
        rev = sv.save(st, {"kind": "new_project"})
    return {"ok": True, "revision": rev, "format": f"{width}x{height}@{fps}", **({"motion": True} if motion else {})}


@edit_tool
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
        sv.check_revision(st)
        sid = id or f"S{len(st['sources']) + 1}"
        if sid in st["sources"]:
            raise ValueError(f"source id '{sid}' already exists")
        if len(st["sources"]) >= sv.MAX_SOURCES:
            raise ValueError(f"the project already has {sv.MAX_SOURCES} sources (the limit)")
        st["sources"][sid] = {"path": path, "sig": P.file_sig(path), **info}
        rev = sv.save(st, {"kind": "import", "source": sid})
        proxy = "none" if sv.dry_run() else proxies.ensure(sv.HOME, st["sources"][sid])      # in the background: the edit does not wait for it
    return {"id": sid, "revision": rev, **({"proxy": proxy} if proxy != "none" else {}), **info}


@tool
def verify_sources() -> dict:
    """Check that every file the project depends on (imported sources, images, audio) is still there and unchanged since it was imported or
    added. Returns {ok, problems}; each problem has a code (SOURCE_MISSING | SOURCE_CHANGED), what it is and its path. A missing file makes the rendering tools refuse;
    a changed one still renders what is on disk, and get_timeline warns. `refresh_source` re-reads a changed source."""
    bad = P.file_problems(sv.load())
    return {"ok": not bad, "problems": bad}


@edit_tool
def refresh_source(id: str) -> dict:
    """Re-read a source whose file changed on disk (new length, resolution, audio): its record is updated and the whole timeline is checked again.
    If the edits no longer fit the new file (e.g. a clip now shorter than a cut) nothing is changed and the error says which edit. `undo` takes it back."""
    with sv.locked():
        st = sv.load()
        sv.check_revision(st)
        if id not in st["sources"]:
            raise EditError("UNKNOWN_SOURCE", f"unknown source '{id}'; known: {sorted(st['sources'])}")
        old = json.loads(json.dumps(st["sources"]))
        path = st["sources"][id]["path"]
        st["sources"][id] = {"path": path, "sig": P.file_sig(path), **sv._probe(path)}
        sv.bind(st)
        live.layout(st["ops"])                                  # raises if an edit no longer fits the new file: nothing is saved
        sv.push_undo(st, {"k": "set", "fields": {"sources": old}})
        sv.save(st, {"kind": "refresh_source", "source": id})
        if not sv.dry_run():
            proxies.ensure(sv.HOME, st["sources"][id])
        return {"refreshed": id, **sv.summary(st)}


@tool
def list_sources() -> dict:
    """List imported sources with duration, resolution, whether they have audio, and the state of their preview proxy (none | pending | ready | failed):
    stills, contact sheets and the preview mp4 read the proxy of a big source once it is ready, and the original until then."""
    return {k: {**v, "proxy": proxies.state(sv.HOME, v)} for k, v in sv.load()["sources"].items()}


@tool
def wait_for_proxies(timeout_s: float = 60.0) -> dict:
    """Wait (up to timeout_s) until the preview proxies of the imported sources are made. Without waiting, get_still and get_contact_sheet still work
    (from the originals, slower) while a proxy is pending. Returns the state of each source."""
    st = sv.load()
    sv.ensure_proxies(st)
    return {"states": proxies.wait(sv.HOME, st["sources"], max(0.0, min(float(timeout_s), 600.0)))}


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
            "edit_tools": {n: str(inspect.signature(f)) for n, f in sorted(sv.BUILDERS.items())}, "error_codes": errors.CODES,
            "animation_presets": list(anim.PRESETS), "easings": list(anim.EASES), "card_layouts": list(cards.LAYOUTS),
            **({"problems": registry.problems()} if registry.problems() else {})}


@edit_tool
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
        sv.check_revision(st)
        old = {"theme": st.get("theme"), "motion": st.get("motion")}
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
        sv.push_undo(st, {"k": "set", "fields": old})
        sv.save(st, {"kind": "set_template", "template": name})
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


@edit_tool
def apply_ops(ops: list[dict]) -> dict:
    """Apply several edits in ONE call (all or nothing). Each item is {"tool": "<edit tool name>", ...that tool's
    arguments}, e.g. [{"tool":"add_clip","source":"A","end_s":3}, {"tool":"add_clip","source":"B"},
    {"tool":"crossfade","first_index":0,"dur_s":0.5}, {"tool":"add_text","text":"Hola","start_s":0.5,"dur_s":2}].
    Allowed tools: <<edit_tools>> (import_clip, new_project, add_card and duck_auto are separate calls). Items are validated in order against
    the timeline as the previous items leave it; if ANY item is invalid nothing is applied and the error names the
    item. Up to 50 items. Returns the final timeline (check `warnings`: it flags overlays that may overlap on screen) and `op_ids`.
    One `undo` takes back the whole batch.
    Prefer this to many single calls: it is the same result with far fewer round trips."""
    if not isinstance(ops, list) or not 1 <= len(ops) <= 50:
        raise ValueError("ops must be a list of 1-50 items")
    with sv.locked():
        return _apply_ops(ops)


def _apply_ops(ops):
    st = sv.load()
    sv.check_revision(st)
    if len(st["ops"]) + len(ops) > sv.MAX_OPS:
        raise ValueError(f"this batch would take the project past {sv.MAX_OPS} edits (it has {len(st['ops'])})")
    made = []
    for i, spec in enumerate(ops):
        if not isinstance(spec, dict) or not isinstance(spec.get("tool"), str):
            raise ValueError(f"item {i}: needs a 'tool' key naming an edit tool; nothing was applied")
        tool = spec["tool"]
        fn = sv.BUILDERS.get(tool)
        if fn is None:
            raise ValueError(f"item {i}: unknown tool '{tool}'; allowed: {', '.join(sorted(sv.BUILDERS))}; nothing was applied")
        try:
            op = sv.prepare(st, fn(**{k: v for k, v in spec.items() if k != "tool"}))
            sv._validate(st, op)
        except TypeError as e:
            import inspect
            raise EditError("INVALID_ARGUMENT", f"item {i} ({tool})", f"bad arguments ({e}); expected {tool}{inspect.signature(fn)}; nothing was applied")
        except ValueError as e:
            raise as_edit_error(e).with_prefix(f"item {i} ({tool}): ", "; nothing was applied")
        st["ops"].append(op)                       # in memory only until every item has passed
        made.append(op["id"])
    sv.push_undo(st, {"k": "batch", "patches": [{"k": "pop", "id": i} for i in reversed(made)]})
    sv.save(st, {"kind": "apply_ops", "ops": made})
    return {"applied": len(ops), "op_ids": made, **sv.summary(st)}


@tool
def get_timeline() -> dict:
    """Current timeline: `revision`, entries (each with a stable `id`) with start/end times, crossfades, fades, overlays
    (pip/text/subtitles/image, with their track), warnings (overlays trimmed or hidden by later cuts) and the full op list
    (each op has a stable `id` and an `index`; both work with remove_op/update_op)."""
    return sv.summary(sv.load(), full=True)


def _describe_patch(p, inv):
    k = p["k"]
    if k == "pop":
        return {"removed": inv["op"]}
    if k == "insert":
        return {"restored": p["op"]}
    if k == "replace":
        return {"reverted": p["id"]}
    if k == "set":
        return {"reverted": list(p["fields"])}
    return {"steps": len(p["patches"])}


def _step(name, src, dst):
    """undo/redo: apply the top patch of stack `src`, check the project is still valid, put the inverse on `dst`."""
    with sv.locked():
        st = sv.load()
        sv.check_revision(st)
        if not st[src]:
            raise ValueError(f"nothing to {name}")
        patch = st[src].pop()
        inv = P.apply_patch(st, patch)
        sv.bind(st)
        live.layout(st["ops"])                              # raises if the result is not valid: nothing is saved
        st[dst].append(inv)
        sv.save(st, {"kind": name})
        return {**_describe_patch(patch, inv), **sv.summary(st, full=True)}


@edit_tool
def undo() -> dict:
    """Take back the last change: an added edit, a removed one, an animation change, a template change or a whole apply_ops batch.
    `redo` puts it back. Up to 200 steps."""
    return _step("undo", "undo", "redo")


@edit_tool
def redo() -> dict:
    """Put back what `undo` took back (until a new edit is made)."""
    return _step("redo", "redo", "undo")


def _op_index(st, index, op_id, what):
    if op_id:
        return P.index_of(st, op_id)
    n = len(st["ops"])
    if index is None:
        raise ValueError(f"{what}: give an op_id or an index")
    i = index + n if index < 0 else index
    if not 0 <= i < n:
        raise ValueError(f"no op {index} (have {n})")
    return i


def _resolved_starts(m):
    """op position -> where its edit starts on the timeline now (seconds), from a layout; edits that are not shown are absent."""
    out = {}
    for item in list(m["layers"]) + list(m["audios"]):
        out[item["op"]] = min(item["start"], out.get(item["op"], item["start"]))
    return out


@edit_tool
def remove_op(index: int | None = None, op_id: str = "", cascade: bool = False, reanchor: str = "") -> dict:
    """Remove an edit by its `op_id` (stable; see get_timeline) or its `index`. If other edits depend on it (removing a clip that a cut, a crossfade or
    an anchored overlay refers to) it is rejected with the list of them and nothing changes, unless: cascade=true removes those edits too, or
    reanchor="timeline" keeps the anchored overlays/audio where they are now (anchored to the timeline instead of the clip; cuts, trims and crossfades
    of the clip still need cascade). `undo` brings everything back in one step."""
    if reanchor not in ("", "timeline"):
        raise EditError("INVALID_ARGUMENT", "reanchor must be '' or 'timeline'")
    with sv.locked():
        st = sv.load()
        sv.check_revision(st)
        i = _op_index(st, index, op_id, "remove_op")
        sv.bind(st)
        target = st["ops"][i]
        plug = lambda o: O.get_op(o["op"])                  # noqa: E731
        depends = lambda gone: [o for o in st["ops"] if o["id"] not in gone and plug(o) and set(plug(o).refs(o)) & gone]   # noqa: E731
        direct = depends({target["id"]})
        gone, moved = {target["id"]}, []
        if direct and cascade:
            while True:                                      # everything that depends on what is going away, transitively
                more = depends(gone)
                if not more:
                    break
                gone |= {o["id"] for o in more}
        elif direct and reanchor == "timeline":
            moved = [o for o in direct if plug(o).anchorable and set(plug(o).refs(o)) <= {target["id"]}]
            direct = [o for o in direct if o not in moved]
        if direct and not cascade:
            raise EditError("TIMELINE_CONFLICT", f"{len(direct)} edit(s) depend on {target['id']} ({target['op']}): "
                            + ", ".join(f"{o['id']} ({o['op']})" for o in direct) + "; pass cascade=true to remove them too"
                            + ("" if reanchor else ", or reanchor='timeline' to keep the overlays and audio where they are"), hint="cascade / reanchor")
        m = live.layout(st["ops"])
        starts = _resolved_starts(m)
        undo = []
        for o in moved:                                      # keep them where they are now: absolute timeline frames
            j = P.index_of(st, o["id"])
            p = plug(o)
            now = starts.get(j, p.start_of(o))
            new = p.shifted(o, now - p.start_of(o))
            new["anchor"] = {"timeline_f": live.CTX.fr(now), "t0_f": live.CTX.fr(now)}
            undo.append({"k": "replace", "id": o["id"], "op": o})
            st["ops"][j] = new
        removed = sorted((j, o) for j, o in enumerate(st["ops"]) if o["id"] in gone)
        for j, o in reversed(removed):
            st["ops"].pop(j)
        undo += [{"k": "insert", "index": j, "op": o} for j, o in removed]
        live.layout(st["ops"])                               # raises if what remains is no longer valid: nothing is saved
        sv.push_undo(st, undo[0] if len(undo) == 1 else {"k": "batch", "patches": undo})
        sv.save(st, {"kind": "remove_op", "ops": [o["id"] for _, o in removed]})
        return {"removed": target, **({"also_removed": [o["id"] for _, o in removed if o["id"] != target["id"]]} if len(removed) > 1 else {}),
                **({"reanchored": [o["id"] for o in moved]} if moved else {}), **sv.summary(st, full=True)}


@edit_tool(anchor=True)
def move_op(op_id: str, start_s: float) -> dict:
    """Move an overlay or audio edit (text, subtitles, lower third, graphic, image, picture-in-picture, callout, audio) to timeline time `start_s` by its
    stable `op_id`; everything timed inside it (subtitle cues, a callout's path, ducking intervals) moves with it. It is anchored to the clip on screen
    at the new time (anchor="timeline" to keep it at that time instead). `undo` takes it back."""
    with sv.locked():
        st = sv.load()
        sv.check_revision(st)
        i = P.index_of(st, op_id)
        old = st["ops"][i]
        plug = O.get_op(old["op"])
        if plug is None or not plug.anchorable:
            raise EditError("INVALID_ARGUMENT", f"op {op_id} is a '{old['op']}': only overlay and audio edits can be moved ({', '.join(n for n, p in registry.items('op') if p.anchorable)})")
        now = plug.start_of(old)
        new = plug.shifted(old, start_s - now)
        new = sv.reanchor(st, {k: v for k, v in new.items() if k != "anchor"}, sv._call().get("anchor") or "clip")
        before = live.layout(st["ops"])["total"]
        sv.replace_op(st, i, new)
        err = plug.placement_error(new, before)
        if err:
            raise EditError("TIMELINE_CONFLICT", err)
        sv.save(st, {"kind": "move_op", "op": op_id})
        return {"moved": op_id, **sv.summary(st, full=True)}


@edit_tool
def update_op(op_id: str, patch: dict) -> dict:
    """Change fields of an existing edit by its stable `op_id` (see get_timeline): `patch` is merged into the op (a null value deletes the field).
    The op kind and id cannot change. The whole timeline is validated again; if the change is not valid nothing is changed and the error says why.
    `undo` takes it back. Example: update_op("op_1a2b3c", {"start": 2.0, "dur": 3.0})."""
    if not isinstance(patch, dict) or not patch:
        raise ValueError("patch must be a non-empty object")
    if {"op", "id"} & set(patch):
        raise ValueError("the op kind and id cannot be changed")
    with sv.locked():
        st = sv.load()
        sv.check_revision(st)
        i = P.index_of(st, op_id)
        new = {**st["ops"][i], **{k: v for k, v in patch.items() if v is not None}}
        for k, v in patch.items():
            if v is None:
                new.pop(k, None)
        if "anchor" not in patch:
            new = sv.reanchor(st, new)                   # a changed time means a new anchor (same kind as before)
        sv.replace_op(st, i, new)
        sv.save(st, {"kind": "update_op", "op": op_id})
        return {"updated": op_id, **sv.summary(st, full=True)}
