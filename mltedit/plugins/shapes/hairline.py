"""hairline: no panels at all; white type with a short accent hairline, thin white keylines, a small accent dot. Works on anything."""
from PIL import Image, ImageDraw

from ...shapes import SS, Shape, _c, frame_canvas, register


@register
class Hairline(Shape):
    name = "hairline"

    def panel(self, img, rect, th, u, key):
        return None, None, True

    def lt_decorate(self, canvas, th, tx, ty, tw, thh, u, W, title):
        ImageDraw.Draw(canvas).rectangle([tx, ty - 0.012 * u, tx + min(tw, 0.12 * W * SS), ty - 0.012 * u + max(2, 0.0035 * u)], fill=_c(th.accent))
        return canvas

    def sub_color(self, th):
        return _c("#e8e8e8")

    def staff(self, d, canvas, c):
        d.line([(c.ax, c.fy0 + (0 if c.up else c.ph)), (c.ax, c.ay)], fill=(0, 0, 0, 120), width=int(0.0045 * c.u))   # dark halo so the white hairline reads on bright ground
        d.line([(c.ax, c.fy0 + (0 if c.up else c.ph)), (c.ax, c.ay)], fill=_c("#ffffff"), width=int(0.0026 * c.u))

    def marker(self, d, canvas, c):
        ax, ay, R = c.ax, c.ay, c.R
        d.ellipse([ax - R * 0.95, ay - R * 0.95, ax + R * 0.95, ay + R * 0.95], fill=(0, 0, 0, 110))
        d.ellipse([ax - R * 0.75, ay - R * 0.75, ax + R * 0.75, ay + R * 0.75], fill=c.acc, outline=_c("#ffffff"), width=max(2, int(0.003 * c.u)))

    def frame(self, W, H, amount, th):
        f = frame_canvas(W, H, amount)
        f.d.rectangle([f.x0, f.y0, f.x1, f.y1], outline=(255, 255, 255, 215), width=max(1, int(0.0012 * f.mn)))
        return f.img.resize((W, H), Image.LANCZOS)

    def tile(self, layer, rect, th, H, key):
        ImageDraw.Draw(layer).rectangle(rect, outline=_c(th.ink, 90), width=max(1, int(0.0016 * H)))
        return _c(th.ink), _c(th.muted), False
