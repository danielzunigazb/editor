"""pixel: blocks with a double-thickness border and a hard block shadow, a dotted border of square blocks (arcade)."""
from PIL import Image, ImageDraw

from ... import themes
from ...shapes import Shape, _c, frame_canvas, register


@register
class Pixel(Shape):
    name = "pixel"

    def panel(self, img, rect, th, u, key):
        x0, y0, x1, y1 = rect
        d = ImageDraw.Draw(img)
        b = max(3, int(0.0055 * u))
        d.rectangle([x0 + 1.5 * b, y0 + 1.5 * b, x1 + 1.5 * b, y1 + 1.5 * b], fill=_c(th.accent2))
        d.rectangle(rect, fill=_c(th.paper), outline=_c(th.accent), width=2 * b)
        return None, None, True

    def staff(self, d, canvas, c):
        d.line([(c.ax, c.fy0 + (0 if c.up else c.ph)), (c.ax, c.ay)], fill=c.acc, width=max(3, int(0.0045 * c.u)))

    def marker(self, d, canvas, c):
        b = max(3, int(0.0055 * c.u))
        d.rectangle([c.ax - c.R, c.ay - c.R, c.ax + c.R, c.ay + c.R], fill=_c(c.th.paper), outline=c.acc, width=b)
        d.rectangle([c.ax - b, c.ay - b, c.ax + b, c.ay + b], fill=_c(c.th.accent2))

    def frame(self, W, H, amount, th):
        f = frame_canvas(W, H, amount)
        d, acc = f.d, _c(th.accent)
        x0, y0, x1, y1 = f.x0, f.y0, f.x1, f.y1
        b = max(4, int(0.011 * f.h))
        for xx in range(int(x0), int(x1) - b, b * 2):
            d.rectangle([xx, y0, xx + b, y0 + b], fill=acc); d.rectangle([xx, y1 - b, xx + b, y1], fill=acc)
        for yy in range(int(y0), int(y1) - b, b * 2):
            d.rectangle([x0, yy, x0 + b, yy + b], fill=acc); d.rectangle([x1 - b, yy, x1, yy + b], fill=acc)
        for cx, cy in ((x0, y0), (x1 - b, y0), (x0, y1 - b), (x1 - b, y1 - b)):
            d.rectangle([cx, cy, cx + b, cy + b], fill=_c(th.accent2))
        return f.img.resize((W, H), Image.LANCZOS)

    def plate(self, img, d, box, size, ss, fill, outline, ow, th):
        sh = max(2, int(size * ss * 0.05))
        d.rectangle([box[0] + sh, box[1] + sh, box[2], box[3]], fill=themes.rgb(th.accent2) + (255,))
        d.rectangle([box[0], box[1], box[2] - sh, box[3] - sh], fill=fill, outline=outline, width=max(1, int(size * ss * ow)) if outline else 0)
