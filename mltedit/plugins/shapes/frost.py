"""frost: tinted translucent glass with a luminous border (imitated: the overlay is a picture, it does not blur the video underneath)."""
from PIL import Image, ImageDraw

from ...shapes import Shape, _blur, _c, frame_canvas, register


@register
class Frost(Shape):
    name = "frost"

    def panel(self, img, rect, th, u, key):
        x0, y0, x1, y1 = rect
        r = max(2, th.radius * u)
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([x0, y0 + 0.008 * u, x1, y1 + 0.008 * u], radius=r, fill=(0, 0, 40, 95))
        img.alpha_composite(_blur(sh, 0.012 * u))
        lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(lay)
        ld.rounded_rectangle(rect, radius=r, fill=_c(th.paper, 150), outline=(255, 255, 255, 150), width=max(2, int(th.stroke * u)))
        ld.line([(x0 + r, y0 + 2), (x1 - r, y0 + 2)], fill=(255, 255, 255, 170), width=max(1, int(0.0012 * u)))
        img.alpha_composite(lay)
        return None, None, True

    def staff(self, d, canvas, c):
        d.line([(c.ax, c.fy0 + (0 if c.up else c.ph)), (c.ax, c.ay)], fill=(255, 255, 255, 210), width=c.lw + 1)

    def marker(self, d, canvas, c):
        ax, ay, R = c.ax, c.ay, c.R
        g = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(g).ellipse([ax - R, ay - R, ax + R, ay + R], outline=c.acc, width=max(3, int(0.004 * c.u)))
        canvas.alpha_composite(_blur(g, 0.006 * c.u))
        d.ellipse([ax - R, ay - R, ax + R, ay + R], fill=(255, 255, 255, 70), outline=(255, 255, 255, 235), width=c.lw + 1)

    def frame(self, W, H, amount, th):
        f = frame_canvas(W, H, amount)
        r = 0.04 * f.mn
        g = Image.new("RGBA", (f.w, f.h), (0, 0, 0, 0))
        ImageDraw.Draw(g).rounded_rectangle([f.x0, f.y0, f.x1, f.y1], radius=r, outline=_c(th.accent), width=max(4, int(0.005 * f.h)))
        img = Image.alpha_composite(_blur(g, 0.006 * f.h), f.img)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([f.x0, f.y0, f.x1, f.y1], radius=r, outline=(255, 255, 255, 190), width=max(2, int(0.0022 * f.h)))
        return img.resize((W, H), Image.LANCZOS)
