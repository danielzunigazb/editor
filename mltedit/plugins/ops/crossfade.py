"""{"op":"crossfade","between":[0,1],"dur":1.0,"style":"dissolve"}: a transition plugin + audio crossfade between adjacent entries."""
from ... import transitions
from ...config import S
from ...ops import Op, op


@op
class Crossfade(Op):
    name = "crossfade"

    def layout(self, o, n, where, st):
        a_, b_ = o["between"]
        if b_ != a_ + 1 or a_ < 0 or b_ >= len(st.entries):
            raise ValueError(f"{where}: needs two adjacent existing entries (have {len(st.entries)})")
        if o["dur"] <= 0:
            raise ValueError(f"{where}: dur must be > 0")
        if st.fr(o["dur"]) < 1:
            raise ValueError(f"{where}: dur {o['dur']:g}s is shorter than one frame ({1 / st.fps:g}s)")
        st.xfades[a_] = st.fr(o["dur"])               # frames
        style_ = transitions.validate(o.get("style") or S.default_transition, where)
        if style_ == "auto":                          # the template's own transition, only when the project's motion is on
            style_ = st.ctx.THEME.transition if st.ctx.MOTION else S.default_transition
        if not transitions.is_plain(style_) and o["dur"] < transitions.MIN_S:
            raise ValueError(f"{where}: a '{style_}' transition needs at least {transitions.MIN_S:g}s (dur is {o['dur']:g}s); use {S.default_transition} for a quick blend")
        st.xstyles[a_] = style_

    def describe(self, o):
        st_ = o.get("style") or ""
        return (f"Fundido cruzado de {o['dur']:g} s entre {o['between'][0]} y {o['between'][1]}" + (f" ({st_})" if st_ else ""),
                f"xfade {o['between'][0]}-{o['between'][1]} {o['dur']:g}s" + (f" {st_}" if st_ else ""))
