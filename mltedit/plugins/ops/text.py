"""{"op":"text","text":"Hola","start":1,"dur":3,...}: a styled text overlay."""
from ...ops import Op, op
from ...ops.common import anim, check_text_fits, clean, need_time, resolve_style, text_style


@op
class Text(Op):
    name = "text"
    animatable = True

    def layout(self, o, n, where, st):
        style = text_style(o, where, st.ctx)
        need_time(o, where)
        st.layers.append({"kind": "text", "op": n, "start": float(o["start"]), "dur": float(o["dur"]), **style,
                          "text": clean(o.get("text"), where, style["style"]), "anim": anim(o, where, st.ctx)})

    def describe(self, o):
        return f"Texto “{o['text'][:30]}” de {o['start']:g} a {o['start']+o['dur']:g} s", f"text @ {o['start']:g}s {o['dur']:g}s"

    def check_new(self, o, ctx):
        check_text_fits([(o.get("text"), o.get("size", 0.06))], resolve_style(o, ctx), o.get("uppercase"), ctx)
