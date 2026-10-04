"""{"op":"cut","clip":0,"at":3.0}: keep the first `at` seconds of base entry `clip`, drop the rest."""
from ...ops import Op, op


@op
class Cut(Op):
    name = "cut"

    def layout(self, o, n, where, st):
        fps = st.fps
        i = st.clip_index(o.get("clip"), where)
        e = st.entries[i]
        at_f = st.fr(o["at"])
        if not 0 < o["at"] < e["dur_f"] / fps:
            raise ValueError(f"{where}: cut at {o['at']:g}s is outside entry {i} (length {e['dur_f'] / fps:g}s)")
        if not 1 <= at_f < e["dur_f"]:
            raise ValueError(f"{where}: cut at {o['at']:g}s leaves nothing or a sub-frame piece (frame = {1 / fps:g}s)")
        e["dur_f"] = at_f

    def migrate_refs(self, o, clip_ids):
        i = o.get("clip")
        return {**o, "clip": clip_ids[i]} if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(clip_ids) else o

    def resolve_refs(self, o, entries):
        i = o.get("clip")
        if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(entries) and entries[i].get("id"):
            return {**o, "clip": entries[i]["id"]}
        return o

    def describe(self, o):
        return f"Cortar la entrada {o['clip']} en {o['at']:g} s", f"cut {o['clip']} @ {o['at']:g}s"
