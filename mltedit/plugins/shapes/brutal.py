"""brutal: square blocks with a thick ink border and a hard offset shadow (neo-brutalism)."""
from PIL import Image, ImageDraw

from ... import themes
from ...shapes import Shape, _c, frame_canvas, register


@register
class Brutal(Shape):
    name = "brutal"

    def panel(self, img, rect, th, u, key):
        x0, y0, x1, y1 = rect
        d = ImageDraw.Draw(img)
        ink, ow, off = _c(th.ink), max(3, int(th.stroke * u)), 0.010 * u
        d.rectangle([x0 + off, y0 + off, x1 + off, y1 + off], fill=ink)
        d.rectangle(rect, fill=_c(th.accent), outline=ink, width=ow)
        return _c(th.ink), _c(th.ink), False

    def staff(self, d, canvas, c):
        d.line([(c.ax, c.fy0 + (0 if c.up else c.ph)), (c.ax, c.ay)], fill=c.ink, width=max(3, int(0.0045 * c.u)))

    def marker(self, d, canvas, c):
        d.rectangle([c.ax - c.R, c.ay - c.R, c.ax + c.R, c.ay + c.R], fill=c.acc, outline=c.ink, width=max(3, int(0.004 * c.u)))

    def frame(self, W, H, amount, th):
        f = frame_canvas(W, H, amount)
        ink, wd, off = _c(th.ink), max(4, int(0.011 * f.h)), 0.012 * f.h
        f.d.rectangle([f.x0 + off, f.y0 + off, f.x1 + off, f.y1 + off], outline=_c(th.accent), width=wd)
        f.d.rectangle([f.x0, f.y0, f.x1, f.y1], outline=ink, width=wd)
        return f.img.resize((W, H), Image.LANCZOS)

    def plate(self, img, d, box, size, ss, fill, outline, ow, th):
        sh = max(2, int(size * ss * 0.05))
        d.rectangle([box[0] + sh, box[1] + sh, box[2], box[3]], fill=themes.rgb(th.ink) + (255,))
        d.rectangle([box[0], box[1], box[2] - sh, box[3] - sh], fill=fill, outline=outline, width=max(1, int(size * ss * ow)) if outline else 0)
