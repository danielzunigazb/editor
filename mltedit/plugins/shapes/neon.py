"""neon: dark glass with a glowing border, magenta corner brackets, [ BRACKETED ] labels, a crosshair on the point."""
from PIL import Image, ImageDraw

from ...shapes import Shape, _blur, _c, corners, frame_canvas, register


@register
class Neon(Shape):
    name = "neon"

    def panel(self, img, rect, th, u, key):
        x0, y0, x1, y1 = rect
        d = ImageDraw.Draw(img)
        r, acc = max(2, th.radius * u), _c(th.accent)
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(glow).rounded_rectangle(rect, radius=r, outline=acc, width=max(3, int(0.004 * u)))
        img.alpha_composite(_blur(glow, 0.011 * u))
        d.rounded_rectangle(rect, radius=r, fill=(10, 15, 28, 232), outline=acc, width=max(2, int(0.002 * u)))
        L, w, m2 = 0.03 * u, max(3, int(0.0032 * u)), _c(th.accent2)
        for cx, cy, sx, sy in ((x0, y0, 1, 1), (x1, y1, -1, -1)):
            d.line([(cx, cy + sy * L), (cx, cy), (cx + sx * L, cy)], fill=m2, width=w, joint="curve")
        return None, None, True

    def callout_title(self, text):
        return f"[ {text} ]"

    def staff(self, d, canvas, c):
        g = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(g).line([(c.ax, c.fy0 + (0 if c.up else c.ph)), (c.ax, c.ay)], fill=c.acc, width=max(3, int(0.004 * c.u)))
        canvas.alpha_composite(_blur(g, 0.008 * c.u))
        d.line([(c.ax, c.fy0 + (0 if c.up else c.ph)), (c.ax, c.ay)], fill=c.acc, width=c.lw)

    def marker(self, d, canvas, c):
        ax, ay, R, acc, lw = c.ax, c.ay, c.R, c.acc, c.lw
        g = Image.new("RGBA", canvas.size, (0, 0, 0, 0)); gd = ImageDraw.Draw(g)
        gd.ellipse([ax - R, ay - R, ax + R, ay + R], outline=acc, width=max(3, int(0.004 * c.u)))
        canvas.alpha_composite(_blur(g, 0.007 * c.u))
        d.ellipse([ax - R, ay - R, ax + R, ay + R], outline=acc, width=lw)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            d.line([(ax + dx * R * 1.15, ay + dy * R * 1.15), (ax + dx * R * 1.7, ay + dy * R * 1.7)], fill=acc, width=lw)
        d.ellipse([ax - R * 0.25, ay - R * 0.25, ax + R * 0.25, ay + R * 0.25], fill=_c(c.th.accent2))

    def frame(self, W, H, amount, th):
        f = frame_canvas(W, H, amount)
        img, d, w, h = f.img, f.d, f.w, f.h
        acc = _c(th.accent)
        L, t = 0.075 * h, max(3, int(0.003 * h))
        g = Image.new("RGBA", (w, h), (0, 0, 0, 0)); gd = ImageDraw.Draw(g)
        for cx, cy, sx, sy in corners(f.x0, f.y0, f.x1, f.y1):
            for dr in (d, gd):
                dr.line([(cx, cy + sy * L), (cx, cy), (cx + sx * L, cy)], fill=acc, width=t * (2 if dr is gd else 1), joint="curve")
            d.ellipse([cx + sx * 0.012 * h - 0.004 * h, cy + sy * 0.012 * h - 0.004 * h, cx + sx * 0.012 * h + 0.004 * h, cy + sy * 0.012 * h + 0.004 * h], fill=_c(th.accent2))
        for cx, cy, dx, dy in ((w / 2, f.y0, 1, 0), (w / 2, f.y1, 1, 0), (f.x0, h / 2, 0, 1), (f.x1, h / 2, 0, 1)):
            gd.line([(cx - dx * L * 0.35, cy - dy * L * 0.35), (cx + dx * L * 0.35, cy + dy * L * 0.35)], fill=acc, width=t * 2)
            d.line([(cx - dx * L * 0.35, cy - dy * L * 0.35), (cx + dx * L * 0.35, cy + dy * L * 0.35)], fill=acc, width=t)
        img = Image.alpha_composite(_blur(g, 0.006 * h), img)
        return img.resize((W, H), Image.LANCZOS)
