"""The built-in graphic kinds: frame (the template's own border), letterbox (cinema bars), vignette, plus the internal lower_third."""
from PIL import Image

from ... import graphics, shapes
from ...graphics import GraphicKind, kind


@kind
class Frame(GraphicKind):
    name = "frame"
    amount = ("inset (fraction of frame width)", 0.015, 0.08, 0.035)

    def draw(self, W, H, params, amount, th):
        return graphics.frame(W, H, amount, th)


@kind
class Letterbox(GraphicKind):
    name = "letterbox"
    amount = ("bar height (fraction of frame height, each bar)", 0.04, 0.25, 0.10)

    def draw(self, W, H, params, amount, th):
        return graphics.letterbox(W, H, amount, shapes.of(th).letterbox_color(th))


@kind
class Vignette(GraphicKind):
    name = "vignette"
    amount = ("strength", 0.1, 1.0, 0.55)

    def draw(self, W, H, params, amount, th):
        img = Image.radial_gradient("L").resize((W, H), Image.BICUBIC)           # 0 centre -> 255 corners
        k = 255 * amount
        alpha = img.point(lambda v: int(min(255, max(0, (v / 255 - 0.35) / 0.65) ** 1.6 * k)))
        out = Image.new("RGBA", (W, H), (0, 0, 0, 255))
        out.putalpha(alpha)
        return out


@kind
class LowerThird(GraphicKind):
    name = "lower_third"
    user = False                                   # made by the lower_third op, not offered by add_graphic

    def key(self, params, amount):
        return f"lt|{params['title']}|{params.get('subtitle', '')}|{params.get('align', 'left')}"

    def draw(self, W, H, params, amount, th):
        return graphics.lower_third(W, H, params["title"], params.get("subtitle", ""), params.get("align", "left"), strict=False, theme=th)


