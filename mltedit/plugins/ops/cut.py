"""{"op":"cut","clip":0,"at":3.0}: keep the first `at` seconds of base entry `clip`, drop the rest."""
from ...ops import Op, op


@op
class Cut(Op):
    name = "cut"

    def layout(self, o, n, where, st):
        i, fps = o.get("clip"), st.fps
        if not isinstance(i, int) or not 0 <= i < len(st.entries):
            raise ValueError(f"{where}: no timeline entry {i} (have {len(st.entries)})")
        e = st.entries[i]
        at_f = st.fr(o["at"])
        if not 0 < o["at"] < e["dur_f"] / fps:
            raise ValueError(f"{where}: cut at {o['at']:g}s is outside entry {i} (length {e['dur_f'] / fps:g}s)")
        if not 1 <= at_f < e["dur_f"]:
            raise ValueError(f"{where}: cut at {o['at']:g}s leaves nothing or a sub-frame piece (frame = {1 / fps:g}s)")
        e["dur_f"] = at_f

    def describe(self, o):
        return f"Cortar la entrada {o['clip']} en {o['at']:g} s", f"cut #{o['clip']} @ {o['at']:g}s"
