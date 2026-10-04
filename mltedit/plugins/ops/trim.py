"""{"op":"trim","clip":<clip id>,"in":1.0,"end":4.0}: keep only the range in..end (seconds of the SOURCE) of a base-track clip: trims its head and its tail."""
from ...ops import Op, op
from ...errors import EditError


@op
class Trim(Op):
    name = "trim"

    def layout(self, o, n, where, st):
        i = st.clip_index(o.get("clip"), where)
        e = st.entries[i]
        src_len = st.ctx.CLIP_LEN[e["src"]]
        a, b = float(o["in"]), float(o["end"])
        if not (0 <= a < b <= src_len + 1e-6):
            raise EditError("OUT_OF_RANGE", where, f"range {a:g}-{b:g}s outside source '{e['src']}' (0-{src_len:g}s)")
        in_f, dur_f = st.fr(a), st.fr(b - a)
        if in_f + dur_f > st.fr(src_len):
            dur_f = st.fr(src_len) - in_f
        if dur_f < 1:
            raise EditError("OUT_OF_RANGE", where, f"range {a:g}-{b:g}s is shorter than one frame ({1 / st.fps:g}s)")
        e["in_f"], e["dur_f"] = in_f, dur_f

    def clip_refs(self, o):
        return [o["clip"]] if isinstance(o.get("clip"), str) else []

    def resolve_refs(self, o, entries):
        i = o.get("clip")
        if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(entries) and entries[i].get("id"):
            return {**o, "clip": entries[i]["id"]}
        return o

    def describe(self, o):
        return f"Recortar el clip {o['clip']} a {o['in']:g}-{o['end']:g} s de la fuente", f"trim {o['clip']} {o['in']:g}-{o['end']:g}s"
