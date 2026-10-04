"""{"op":"text","text":"Hola","start":1,"dur":3,...}: a styled text overlay."""
from ...ops import Op, op
from ...ops.common import anim, check_text_fits, clean, need_time, resolve_style, text_style


@op
class Text(Op):
    timed = True
    order = 10
    name = "text"
    defaults = {"pos": "bottom", "size": 0.06, "fade": 0.15, "style": "auto"}
    animatable = True

    def layout(self, o, n, where, st):
        style = text_style(o, where, st.ctx)
        need_time(o, where)
        st.layers.append({"kind": "text", "op": n, "start": float(o["start"]), "dur": float(o["dur"]), **style,
                          "text": clean(o.get("text"), where, style["style"]), "anim": anim(o, where, st.ctx)})

    def describe(self, o):
        return f"Texto “{o['text'][:30]}” de {o['start']:g} a {o['start']+o['dur']:g} s", f"text @ {o['start']:g}s {o['dur']:g}s"

    def legibility(self, o, i, st):
        H = st["height"]
        if isinstance(o.get("size"), (int, float)) and o["size"] * H < 14:
            return [f"op {i} (text) would be only {o['size'] * H:.0f} px tall at {st['width']}x{H}: raise its size to at least {14 / H:.3f}"]
        return []

    def check_new(self, o, ctx):
        check_text_fits([(o.get("text"), o.get("size", 0.06))], resolve_style(o, ctx), o.get("uppercase"), ctx)
