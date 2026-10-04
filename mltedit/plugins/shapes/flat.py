"""flat: a white (paper) card with a soft shadow and an accent bar on its left edge; the plain business look."""
from PIL import Image, ImageDraw

from ...shapes import Shape, _blur, _c, register


@register
class Flat(Shape):
    name = "flat"

    def panel(self, img, rect, th, u, key):
        x0, y0, x1, y1 = rect
        d = ImageDraw.Draw(img)
        r, acc = max(2, th.radius * u), _c(th.accent)
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([x0, y0 + 0.006 * u, x1, y1 + 0.006 * u], radius=r, fill=(0, 0, 0, 115))
        img.alpha_composite(_blur(sh, 0.009 * u))
        d.rounded_rectangle(rect, radius=r, fill=acc)
        d.rounded_rectangle([x0 + 0.0075 * u, y0, x1, y1], radius=r, fill=_c(th.paper, 246), corners=(False, True, True, False))
        if th.stroke:
            d.rounded_rectangle([x0 + 0.0075 * u, y0, x1, y1], radius=r, outline=_c(th.accent2), width=max(1, int(th.stroke * u)), corners=(False, True, True, False))
        return _c(th.ink), _c(th.muted), False

    def lt_bar(self, u):
        return 0.0075 * u
