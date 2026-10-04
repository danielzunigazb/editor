"""Helpers shared by card background and rule plugins (plugins/card_bgs, plugins/card_rules), and their registration decorators."""
from PIL import Image, ImageChops, ImageDraw

from . import registry, themes

c_ = lambda hex_, a=255: themes.rgb(hex_) + (a,)


def card_bg(name):
    """@card_bg("paper") registers fn(W, H, th, rg) -> RGBA image (rg: the card's seeded random.Random; options: th.card['bg_options'])."""
    return registry.decorator("card_bg", name)


def card_rule(name):
    """@card_rule("diamond") registers fn(d, th, x, x0, y, w, H, acc, t): the divider under a card title (d draws on a transparent layer)."""
    return registry.decorator("card_rule", name)


def vgrad(W, H, top, bottom):
    m = Image.linear_gradient("L").resize((W, H))
    return Image.composite(Image.new("RGB", (W, H), bottom), Image.new("RGB", (W, H), top), m)


def radial(W, H, cx, cy, rad, color, alpha):
    g = Image.radial_gradient("L").resize((int(rad * 2), int(rad * 2)))          # white centre -> black edge: invert for a soft spot
    spot = Image.new("RGBA", g.size, color + (255,)); spot.putalpha(g.point(lambda v: int((255 - v) * alpha)))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0)); layer.paste(spot, (int(cx - rad), int(cy - rad)), spot)
    return layer


def grain(img, rg, n, lo=10, hi=34, dark=(90, 70, 40), light=(255, 255, 255)):
    """Paper/film grain: n translucent specks on their own layer (ImageDraw would replace alpha)."""
    W, H = img.size
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for _ in range(n):
        d.point((rg.randrange(W), rg.randrange(H)), fill=(rg.choice((dark, light)) + (rg.randrange(lo, hi),)))
    img.alpha_composite(ov)


def dither(img, rg, amp=7):
    """Add seeded uniform noise of +-amp levels (sigma about 4) to an RGBA image's colour: breaks up the contour rings of smooth gradients.
    Seeded from the card's own random.Random, so the same card is the same pixels."""
    W, H = img.size
    noise = Image.frombytes("L", (W, H), rg.randbytes(W * H)).point(lambda v: 128 + (v * (2 * amp + 1) >> 8) - amp).convert("RGB")
    rgb = ImageChops.add(img.convert("RGB"), noise, scale=1, offset=-128)
    out = rgb.convert("RGBA")
    out.putalpha(img.getchannel("A"))
    return out


def paper_layer(W, H, th):
    """(base image filled with the paper colour, transparent overlay, its ImageDraw)."""
    img = Image.new("RGBA", (W, H), c_(th.paper))
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    return img, ov, ImageDraw.Draw(ov)


def ref(th, v):
    """A colour option: a palette name (accent...) or #rrggbb -> RGB."""
    return themes.rgb(getattr(th, v)) if v in themes.PALETTE else themes.rgb(v)
