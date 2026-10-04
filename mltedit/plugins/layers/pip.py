"""Picture-in-picture: a range of a video source in a corner, with its audio mixed in."""
from ...ops import Layer, layer
from . import boxed


@layer
class Pip(Layer):
    name = "pip"
    audible = True

    def source(self, L, ctx):
        return ctx.CLIPS[L["src"]], None

    def first_frame(self, L, ctx):
        return ctx.fr(L.get("in", 0.0))

    def place(self, L, ctx, n, crop, extra):
        return (*boxed.place_box(L, ctx.W, ctx.H, ctx.H * L["scale"]), L["opacity"], min(6, (n - 1) // 2))

    def label(self, L):
        return f"pip {boxed.name(L)}"

    def zone(self, L, ctx):
        return boxed.zone_box(L, max(ctx.W, 1), max(ctx.H, 1), L["scale"])

    def svg(self, L):
        src = L.get("src", "")
        return f"{src} · PiP", (f"c{src.lower()}" if len(src) == 1 and src.lower() in "abc" else "ca")

    def summary(self, L):
        return {"source": L["src"]}
