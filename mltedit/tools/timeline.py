"""Edits of the main track: clips, cuts, transitions, fades."""

from .. import assets as assets_lib
from .. import engine as live
from .. import server as sv
from ..config import S
from . import builder, edit_tool

@builder("add_clip")
# ---- one builder per edit tool: (tool arguments) -> engine op. The tools and apply_ops share them, so a batch behaves
# exactly like the same calls made one by one.
def _b_add_clip(source, start_s=0.0, end_s=None):
    op = {"op": "add", "src": source, "in": start_s}
    if end_s is not None:
        op["end"] = end_s
    return op


@builder("cut_clip")
def _b_cut_clip(index=-1, at_s=0.0, clip_id=""):
    return {"op": "cut", "clip": clip_id or index, "at": at_s}


@builder("crossfade")
def _b_crossfade(first_index=-1, dur_s=1.0, style="", first_clip_id=""):
    between = [first_clip_id, None] if first_clip_id else [first_index, first_index + 1]      # None: the clip after the first (resolved on commit)
    return {"op": "crossfade", "between": between, "dur": dur_s, **({"style": style} if style and style != S.default_transition else {})}


@builder("set_fades")
def _b_set_fades(fade_in_s=0.0, fade_out_s=0.0):
    return {"op": "fade", "in": fade_in_s, "out": fade_out_s}


# ------------------------------------------------------------------ tools: edits (instant, no rendering)
@edit_tool
def add_clip(source: str, start_s: float = 0.0, end_s: float | None = None) -> dict:
    """Append a clip (or the range start_s..end_s of it) to the end of the main track.
    Returns the updated timeline. The new entry's index is the last one."""
    return sv.commit(sv.BUILDERS["add_clip"](source, start_s, end_s))


@edit_tool
def cut_clip(index: int, at_s: float, clip_id: str = "") -> dict:
    """Cut timeline entry `index` (or the clip with this stable `clip_id`: the `id` of an entry in get_timeline) at `at_s` seconds from the ENTRY's
    own start and drop everything after (the entry becomes `at_s` long). Later entries shift earlier."""
    return sv.commit(sv.BUILDERS["cut_clip"](index, at_s, clip_id))


@edit_tool
def crossfade(first_index: int, dur_s: float = 1.0, style: str = "", sfx: str = "", first_clip_id: str = "") -> dict:
    """Transition (video) and crossfade (audio) between entry `first_index` (or the clip with this stable `first_clip_id`) and the next one. The two entries overlap by dur_s, so the
    timeline gets shorter by dur_s. style: <<transitions>> | auto (the template's own, only when the project's motion is on); slide-* = the
    new clip travels over the old one. Anything but <<default_transition>> needs dur_s >= 0.2. sfx: also put a sound effect where the transition starts: an asset id from list_assets(kind='sfx'),
    or "auto" = the template's own whoosh (or pop/click when it has none; CC0; needs R2 access the first time). Both edits are added together or neither."""
    op = sv.BUILDERS["crossfade"](first_index, dur_s, style, first_clip_id)
    if not sfx:
        return sv.commit(op)
    with sv.locked():
        st = sv.load()
        sv.check_revision(st)
        if len(st["ops"]) + 2 > sv.MAX_OPS:
            raise ValueError(f"the project already has {len(st['ops'])} edits (the limit is {sv.MAX_OPS}); this needs room for two")
        op = sv.prepare(st, op)
        sv._validate(st, op)
        st["ops"].append(op)
        sv.bind(st)
        entries = live.layout(st["ops"])["entries"]
        ids, nxt = [e.get("id") for e in entries], op["between"][1]
        t = entries[ids.index(nxt) if nxt in ids else nxt]["start"]                # the new clip starts coming in here
        if sfx == "auto":
            sfx = assets_lib.auto_sfx(live.THEME.name)
        aop = sv.prepare(st, sv.BUILDERS["add_audio"](start_s=t, dur_s=None, asset=sfx, volume_db=-10.0))
        sv._validate(st, aop)
        st["ops"].append(aop)
        sv.push_undo(st, {"k": "batch", "patches": [{"k": "pop", "id": aop["id"]}, {"k": "pop", "id": op["id"]}]})
        sv.save(st, {"kind": "crossfade+sfx", "ops": [op["id"], aop["id"]]})
        return {"op_ids": [op["id"], aop["id"]], **sv.summary(st)}


@edit_tool
def set_fades(fade_in_s: float = 0.0, fade_out_s: float = 0.0) -> dict:
    """Fade from black/silence at the start and to black/silence at the end of the whole timeline.
    Replaces any previous fade setting."""
    return sv.commit(sv.BUILDERS["set_fades"](fade_in_s, fade_out_s))
