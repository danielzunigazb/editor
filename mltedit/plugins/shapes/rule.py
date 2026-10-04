"""rule: a paper card between two thin accent rules; footnote-like labels; a double keyline with square corners."""
from PIL import Image, ImageDraw

from ...shapes import Shape, _c, frame_canvas, register


@register
class Rule(Shape):
    name = "rule"

    def panel(self, img, rect, th, u, key):
        x0, y0, x1, y1 = rect
        d = ImageDraw.Draw(img)
        r, acc = max(2, th.radius * u), _c(th.accent)
        d.rounded_rectangle(rect, radius=r, fill=_c(th.paper, 240))
        rw = max(2, int(0.0025 * u)); ins = 0.009 * u
        d.rectangle([x0 + ins, y0 + ins, x1 - ins, y0 + ins + rw], fill=acc)
        d.rectangle([x0 + ins, y1 - ins - rw, x1 - ins, y1 - ins], fill=acc)
        return _c(th.ink), _c(th.muted), False

    def staff(self, d, canvas, c):
        d.line([(c.ax, c.fy0 + (0 if c.up else c.ph)), (c.ax, c.ay)], fill=c.acc, width=max(1, c.lw - 1))

    def marker(self, d, canvas, c):
        ax, ay, R = c.ax, c.ay, c.R
        d.ellipse([ax - R, ay - R, ax + R, ay + R], outline=c.acc, width=c.lw)
        d.ellipse([ax - R * 0.3, ay - R * 0.3, ax + R * 0.3, ay + R * 0.3], fill=c.acc)

    def frame(self, W, H, amount, th):
        f = frame_canvas(W, H, amount)
        d, mn = f.d, f.mn
        cream = _c(th.paper, 238)
        d.rectangle([f.x0, f.y0, f.x1, f.y1], outline=cream, width=max(2, int(0.0026 * mn)))
        g_ = 0.008 * mn
        d.rectangle([f.x0 + g_, f.y0 + g_, f.x1 - g_, f.y1 - g_], outline=cream[:3] + (170,), width=max(1, int(0.0013 * mn)))
        sq = 0.006 * mn
        for cx, cy in ((f.x0, f.y0), (f.x1, f.y0), (f.x0, f.y1), (f.x1, f.y1)):
            d.rectangle([cx - sq, cy - sq, cx + sq, cy + sq], fill=cream)
        return f.img.resize((W, H), Image.LANCZOS)
