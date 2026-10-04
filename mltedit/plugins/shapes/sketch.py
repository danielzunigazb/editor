"""sketch: hand-drawn paper notes, wobbly marker strokes, a highlighter under titles, a circle drawn around the point (deterministic wobble)."""
from PIL import Image, ImageDraw

from ...render import sketch
from ...shapes import Shape, _c, frame_canvas, register


@register
class Sketch(Shape):
    name = "sketch"

    def panel(self, img, rect, th, u, key):
        x0, y0, x1, y1 = rect
        d = ImageDraw.Draw(img)
        rg = sketch.rng(key, "panel")
        sk = max(3, 0.0035 * u)
        sketch.rect(d, x0 + 0.006 * u, y0 + 0.007 * u, x1 + 0.006 * u, y1 + 0.007 * u, (0, 0, 0, 70), sk * 1.3, rg, double=False)
        sketch.rect(d, x0, y0, x1, y1, _c(th.ink), sk, rg, fill=_c(th.paper, 248))
        return _c(th.ink), _c(th.muted), False

    def card_tile_colors(self, th):
        return _c(th.ink), _c(th.muted), False

    def lt_decorate(self, canvas, th, tx, ty, tw, thh, u, W, title):
        hl = Image.new("RGBA", canvas.size, (0, 0, 0, 0))                # marker highlighter under the title
        sketch.highlighter(hl, tx - 0.004 * u, ty + thh * 0.52, tx + tw + 0.006 * u, ty + thh * 0.96, _c("#ffe45c", 165), sketch.rng(title, "hl"))
        return Image.alpha_composite(canvas, hl)

    def staff(self, d, canvas, c):
        rg = sketch.rng(c.key, "staff")
        sketch.line(d, (c.ax, min(c.fy0 + c.ph * (0.0 if c.up else 1.0), c.ay)), (c.ax, c.ay + (-c.R if c.up else c.R)), c.ink, 0.0035 * c.u, rg, double=False)

    def marker(self, d, canvas, c):
        sketch.circle(d, c.ax, c.ay, c.R * 2.1, _c(c.th.accent), 0.0055 * c.u, sketch.rng(c.key, "ring"))

    def frame(self, W, H, amount, th):
        f = frame_canvas(W, H, amount)
        d, h = f.d, f.h
        rg = sketch.rng("frame", W, H)
        sk = 0.0042 * h
        sketch.rect(d, f.x0 + sk * 0.7, f.y0 + sk * 0.7, f.x1 + sk * 0.7, f.y1 + sk * 0.7, (0, 0, 0, 110), sk * 1.1, rg, double=False)   # dark under-stroke
        sketch.rect(d, f.x0, f.y0, f.x1, f.y1, (255, 255, 255, 240), sk, rg)
        for cx, cy in ((f.x0, f.y0), (f.x1, f.y0), (f.x0, f.y1), (f.x1, f.y1)):
            sketch.star(d, cx, cy, 0.016 * h, _c(th.accent2), sk * 0.8, rg)
        return f.img.resize((W, H), Image.LANCZOS)

    def plate(self, img, d, box, size, ss, fill, outline, ow, th):
        r = sketch.rng("plate", size)
        d.ellipse(box, fill=fill)
        sketch.circle(d, size * ss / 2, size * ss / 2, size * ss * 0.46, outline, max(3, size * ss * ow), r, turns=1.06)
