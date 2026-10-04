"""Luxury graphic resources, drawn procedurally (no binary assets): gold frame, cinematic bars, vignette and an
elegant lower third. Each function returns a transparent W x H RGBA image the same size as the video frame, so it is
placed full-frame by the editor like any other overlay."""
import hashlib, os

from PIL import Image, ImageDraw, ImageFilter

import textrender as T
import themes

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


def letterbox(W, H, amount, color=None):
    """Cinema bars with a hairline of gold (or the theme's accent) on the inner edge."""
    color = color or GOLD
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bh = int(round(amount * H))
    d.rectangle([0, 0, W, bh], fill=(0, 0, 0, 255))
    d.rectangle([0, H - bh, W, H], fill=(0, 0, 0, 255))
    t = max(1, int(round(H * 0.0012)))
    d.rectangle([0, bh, W, bh + t], fill=tuple(color) + (170,))
    d.rectangle([0, H - bh - t, W, H - bh], fill=tuple(color) + (170,))
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


def _lower_third_glass(W, H, title, subtitle="", align="left", strict=True):
    """Glass panel with a gold side bar; title in luxury gold, subtitle in tracked ivory capitals (the luxury template)."""
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


def _theme(theme):
    """A themes.Theme from a project theme spec ({"name", "accent"} | name | Theme | None)."""
    return theme if isinstance(theme, themes.Theme) else themes.get(theme)


def lower_third(W, H, title, subtitle="", align="left", strict=True, theme=None):
    """Name/role panel in the project's template (luxury = the original glass panel with the gold bar)."""
    th = _theme(theme)
    if th.shape == "glass":
        return _lower_third_glass(W, H, title, subtitle, align, strict)
    import themed
    return themed.lower_third(W, H, title, subtitle, align, strict, th)


def callout(W, H, title, subtitle="", side="ne", strict=True, theme=None):
    """(image, ax, ay): a label pinned to a point; the image is small and (ax, ay) is the ring centre inside it."""
    th = _theme(theme)
    if th.shape == "glass":
        return _callout_glass(W, H, title, subtitle, side, strict)
    import themed
    return themed.callout(W, H, title, subtitle, side, strict, th)


def render_merged(parts, W, H, cache_dir):
    """Alpha-composite several graphics (list of (kind, params)) into ONE cached PNG, so a single qtblend draws them."""
    key = "merged|" + "|".join(f"{k}:{sorted(p.items())}" for k, p in parts)
    out = os.path.join(cache_dir, f"gfx_{hashlib.sha1(f'v1|{key}|{W}|{H}'.encode()).hexdigest()[:16]}.png")
    if os.path.exists(out):
        return out
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for kind, params in parts:
        with Image.open(render(kind, W, H, cache_dir, **params)) as layer:
            img = Image.alpha_composite(img, layer.convert("RGBA"))
    os.makedirs(cache_dir, exist_ok=True)
    tmp = out + f".{os.getpid()}.tmp"
    img.save(tmp, format="PNG")
    os.replace(tmp, out)
    return out


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
    th = _theme(params.get("theme"))
    if kind == "lower_third":
        key = f"lt|{params['title']}|{params.get('subtitle','')}|{params.get('align','left')}"
    else:
        validate(kind, params)
        amount = params.get("amount") if params.get("amount") is not None else AMOUNT[kind][3]
        key = f"{kind}|{amount}"
    key += f"|{th.name}|{th.accent}"
    out = os.path.join(cache_dir, f"gfx_{hashlib.sha1(f'v2|{key}|{W}|{H}'.encode()).hexdigest()[:16]}.png")
    if os.path.exists(out):
        return out
    if kind == "lower_third":
        img = lower_third(W, H, params["title"], params.get("subtitle", ""), params.get("align", "left"), strict=False, theme=th)
    elif kind == "frame" and th.shape != "glass":
        import themed
        img = themed.frame(W, H, amount, th)
    elif kind == "letterbox" and th.shape != "glass":
        img = letterbox(W, H, amount, themes.rgb(th.accent))
    else:
        img = {"frame": frame, "letterbox": letterbox, "vignette": vignette}[kind](W, H, amount)
    os.makedirs(cache_dir, exist_ok=True)
    tmp = out + f".{os.getpid()}.tmp"
    img.save(tmp, format="PNG")
    os.replace(tmp, out)
    return out


# ---------------------------------------------------------------- callout: a label pinned to a point of the picture
CALLOUT_TITLE_MAX, CALLOUT_SUB_MAX = 40, 60
CALLOUT_SIDES = ("auto", "ne", "nw", "se", "sw")      # where the flag sits relative to the pinned point


def _callout_glass(W, H, title, subtitle="", side="ne", strict=True):
    """A gold ring on the exact point + a thin staff + a glass flag with the name. Returns (RGBA image, ax, ay):
    the image is SMALL (not frame-sized) and (ax, ay) is where the ring centre is inside it, so the editor can place
    the image with its ring on any x,y of the frame. side: ne/nw/se/sw = flag up-right, up-left, down-right, down-left."""
    if side not in CALLOUT_SIDES[1:]:
        raise ValueError(f"callout side must be one of {CALLOUT_SIDES[1:]} (or 'auto' in the editor)")
    title = T.clean(title, "luxury")
    subtitle = T.clean(subtitle, "modern") if subtitle else ""
    if "\n" in title or "\n" in subtitle:
        raise ValueError("callout title and subtitle must be single lines")
    if not title or len(title) > CALLOUT_TITLE_MAX or len(subtitle) > CALLOUT_SUB_MAX:
        raise ValueError(f"callout: title 1-{CALLOUT_TITLE_MAX} characters, subtitle max {CALLOUT_SUB_MAX}")
    S = SS * 2
    h_ = H * S
    max_w = 0.30 * W * S
    ft = _fit(title, "luxury", 0.030 * h_, max_w)
    fs = _fit(subtitle, "modern", 0.0135 * h_, max_w, 0.16) if subtitle else None
    if ft is None or (subtitle and fs is None):
        if strict:
            raise ValueError("callout text is too long to fit; shorten the title or subtitle")
        ft = ft or (T.make_font("luxury", 0.030 * h_ * T.MIN_SHRINK), 0)
        fs = fs or (T.make_font("modern", 0.0135 * h_ * T.MIN_SHRINK), 0)
    (tf, ttr), sub = ft, (fs if subtitle else None)
    sub_txt = subtitle.upper()
    tw = T.text_width(title, tf, ttr)
    sw = T.text_width(sub_txt, sub[0], sub[1]) if sub else 0
    th, sh = sum(tf.getmetrics()), (sum(sub[0].getmetrics()) if sub else 0)
    pad = 0.011 * h_
    gapv = 0.006 * h_ if sub else 0
    ph, pw = th + gapv + sh + 2 * pad, max(tw, sw) + 2 * pad
    R, dot = 0.0085 * h_, 0.0028 * h_                 # ring radius, centre dot
    stem = 0.035 * h_                                  # staff beyond the flag
    m = 0.004 * h_
    lw = max(2, int(round(0.0016 * h_)))
    right, up = side[1] == "e", side[0] == "n"
    iw = int(R + m + pw + m)
    ih = int(ph + stem + 2 * R + 2 * m)
    ax = (R + m) if right else (iw - R - m)
    ay = (ih - R - m) if up else (R + m)
    img = Image.new("RGBA", (iw, ih), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    gold = GOLD + (255,)
    fx0 = ax if right else ax - pw                     # flag rectangle, attached to the staff
    fy0 = 0 if up else ih - ph
    d.rectangle([ax - lw / 2, (0 if up else ay + R), ax + lw / 2, (ay - R if up else ih)], fill=gold)      # staff
    d.rounded_rectangle([fx0, fy0, fx0 + pw, fy0 + ph], radius=0.006 * h_, fill=(8, 8, 11, 175), outline=GOLD + (120,), width=max(1, lw // 2))
    d.ellipse([ax - R - lw, ay - R - lw, ax + R + lw, ay + R + lw], outline=(0, 0, 0, 120), width=lw)   # dark halo: legible on bright ground
    d.ellipse([ax - R, ay - R, ax + R, ay + R], outline=gold, width=lw)
    d.ellipse([ax - dot, ay - dot, ax + dot, ay + dot], fill=gold)
    tx = fx0 + pad
    mask = Image.new("L", (iw, ih), 0)
    T.draw_tracked(ImageDraw.Draw(mask), tx, fy0 + pad, title, tf, ttr, 255)
    grad = Image.new("RGBA", (iw, ih), (0, 0, 0, 0)); grad.paste(T.gradient(iw, int(th), T.GOLD), (0, int(fy0 + pad)))
    grad.putalpha(mask)
    img = Image.alpha_composite(img, grad)
    if sub:
        sm = Image.new("L", (iw, ih), 0)
        T.draw_tracked(ImageDraw.Draw(sm), tx, fy0 + pad + th + gapv, sub_txt, sub[0], sub[1], 255)
        ivory = Image.new("RGBA", (iw, ih), (246, 236, 214, 255)); ivory.putalpha(sm)
        img = Image.alpha_composite(img, ivory)
    ow, oh = max(2, int(round(iw / S))), max(2, int(round(ih / S)))
    img = img.resize((ow, oh), Image.LANCZOS)
    return img, ax / S, ay / S


def render_callout(W, H, title, subtitle, side, cache_dir, theme=None):
    """Cached PNG of a callout + its anchor. Returns (path, w, h, ax, ay)."""
    import json
    th = _theme(theme)
    key = f"v2|{title}|{subtitle}|{side}|{W}|{H}|{th.name}|{th.accent}"
    base = os.path.join(cache_dir, f"callout_{hashlib.sha1(key.encode()).hexdigest()[:16]}")
    try:
        with open(base + ".json") as f:
            meta = json.load(f)
        if os.path.exists(base + ".png"):
            return (base + ".png", *meta)
    except (OSError, ValueError):
        pass
    img, ax, ay = callout(W, H, title, subtitle, side, strict=False, theme=th)
    os.makedirs(cache_dir, exist_ok=True)
    tmp = base + f".{os.getpid()}.tmp"
    img.save(tmp, format="PNG")
    os.replace(tmp, base + ".png")
    meta = [img.width, img.height, ax, ay]
    tmp = base + f".{os.getpid()}.json.tmp"
    with open(tmp, "w") as f:
        json.dump(meta, f)
    os.replace(tmp, base + ".json")
    return (base + ".png", *meta)
