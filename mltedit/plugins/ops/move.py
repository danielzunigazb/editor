"""{"op":"move","clip":<clip id>,"to":0}: put a base-track clip at another position. Edits anchored to the clip travel with it."""
from ...ops import Op, op
from ...errors import EditError


@op
class Move(Op):
    name = "move"

    def layout(self, o, n, where, st):
        i = st.clip_index(o.get("clip"), where)
        to = o.get("to")
        if not isinstance(to, int) or isinstance(to, bool) or not 0 <= to < len(st.entries):
            raise EditError("OUT_OF_RANGE", where, f"to must be a position between 0 and {len(st.entries) - 1}")
        st.entries.insert(to, st.entries.pop(i))

    def clip_refs(self, o):
        return [o["clip"]] if isinstance(o.get("clip"), str) else []

    def resolve_refs(self, o, entries):
        i = o.get("clip")
        if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(entries) and entries[i].get("id"):
            return {**o, "clip": entries[i]["id"]}
        return o

    def describe(self, o):
        return f"Mover el clip {o['clip']} a la posición {o['to']}", f"move {o['clip']} -> {o['to']}"
