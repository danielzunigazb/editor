"""{"op":"graphic","kind":"frame","start":0,"dur":5,"amount":0.5}: a full-frame decoration (graphics.KINDS)."""
from ... import graphics
from ...ops import Op, op
from ...ops.common import gfx_layer, theme_key


@op
class Graphic(Op):
    timed = True
    order = 40
    name = "graphic"
    defaults = {"opacity": 1.0, "fade": 0.4}
    animatable = True

    def layout(self, o, n, where, st):
        gk, par = o.get("kind"), {"amount": o.get("amount"), "theme": theme_key(o, where, st.ctx)}
        try:
            graphics.validate(gk, par)
        except ValueError as e:
            raise ValueError(f"{where}: {e}")
        st.layers.append(gfx_layer(n, o, gk, par, where, st.ctx))

    def describe(self, o):
        return f"Gráfico {o['kind']} de {o['start']:g} a {o['start']+o['dur']:g} s", f"graphic {o['kind']} @ {o['start']:g}s"
