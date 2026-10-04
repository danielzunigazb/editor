"""Card backgrounds of the second templates: brutal, grain, black, scanlines, dither, halftone, blobs. All deterministic, PIL only."""
from PIL import Image

from ... import themes
from ...cardkit import card_bg, dither as _dither, grain, paper_layer, radial, ref

BAYER4 = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))


def _cols(th):
    return themes.rgb(th.accent), themes.rgb(th.accent2), themes.rgb(th.ink)


@card_bg("brutal")
def brutal(W, H, th, rg):                                                         # paper, thick ink border with an accent2 copy, accent blocks in the corners
    acc, acc2, ink = _cols(th)
    img, ov, d = paper_layer(W, H, th)
    bw, off, ins = max(5, int(0.012 * H)), int(0.014 * H), int(0.03 * H)
    d.rectangle([ins + off, ins + off, W - ins + off, H - ins + off], outline=acc2 + (255,), width=bw)
    d.rectangle([ins, ins, W - ins, H - ins], outline=ink + (255,), width=bw)
    for x0, y0, w, h in ((0.07, 0.10, 0.10, 0.07), (0.83, 0.82, 0.10, 0.07), (0.86, 0.12, 0.05, 0.05), (0.07, 0.80, 0.05, 0.05)):
        rx, ry, rw, rh = x0 * W, y0 * H, w * W, h * H
        d.rectangle([rx + off * 0.6, ry + off * 0.6, rx + rw + off * 0.6, ry + rh + off * 0.6], fill=ink + (255,))
        d.rectangle([rx, ry, rx + rw, ry + rh], fill=acc + (255,), outline=ink + (255,), width=max(3, bw // 2))
    img.alpha_composite(ov)
    return img


@card_bg("grain")
def grain_bg(W, H, th, rg):                                                       # warm paper, an arch in the accent, heavy grain, a double rule
    acc, _, _ = _cols(th)
    img, ov, d = paper_layer(W, H, th)
    d.pieslice([W * 0.62, H * 0.45, W * 1.18, H * 1.55], 180, 270, fill=acc + (52,))
    d.ellipse([W * -0.08, H * -0.35, W * 0.2, H * 0.12], fill=acc + (38,))
    ins = int(0.045 * H)
    d.rectangle([ins, ins, W - ins, H - ins], outline=acc + (210,), width=max(2, int(0.003 * H)))
    d.rectangle([ins + int(0.012 * H), ins + int(0.012 * H), W - ins - int(0.012 * H), H - ins - int(0.012 * H)], outline=acc + (130,), width=max(1, int(0.0014 * H)))
    img.alpha_composite(ov)
    vig = Image.radial_gradient("L").resize((W, H)).point(lambda v: int(v * 0.22))
    dark = Image.new("RGBA", (W, H), (90, 60, 30, 255)); dark.putalpha(vig); img.alpha_composite(dark)
    grain(img, rg, int(W * H / 1800), 8, 30)
    return img


@card_bg("black")
def black(W, H, th, rg):                                                          # near-black, a faint bloom, letterbox bars with an accent hairline, film grain
    acc, _, _ = _cols(th)
    img, ov, d = paper_layer(W, H, th)
    img.alpha_composite(radial(W, H, W / 2, H * 0.5, max(W, H) * 0.65, ref(th, th.card.get("bg_options", {}).get("glow", "accent2")), 0.55))
    bar = int(0.11 * H)
    d.rectangle([0, 0, W, bar], fill=(0, 0, 0, 255)); d.rectangle([0, H - bar, W, H], fill=(0, 0, 0, 255))
    d.rectangle([0, bar, W, bar + max(2, int(0.0028 * H))], fill=acc + (255,)); d.rectangle([0, H - bar - max(2, int(0.0028 * H)), W, H - bar], fill=acc + (255,))
    img.alpha_composite(ov)
    grain(img, rg, int(W * H / 2500), 8, 24, dark=(255, 255, 255), light=(255, 255, 255))
    return img


@card_bg("scanlines")
def scanlines(W, H, th, rg):                                                      # black, scanlines, a window bar with three squares, a block cursor
    acc, acc2, _ = _cols(th)
    img, ov, d = paper_layer(W, H, th)
    step = max(3, int(0.004 * H))
    for y in range(0, H, step * 2):
        d.rectangle([0, y, W, y + step - 1], fill=acc + (14,))
    bar = int(0.06 * H)
    d.rectangle([0, 0, W, bar], fill=(20, 24, 36, 255)); d.line([(0, bar), (W, bar)], fill=acc + (200,), width=max(2, int(0.0022 * H)))
    for i, col in enumerate(((230, 80, 80), (230, 190, 70), acc)):
        cx = int(0.03 * H) + i * int(0.04 * H)
        d.rectangle([cx - int(0.011 * H), bar // 2 - int(0.011 * H), cx + int(0.011 * H), bar // 2 + int(0.011 * H)], fill=col + (255,))
    d.rectangle([int(0.05 * W), int(0.9 * H), int(0.05 * W) + int(0.016 * H), int(0.9 * H) + int(0.034 * H)], fill=acc2 + (255,))
    img.alpha_composite(ov)
    return img


@card_bg("dither")
def dither_bg(W, H, th, rg):                                                      # black, a Bayer-dithered accent2 glow from the bottom edge, a block border
    acc, acc2, _ = _cols(th)
    img, ov, d = paper_layer(W, H, th)
    blk = max(4, int(0.0075 * H))
    sw, sh = W // blk + 1, H // blk + 1
    small = Image.new("L", (sw, sh), 0)
    px = small.load()
    for y in range(sh):
        g = max(0.0, (y / sh - 0.58) / 0.42)                                       # 0 above 58% of the height, 1 at the bottom
        for x in range(sw):
            px[x, y] = 255 if g * 16 > BAYER4[y % 4][x % 4] + 0.5 else 0
    mask = small.resize((sw * blk, sh * blk), Image.NEAREST).crop((0, 0, W, H))
    glow = Image.new("RGBA", (W, H), acc2 + (255,)); glow.putalpha(mask)
    img.alpha_composite(glow)
    bb = max(4, int(0.011 * H))
    for xx in range(int(0.03 * W), int(0.97 * W) - bb, bb * 2):
        d.rectangle([xx, int(0.04 * H), xx + bb, int(0.04 * H) + bb], fill=acc + (255,)); d.rectangle([xx, int(0.96 * H) - bb, xx + bb, int(0.96 * H)], fill=acc + (255,))
    img.alpha_composite(ov)
    return img


@card_bg("halftone")
def halftone(W, H, th, rg):                                                       # warm paper, accent halftone fading from a corner, a misregistered disc, grain
    acc, acc2, _ = _cols(th)
    img, ov, d = paper_layer(W, H, th)
    step = max(8, int(0.03 * H))
    for gy in range(0, H + step, step):
        for gx in range(0, W + step, step):
            t = max(0.0, 1 - ((gx - W) ** 2 + (gy - H * 0.02) ** 2) ** 0.5 / (0.40 * W))
            r = step * 0.5 * t
            if r > 0.8:
                d.ellipse([gx - r, gy - r, gx + r, gy + r], fill=acc + (235,))
    R = 0.2 * H
    d.ellipse([W * 0.07 - R, H * 0.86 - R, W * 0.07 + R, H * 0.86 + R], fill=acc2 + (235,))
    d.ellipse([W * 0.07 - R + 0.008 * H, H * 0.86 - R + 0.006 * H, W * 0.07 + R + 0.008 * H, H * 0.86 + R + 0.006 * H], outline=acc + (255,), width=max(3, int(0.005 * H)))
    img.alpha_composite(ov)
    grain(img, rg, int(W * H / 2200), 8, 26)
    return img


@card_bg("blobs")
def blobs(W, H, th, rg):
    """Dark base, soft colour blobs. Options: strength (blob opacity), second (colour of the second blob), third (an optional third blob),
    grid (a faint dot grid). Dithered: soft blobs on 8 bits band into visible rings; noise of +-7 levels survives the H.264 encode and hides them."""
    o = th.card.get("bg_options", {})
    acc, _, _ = _cols(th)
    img, ov, d = paper_layer(W, H, th)
    k = float(o.get("strength", 0.8))
    img.alpha_composite(radial(W, H, W * 0.18, H * 0.25, H * 1.0, acc, k))
    img.alpha_composite(radial(W, H, W * 0.85, H * 0.8, H * 1.0, ref(th, o.get("second", "accent2")), k * 0.85))
    if o.get("third"):
        img.alpha_composite(radial(W, H, W * 0.6, H * 0.15, H * 0.55, ref(th, o["third"]), 0.4))
    if o.get("grid"):
        step = int(0.05 * H)
        for y in range(step, H, step):
            for x in range(step, W, step):
                d.point((x, y), fill=(255, 255, 255, 40))
        img.alpha_composite(ov)
    return _dither(img, rg)
