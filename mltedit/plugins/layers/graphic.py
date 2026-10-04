"""Graphic overlays (graphic and lower_third ops): full-frame PNGs from graphics.py. Static decoration shown at exactly the same time
(e.g. vignette + frame for the whole video) is pre-composited into one PNG and drawn by one compositor."""
from ... import graphics
from ...ops import Layer, layer


@layer
class Graphic(Layer):
    name = "graphic"
    croppable = True

    def source(self, L, ctx):
        if L["gk"] == "merged":
            return graphics.render_merged(L["params"]["parts"], ctx.W, ctx.H, ctx.CACHE), None
        return graphics.render(L["gk"], ctx.W, ctx.H, ctx.CACHE, **L["params"]), None

    def place(self, L, ctx, n, crop, extra):
        x, y, w, h = crop[1:] if crop else (0, 0, ctx.W, ctx.H)
        return x, y, w, h, L.get("opacity", 1.0), min(ctx.fr(L["fade"]), (n - 1) // 2)

    def merge_key(self, L):
        if L["gk"] in graphics.KINDS and not L.get("anim"):
            return (round(L["start"], 4), round(L["dur"], 4), L["opacity"], L["fade"])
        return None

    def merge(self, group):
        return dict(group[0], gk="merged", params={"parts": [(g["gk"], g["params"]) for g in group]})

    def label(self, L):
        return f"graphic {L['gk']}"

    def zone(self, L, ctx):
        if L["gk"] != "lower_third":
            return None                                   # full-frame decoration sits under everything
        fw, fh = max(ctx.W, 1), max(ctx.H, 1)
        p = L["params"]
        width = min(0.92, max(len(p["title"]) * 0.5 * 0.046 * fh / fw * 1.1, len(p.get("subtitle", "")) * 0.7 * 0.0185 * fh / fw * 1.3) + 0.08)
        return (0.06, 0.78, 0.06 + width, 0.91) if p.get("align", "left") == "left" else (0.94 - width, 0.78, 0.94, 0.91)

    def svg(self, L):
        return L.get("gk", "graphic"), "ctext"
