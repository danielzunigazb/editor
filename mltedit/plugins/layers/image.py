"""Image overlays: a picture file as is, or an SVG / bundled icon rasterized at the export size (icons.py)."""
import os

from ... import icons
from ...ops import Layer, layer
from . import boxed


@layer
class Image(Layer):
    name = "image"

    def source(self, L, ctx):
        if L.get("icon") or L.get("svg"):
            return icons.render_layer(L, ctx.W, ctx.CACHE), None
        return os.path.abspath(os.path.expanduser(L["path"])), None

    def place(self, L, ctx, n, crop, extra):
        w = ctx.W * L["scale"]
        return (*boxed.place_box(L, ctx.W, ctx.H, w / L["aspect"]), L["opacity"], min(6, (n - 1) // 2))

    def label(self, L):
        return f"image {boxed.name(L)}"

    def zone(self, L, ctx):
        fw, fh = max(ctx.W, 1), max(ctx.H, 1)
        return boxed.zone_box(L, fw, fh, L["scale"] * fw / (L["aspect"] * fh))

    def svg(self, L):
        return "imagen", "ctext"
