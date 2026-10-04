"""bubble: coloured bubbles with a thick ink outline and a hard sticker shadow; a rounded border with coloured dots."""
from PIL import Image, ImageDraw

from ... import themes
from ...shapes import Shape, _c, frame_canvas, register


@register
class Bubble(Shape):
    name = "bubble"

    def panel(self, img, rect, th, u, key):
        x0, y0, x1, y1 = rect
        d = ImageDraw.Draw(img)
        r = max(2, th.radius * u)
        ink, ow = _c(th.ink), max(3, int(0.004 * u))
        d.rounded_rectangle([x0 + 0.008 * u, y0 + 0.01 * u, x1 + 0.008 * u, y1 + 0.01 * u], radius=r, fill=ink)
        d.rounded_rectangle(rect, radius=r, fill=_c(th.accent2), outline=ink, width=ow)
        return None, None, True

    def staff(self, d, canvas, c):
        d.line([(c.ax, c.fy0 + (0 if c.up else c.ph)), (c.ax, c.ay)], fill=c.ink, width=max(4, int(0.0045 * c.u)))

    def marker(self, d, canvas, c):
        d.ellipse([c.ax - c.R, c.ay - c.R, c.ax + c.R, c.ay + c.R], fill=c.acc, outline=c.ink, width=max(3, int(0.004 * c.u)))

    def frame(self, W, H, amount, th):
        f = frame_canvas(W, H, amount)
        d, h, mn = f.d, f.h, f.mn
        x0, y0, x1, y1 = f.x0, f.y0, f.x1, f.y1
        ink = _c(th.ink)
        r = 0.05 * mn
        d.rounded_rectangle([x0, y0, x1, y1], radius=r, outline=ink, width=int(0.011 * h))
        d.rounded_rectangle([x0 + 0.0085 * h, y0 + 0.0085 * h, x1 - 0.0085 * h, y1 - 0.0085 * h], radius=r * 0.85, outline=(255, 255, 255, 255), width=int(0.006 * h))
        for (cx, cy), col in zip(((x0, y0), (x1, y0), (x1, y1), (x0, y1)), (th.accent, *th.extra, th.accent2)):
            rr = 0.016 * h
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=_c(col), outline=ink, width=int(0.004 * h))
        return f.img.resize((W, H), Image.LANCZOS)

    def plate(self, img, d, box, size, ss, fill, outline, ow, th):
        d.ellipse([box[0] + size * ss * 0.03, box[1] + size * ss * 0.04, box[2] + size * ss * 0.03, box[3] + size * ss * 0.04], fill=themes.rgb(th.ink) + (255,))   # hard sticker shadow
        super().plate(img, d, box, size, ss, fill, outline, ow, th)
