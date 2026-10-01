"""Luxury graphic resources, drawn procedurally (no binary assets): gold frame, cinematic bars, vignette and an
elegant lower third. Each function returns a transparent W x H RGBA image the same size as the video frame, so it is
placed full-frame by the editor like any other overlay."""
import hashlib, os

from PIL import Image, ImageDraw, ImageFilter

import textrender as T

KINDS = ("frame", "letterbox", "vignette")
AMOUNT = {            # kind -> (name, min, max, default)
    "frame": ("inset (fraction of frame width)", 0.015, 0.08, 0.035),
    "letterbox": ("bar height (fraction of frame height, each bar)", 0.04, 0.25, 0.10),
    "vignette": ("strength", 0.1, 1.0, 0.55),
}
GOLD = (217, 178, 90)
SS = 2                # supersampling factor for smooth thin lines


def frame(W, H, amount):
    """Double gold keyline with small diamonds at the corners and edge midpoints."""
    w, h = W * SS, H * SS
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    inset = amount * w
    lw = max(2, int(round(0.0022 * min(w, h))))
    gap = 0.011 * min(w, h)
    col = GOLD + (235,)
    d.rectangle([inset, inset, w - inset, h - inset], outline=col, width=lw)
    d.rectangle([inset + gap, inset + gap, w - inset - gap, h - inset - gap], outline=GOLD + (170,), width=max(1, lw // 2))
    r = 0.0085 * min(w, h)
    for cx, cy in [(inset, inset), (w - inset, inset), (inset, h - inset), (w - inset, h - inset),
                   (w / 2, inset), (w / 2, h - inset), (inset, h / 2), (w - inset, h / 2)]:
        d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=col)
    return img.resize((W, H), Image.LANCZOS)


def letterbox(W, H, amount):
    """Cinema bars with a hairline of gold on the inner edge."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bh = int(round(amount * H))
    d.rectangle([0, 0, W, bh], fill=(0, 0, 0, 255))
    d.rectangle([0, H - bh, W, H], fill=(0, 0, 0, 255))
    t = max(1, int(round(H * 0.0012)))
    d.rectangle([0, bh, W, bh + t], fill=GOLD + (170,))
    d.rectangle([0, H - bh - t, W, H - bh], fill=GOLD + (170,))
    return img


def vignette(W, H, amount):
    """Soft dark falloff toward the edges."""
    g = Image.radial_gradient("L").resize((W, H), Image.BICUBIC)           # 0 centre -> 255 corners
    k = 255 * amount
    alpha = g.point(lambda v: int(min(255, max(0, (v / 255 - 0.35) / 0.65) ** 1.6 * k)))
    img = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    img.putalpha(alpha)
    return img


def _fit(line, style, px, max_w, track_em=None):
    """Largest font <= px whose line fits in max_w (down to 55%); returns (font, track_px) or None."""
    st = T.STYLES[style]
    cur = px
    while cur >= px * T.MIN_SHRINK:
        f = T.make_font(style, cur)
        tr = (st["tracking"] if track_em is None else track_em) * f.size
        if T.text_width(line.upper() if st["upper"] else line, f, tr) <= max_w:
            return f, tr
        cur *= 0.94
    return None


def lower_third(W, H, title, subtitle="", align="left", strict=True):
    """Glass panel with a gold side bar; title in luxury gold, subtitle in tracked ivory capitals."""
    title = T.clean(title, "luxury")
    subtitle = T.clean(subtitle, "modern") if subtitle else ""
    if "\n" in title or "\n" in subtitle:
        raise ValueError("lower third title and subtitle must be single lines")
    if len(title) > 60 or len(subtitle) > 80:
        raise ValueError("lower third: title max 60 characters, subtitle max 80")
    max_w = 0.78 * W
    ft = _fit(title, "luxury", 0.046 * H, max_w)
    fs = _fit(subtitle, "modern", 0.0185 * H, max_w, 0.16) if subtitle else None
    if ft is None or (subtitle and fs is None):
        if strict:
            raise ValueError("lower third text is too long to fit; shorten the title or subtitle")
        ft = ft or (T.make_font("luxury", 0.046 * H * T.MIN_SHRINK), 0)
        fs = fs or (T.make_font("modern", 0.0185 * H * T.MIN_SHRINK), 0)
    (tf, ttr), sub = ft, (fs if subtitle else None)
    sub_txt = subtitle.upper()
    tw = T.text_width(title, tf, ttr)
    sw = T.text_width(sub_txt, sub[0], sub[1]) if sub else 0
    ta, td = tf.getmetrics()
    th = (ta + td)
    sh = sum(sub[0].getmetrics()) if sub else 0
    pad = 0.022 * H
    gapv = 0.012 * H if sub else 0
    ph = th + gapv + sh + 2 * pad
    pw = min(0.92 * W, max(tw, sw) + 2 * pad + 0.016 * W)
    margin = 0.06 * W
    x0 = margin if align == "left" else W - margin - pw
    y1 = H - 0.09 * H
    y0 = y1 - ph
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([x0, y0, x0 + pw, y1], radius=0.012 * H, fill=(8, 8, 11, 165), outline=GOLD + (110,), width=max(1, int(H * 0.0012)))
    bar = max(3, int(0.006 * W))
    bx = x0 if align == "left" else x0 + pw - bar
    bar_img = T.gradient(bar, int(ph), T.GOLD)
    img.paste(bar_img, (int(bx), int(y0)))
    tx = x0 + pad + (bar if align == "left" else 0)
    ty = y0 + pad
    mask = Image.new("L", (W, H), 0)
    T.draw_tracked(ImageDraw.Draw(mask), tx, ty, title, tf, ttr, 255)
    sd = Image.new("L", (W, H), 0); sd.paste(mask, (0, int(round(0.05 * tf.size))))
    sd = sd.filter(ImageFilter.GaussianBlur(max(0.5, 0.07 * tf.size))).point(lambda v: int(v * 0.7))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 255)); shadow.putalpha(sd)
    img = Image.alpha_composite(img, shadow)
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0)); grad.paste(T.gradient(W, int(th), T.GOLD), (0, int(ty)))
    grad.putalpha(mask)
    img = Image.alpha_composite(img, grad)
    if sub:
        sm = Image.new("L", (W, H), 0)
        T.draw_tracked(ImageDraw.Draw(sm), tx, ty + th + gapv, sub_txt, sub[0], sub[1], 255)
        ivory = Image.new("RGBA", (W, H), (246, 236, 214, 255)); ivory.putalpha(sm)
        img = Image.alpha_composite(img, ivory)
    return img


def validate(kind, params):
    """Raise ValueError for bad graphic params (pure checks, no rendering)."""
    if kind not in KINDS:
        raise ValueError(f"unknown graphic '{kind}'; choose one of {KINDS}")
    name, lo, hi, _ = AMOUNT[kind]
    a = params.get("amount")
    if a is not None and not (isinstance(a, (int, float)) and lo <= a <= hi):
        raise ValueError(f"{kind}: amount ({name}) must be between {lo} and {hi}")


def render(kind, W, H, cache_dir, **params):
    """Render a graphic (or lower third) to a cached PNG and return its path."""
    if kind == "lower_third":
        key = f"lt|{params['title']}|{params.get('subtitle','')}|{params.get('align','left')}"
    else:
        validate(kind, params)
        amount = params.get("amount") if params.get("amount") is not None else AMOUNT[kind][3]
        key = f"{kind}|{amount}"
    out = os.path.join(cache_dir, f"gfx_{hashlib.sha1(f'v1|{key}|{W}|{H}'.encode()).hexdigest()[:16]}.png")
    if os.path.exists(out):
        return out
    if kind == "lower_third":
        img = lower_third(W, H, params["title"], params.get("subtitle", ""), params.get("align", "left"), strict=False)
    else:
        img = {"frame": frame, "letterbox": letterbox, "vignette": vignette}[kind](W, H, amount)
    os.makedirs(cache_dir, exist_ok=True)
    tmp = out + f".{os.getpid()}.tmp"
    img.save(tmp, format="PNG")
    os.replace(tmp, out)
    return out
