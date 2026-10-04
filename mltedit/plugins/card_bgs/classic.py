"""Card backgrounds of the first templates: marble, gradient, paper, dots, dark_grid, confetti."""
from PIL import Image, ImageDraw, ImageFilter

from ... import shapes, themes
from ...cardkit import c_, card_bg, radial, ref, vgrad
from ...render import sketch


@card_bg("gradient")
def gradient(W, H, th, rg):                                                       # navy to deep blue, a soft light band, an accent strip at the bottom
    o = th.card.get("bg_options", {})
    img = vgrad(W, H, ref(th, o.get("top", "ink")), ref(th, o.get("bottom", "ink"))).convert("RGBA")
    img.alpha_composite(radial(W, H, W * 0.85, H * 0.15, H * 0.9, ref(th, o.get("light", "accent")), 0.35))
    ImageDraw.Draw(img).rectangle([0, int(H * 0.94), W, H], fill=c_(th.accent))
    return img


@card_bg("paper")
def paper(W, H, th, rg):                                                          # paper; option frame: light vignette, speckle and a double accent keyline
    base = Image.new("RGBA", (W, H), c_(th.paper))
    if th.card.get("bg_options", {}).get("frame", True):
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        vig = Image.radial_gradient("L").resize((W, H)).point(lambda v: int(v * 0.16))
        dark = Image.new("RGBA", (W, H), (90, 70, 30, 255)); dark.putalpha(vig); base.alpha_composite(dark)
        d = ImageDraw.Draw(ov)                                                    # translucent details go on their own layer: ImageDraw would REPLACE alpha
        for _ in range(int(W * H / 5000)):
            x, y = rg.randrange(W), rg.randrange(H)
            d.point((x, y), fill=(120, 100, 70, rg.randrange(10, 28)))
        ins = int(0.045 * H)
        d.rectangle([ins, ins, W - ins, H - ins], outline=c_(th.accent, 220), width=max(2, int(0.003 * H)))
        d.rectangle([ins + int(0.012 * H), ins + int(0.012 * H), W - ins - int(0.012 * H), H - ins - int(0.012 * H)], outline=c_(th.accent, 150), width=max(1, int(0.0014 * H)))
        base.alpha_composite(ov)
    return base


@card_bg("dots")
def dots(W, H, th, rg):                                                           # paper, dot grid, a few doodles in the corners
    img = Image.new("RGBA", (W, H), c_(th.paper))
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    step = int(0.04 * H)
    for y in range(step, H, step):
        for x in range(step, W, step):
            d.ellipse([x - 1.5, y - 1.5, x + 1.5, y + 1.5], fill=(150, 150, 140, 90))
    r = sketch.rng("cardbg", W, H)
    sketch.star(d, W * 0.09, H * 0.14, 0.035 * H, c_(th.accent2), 0.005 * H, r)
    sketch.arrow(d, (W * 0.86, H * 0.84), (W * 0.93, H * 0.74), c_(th.accent), 0.005 * H, r)
    sketch.circle(d, W * 0.9, H * 0.15, 0.03 * H, c_(th.accent), 0.005 * H, r)
    img.alpha_composite(ov)
    return img


@card_bg("dark_grid")
def dark_grid(W, H, th, rg):                                                      # dark, faint grid, two coloured glows, corner brackets
    img = Image.new("RGBA", (W, H), c_(th.paper))
    img.alpha_composite(radial(W, H, W * 0.15, H * 0.2, H * 0.9, themes.rgb(th.accent), 0.28))
    img.alpha_composite(radial(W, H, W * 0.9, H * 0.9, H * 0.9, themes.rgb(th.accent2), 0.22))
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    step = int(0.06 * H)
    for x in range(0, W, step):
        d.line([(x, 0), (x, H)], fill=c_(th.accent, 16), width=1)
    for y in range(0, H, step):
        d.line([(0, y), (W, y)], fill=c_(th.accent, 16), width=1)
    L, t, ins = 0.09 * H, max(3, int(0.004 * H)), 0.05 * H
    for cx, cy, sx, sy in ((ins, ins, 1, 1), (W - ins, ins, -1, 1), (ins, H - ins, 1, -1), (W - ins, H - ins, -1, -1)):
        d.line([(cx, cy + sy * L), (cx, cy), (cx + sx * L, cy)], fill=c_(th.accent), width=t, joint="curve")
    img.alpha_composite(ov)
    return img


@card_bg("confetti")
def confetti(W, H, th, rg):                                                       # warm paper with scattered confetti and a thick border
    img = Image.new("RGBA", (W, H), c_(th.paper))
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    cols = (th.accent, *th.extra, th.accent2)
    for _ in range(90):
        x, y, s = rg.randrange(W), rg.randrange(H), rg.uniform(0.012, 0.03) * H
        if 0.08 * W < x < 0.92 * W and 0.27 * H < y < 0.73 * H:                # keep the middle band clear: that is where the text goes
            continue
        col, shape = c_(rg.choice(cols), 230), rg.randrange(3)
        if shape == 0:
            d.ellipse([x - s, y - s, x + s, y + s], fill=col)
        elif shape == 1:
            d.rounded_rectangle([x - s, y - s * 0.5, x + s, y + s * 0.5], radius=s * 0.4, fill=col)
        else:
            d.polygon([(x, y - s), (x + s, y + s * 0.8), (x - s, y + s * 0.8)], fill=col)
    d.rounded_rectangle([0.025 * H, 0.025 * H, W - 0.025 * H, H - 0.025 * H], radius=0.05 * H, outline=c_(th.ink), width=max(4, int(0.009 * H)))
    img.alpha_composite(ov)
    return img


@card_bg("marble")
def marble(W, H, th, rg):                                                         # near-black, a warm centre glow, faint veins in the accent, the theme's frame
    img = Image.new("RGBA", (W, H), c_(th.paper))
    img.alpha_composite(radial(W, H, W / 2, H * 0.45, max(W, H) * 0.7, ref(th, th.card.get("bg_options", {}).get("glow", "accent2")), 0.5))
    veins = Image.new("RGBA", (W, H), (0, 0, 0, 0)); vd = ImageDraw.Draw(veins)
    for _ in range(7):
        x, y = rg.randrange(W), rg.randrange(H)
        pts = []
        for i in range(24):
            x += rg.uniform(-0.04, 0.06) * W; y += rg.uniform(-0.05, 0.05) * H
            pts.append((x, y))
        vd.line(pts, fill=c_(th.accent, rg.randrange(28, 60)), width=max(1, int(0.002 * H)), joint="curve")
    img.alpha_composite(veins.filter(ImageFilter.GaussianBlur(0.0025 * H)))
    img.alpha_composite(shapes.of(th).frame(W, H, 0.035, th))
    return img
