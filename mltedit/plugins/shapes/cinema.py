"""cinema: no panels, a slim accent bar, corner ticks; the type carries the look."""
from PIL import Image, ImageDraw

from ...shapes import Shape, _c, corners, frame_canvas, register


@register
class Cinema(Shape):
    name = "cinema"

    def panel(self, img, rect, th, u, key):
        x0, y0, x1, y1 = rect
        ImageDraw.Draw(img).rectangle([x0, y0, x0 + max(3, int(0.0055 * u)), y1], fill=_c(th.accent))
        return None, None, True

    def tile(self, layer, rect, th, H, key):
        ImageDraw.Draw(layer).rectangle(rect, fill=(255, 255, 255, 14))   # a faint plate: the panel itself is only an accent bar
        return self.panel(layer, rect, th, H, key)

    def staff(self, d, canvas, c):
        d.line([(c.ax, c.fy0 + (0 if c.up else c.ph)), (c.ax, c.ay)], fill=c.acc, width=c.lw + 1)

    def marker(self, d, canvas, c):
        ax, ay, R = c.ax, c.ay, c.R
        d.ellipse([ax - R, ay - R, ax + R, ay + R], outline=c.acc, width=c.lw + 1)
        d.ellipse([ax - R * 0.3, ay - R * 0.3, ax + R * 0.3, ay + R * 0.3], fill=_c("#ffffff"))

    def frame(self, W, H, amount, th):
        f = frame_canvas(W, H, amount)
        L, t = 0.05 * f.h, max(2, int(0.0028 * f.h))
        f.d.rectangle([f.x0, f.y0, f.x1, f.y1], outline=(255, 255, 255, 70), width=max(1, int(0.001 * f.h)))
        for cx, cy, sx, sy in corners(f.x0, f.y0, f.x1, f.y1):
            f.d.line([(cx, cy + sy * L), (cx, cy), (cx + sx * L, cy)], fill=(255, 255, 255, 235), width=t)
        return f.img.resize((W, H), Image.LANCZOS)
