"""riso: two inks printed slightly out of register (a pink block shifted under a paper card with a blue outline)."""
from PIL import Image, ImageDraw

from ... import themes
from ...shapes import Shape, _c, frame_canvas, register


@register
class Riso(Shape):
    name = "riso"

    def panel(self, img, rect, th, u, key):
        x0, y0, x1, y1 = rect
        d = ImageDraw.Draw(img)
        off = 0.007 * u
        d.rectangle([x0 + off, y0 + off, x1 + off, y1 + off], fill=_c(th.accent, 235))
        d.rectangle(rect, fill=_c(th.paper, 248), outline=_c(th.accent2), width=max(2, int(th.stroke * u)))
        return None, None, True

    def staff(self, d, canvas, c):
        off = 0.004 * c.u
        d.line([(c.ax + off, c.fy0 + (0 if c.up else c.ph)), (c.ax + off, c.ay)], fill=_c(c.th.accent, 235), width=max(3, int(0.004 * c.u)))
        d.line([(c.ax, c.fy0 + (0 if c.up else c.ph)), (c.ax, c.ay)], fill=_c(c.th.accent2), width=c.lw)

    def marker(self, d, canvas, c):
        ax, ay, R = c.ax, c.ay, c.R
        off = 0.004 * c.u
        d.ellipse([ax - R + off, ay - R + off, ax + R + off, ay + R + off], fill=_c(c.th.accent, 235))
        d.ellipse([ax - R, ay - R, ax + R, ay + R], outline=_c(c.th.accent2), width=max(2, int(0.003 * c.u)))

    def frame(self, W, H, amount, th):
        f = frame_canvas(W, H, amount)
        wd, off = max(3, int(0.004 * f.h)), 0.006 * f.h
        f.d.rectangle([f.x0 + off, f.y0 + off, f.x1 + off, f.y1 + off], outline=_c(th.accent, 225), width=wd)
        f.d.rectangle([f.x0, f.y0, f.x1, f.y1], outline=_c(th.accent2, 235), width=max(2, wd // 2))
        return f.img.resize((W, H), Image.LANCZOS)

    def plate(self, img, d, box, size, ss, fill, outline, ow, th):
        sh = max(2, int(size * ss * 0.035))                               # pink ink block misregistered under the paper disc
        d.ellipse([box[0] + sh, box[1] + sh, box[2], box[3]], fill=themes.rgb(th.accent) + (235,))
        super().plate(img, d, [box[0], box[1], box[2] - sh, box[3] - sh], size, ss, fill, outline, ow, th)
