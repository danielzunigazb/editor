"""{"op":"add","src":"A","in":0,"end":5}: append a range of a source to the base track."""
from ...ops import Op, op


@op
class Add(Op):
    name = "add"
    defaults = {"in": 0.0}
    makes_clip = True

    def normalize(self, o, ctx=None):
        o = super().normalize(o)
        if ctx is not None and "end" not in o and o.get("src") in ctx.CLIP_LEN:
            o = {**o, "end": ctx.CLIP_LEN[o["src"]]}               # "to the end of the source" becomes the number it meant when the edit was made
        return o

    def layout(self, o, n, where, st):
        clip_len, fr = st.ctx.CLIP_LEN, st.fr
        src = o.get("src")
        if src not in clip_len:
            raise ValueError(f"{where}: unknown source '{src}'; known: {sorted(clip_len)}")
        start, end = float(o.get("in", 0.0)), float(o.get("end", clip_len[src]))
        if not (0 <= start < end <= clip_len[src] + 1e-6):
            raise ValueError(f"{where}: range {start:g}-{end:g}s outside source '{src}' (0-{clip_len[src]:g}s)")
        in_f, dur_f, src_f = fr(start), fr(end - start), fr(clip_len[src])
        if in_f + dur_f > src_f:                 # rounding may overrun the last frame by one
            dur_f = src_f - in_f
        if dur_f < 1:
            raise ValueError(f"{where}: range {start:g}-{end:g}s is shorter than one frame ({1 / st.fps:g}s)")
        st.entries.append({"src": src, "in_f": in_f, "dur_f": dur_f, "id": o.get("id")})

    def describe(self, o):
        return f"Agregar clip {o['src']}", f"add {o['src']}"
