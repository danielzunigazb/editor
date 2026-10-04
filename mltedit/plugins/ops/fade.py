"""{"op":"fade","in":0.5,"out":1.0}: fade from/to black (video) and silence (audio)."""
from ...ops import Op, op


@op
class Fade(Op):
    name = "fade"

    def layout(self, o, n, where, st):
        st.fade = {"in": float(o.get("in", 0.0)), "out": float(o.get("out", 0.0))}
        if st.fade["in"] < 0 or st.fade["out"] < 0:
            raise ValueError(f"{where}: fade times must be >= 0")

    def describe(self, o):
        return f"Fade desde negro ({o['in']:g} s) y a negro ({o['out']:g} s)", f"fade in {o['in']:g} out {o['out']:g}"
