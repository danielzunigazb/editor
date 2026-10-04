"""{"op":"lower_third","title":"Ana","subtitle":"Directora","start":1,"dur":4,"align":"left"}: a name strap in the template's shape."""
from ... import graphics, themes
from ...ops import Op, op
from ...ops.common import clean, gfx_layer, theme_key


@op
class LowerThird(Op):
    timed = True
    order = 50
    name = "lower_third"
    animatable = True

    def layout(self, o, n, where, st):
        if o.get("align", "left") not in ("left", "right"):
            raise ValueError(f"{where}: align must be left or right")
        tk = theme_key(o, where, st.ctx)
        th_ = themes.get(tk)
        title, sub = clean(o.get("title"), where, th_.title_style), (clean(o["subtitle"], where, th_.caption_style) if o.get("subtitle") else "")
        if "\n" in title or "\n" in sub or len(title) > 60 or len(sub) > 80:
            raise ValueError(f"{where}: lower third needs single-line text (title max 60, subtitle max 80 characters)")
        st.layers.append(gfx_layer(n, o, "lower_third", {"title": title, "subtitle": sub, "align": o.get("align", "left"), "theme": tk}, where, st.ctx))

    def describe(self, o):
        return f"Tercio inferior “{o['title'][:24]}”", f"lower_third @ {o['start']:g}s"

    def check_new(self, o, ctx):
        graphics.lower_third(ctx.W, ctx.H, o["title"], o.get("subtitle", ""), o.get("align", "left"), strict=True, theme=theme_key(o, "lower_third", ctx))
