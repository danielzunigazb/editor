"""{"op":"crossfade","between":[0,1],"dur":1.0,"style":"dissolve"}: a transition plugin + audio crossfade between adjacent entries."""
from ... import transitions
from ...config import S
from ...ops import Op, op


@op
class Crossfade(Op):
    name = "crossfade"

    def normalize(self, o, ctx=None):
        return {"style": S.default_transition, **o}                  # read at call time: the setting can change between runs

    def layout(self, o, n, where, st):
        a_, b_ = (st.clip_index(r, where) for r in o["between"])
        if b_ != a_ + 1:
            raise ValueError(f"{where}: needs two adjacent existing entries (have {len(st.entries)})")
        if o["dur"] <= 0:
            raise ValueError(f"{where}: dur must be > 0")
        if st.fr(o["dur"]) < 1:
            raise ValueError(f"{where}: dur {o['dur']:g}s is shorter than one frame ({1 / st.fps:g}s)")
        frames = st.fr(o["dur"])
        style_ = transitions.validate(o.get("style") or S.default_transition, where)
        if style_ == "auto":                          # the template's own transition, only when the project's motion is on
            style_ = st.ctx.THEME.transition if st.ctx.MOTION else S.default_transition
        if not transitions.is_plain(style_) and o["dur"] < transitions.MIN_S:
            raise ValueError(f"{where}: a '{style_}' transition needs at least {transitions.MIN_S:g}s (dur is {o['dur']:g}s); use {S.default_transition} for a quick blend")
        ea = st.entries[a_]
        st.xfade_pairs[id(ea)] = {"a": ea, "b": st.entries[b_], "frames": frames, "style": style_, "op": o.get("id")}

    def clip_refs(self, o):
        return [b for b in o.get("between", []) if isinstance(b, str)]

    def migrate_refs(self, o, clip_ids):
        b = o.get("between", [])
        if all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x < len(clip_ids) for x in b):
            return {**o, "between": [clip_ids[x] for x in b]}
        return o

    def resolve_refs(self, o, entries):
        ids = [e.get("id") for e in entries]
        a, b = o["between"]
        a = ids[a] if isinstance(a, int) and not isinstance(a, bool) and 0 <= a < len(ids) and ids[a] else a
        if isinstance(a, str) and a in ids and (b is None or b == a) and ids.index(a) + 1 < len(ids):
            b = ids[ids.index(a) + 1]                          # only the first clip given: the next one is its partner
        elif isinstance(b, int) and not isinstance(b, bool) and 0 <= b < len(ids) and ids[b]:
            b = ids[b]
        return {**o, "between": [a, b]}

    def describe(self, o):
        st_ = o.get("style") or ""
        return (f"Fundido cruzado de {o['dur']:g} s entre {o['between'][0]} y {o['between'][1]}" + (f" ({st_})" if st_ else ""),
                f"xfade {o['between'][0]}-{o['between'][1]} {o['dur']:g}s" + (f" {st_}" if st_ else ""))
