"""term: black square panels with a phosphor line, '> ' prompts, dashed staffs, brackets around the point, tick marks on the frame."""
from PIL import Image, ImageDraw

from ...shapes import Shape, _c, corners, frame_canvas, register


@register
class Term(Shape):
    name = "term"

    def panel(self, img, rect, th, u, key):
        ImageDraw.Draw(img).rectangle(rect, fill=_c(th.paper, 238), outline=_c(th.accent), width=max(2, int(th.stroke * u)))
        return None, None, True

    def callout_title(self, text):
        return f"> {text}"

    def staff(self, d, canvas, c):                                       # dashed staff
        y_a, y_b = sorted((c.fy0 + (0 if c.up else c.ph), c.ay))
        seg = max(4, int(0.008 * c.u))
        for yy in range(int(y_a), int(y_b), seg * 2):
            d.line([(c.ax, yy), (c.ax, min(yy + seg, y_b))], fill=c.acc, width=c.lw)

    def marker(self, d, canvas, c):                                      # square brackets around the point
        ax, ay, R = c.ax, c.ay, c.R
        L2 = R * 1.5
        for sx_, sy_ in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            d.line([(ax + sx_ * L2, ay + sy_ * (L2 - R * 0.9)), (ax + sx_ * L2, ay + sy_ * L2), (ax + sx_ * (L2 - R * 0.9), ay + sy_ * L2)], fill=c.acc, width=c.lw + 1)
        d.rectangle([ax - R * 0.28, ay - R * 0.28, ax + R * 0.28, ay + R * 0.28], fill=_c(c.th.accent2))

    def frame(self, W, H, amount, th):
        f = frame_canvas(W, H, amount)
        acc = _c(th.accent)
        L, t = 0.06 * f.h, max(2, int(0.0028 * f.h))
        for cx, cy, sx, sy in corners(f.x0, f.y0, f.x1, f.y1):
            f.d.line([(cx, cy + sy * L), (cx, cy), (cx + sx * L, cy)], fill=acc, width=t)
        for k in range(1, 8):
            tick = 0.012 * f.h * (2 if k == 4 else 1)
            f.d.line([(f.x0 + (f.x1 - f.x0) * k / 8, f.y0), (f.x0 + (f.x1 - f.x0) * k / 8, f.y0 + tick)], fill=acc, width=max(1, t // 2))
        return f.img.resize((W, H), Image.LANCZOS)

    def plate(self, img, d, box, size, ss, fill, outline, ow, th):
        d.rectangle(box, fill=fill, outline=outline, width=max(1, int(size * ss * ow)) if outline else 0)
