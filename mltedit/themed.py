"""Template-specific overlays: lower third, callout and frame for every theme whose shape language is not the original 'glass'
(corporate/minimal = flat, academic = rule, sketch, tech = neon, playful = bubble). graphics.py keeps the luxury code untouched and
delegates here when the project's theme is something else.

Each overlay is drawn on a small supersampled canvas (never a full 4K one) and shrunk, so it costs little and the strokes stay smooth.
Geometry contracts match the luxury versions: the callout returns (image, ax, ay) with (ax, ay) = where the ring is inside the image,
so live.py places any theme's callout the same way."""

from PIL import Image, ImageDraw, ImageFilter

from .render import sketch
from .render import text as T
from . import themes

S = 3                                   # supersampling of the local canvases


def _c(hex_, a=255):
    return themes.rgb(hex_) + (a,)


def _blur(layer, radius):
    return layer.filter(ImageFilter.GaussianBlur(max(0.5, radius)))


def _text_layer(size, xy, text, style, font, track, color=None, effects=True):
    """One line of text, top-left at xy, painted like textrender paints `style` (glow, sticker, outline, shadow, fill).
    `color` overrides the fill and, with effects=False, gives plain ink text for dark-on-paper panels."""
    st = T.STYLES[style]
    mask = Image.new("L", size, 0)
    T.draw_tracked(T._pd(mask, st), xy[0], xy[1], text, font, track, 255)
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    px = font.size

    def tint(m, col):                                            # col: "#rrggbb" or an RGBA tuple
        t = Image.new("RGBA", size, col if isinstance(col, tuple) else _c(col)); t.putalpha(m); return t
    if effects:
        if st.get("glow"):
            gc, gb, ga = st["glow"]
            out = Image.alpha_composite(out, tint(_blur(mask, gb * px).point(lambda v: min(255, int(v * 2.2 * ga))), gc))
        if st.get("sticker"):
            sx, sy, sc = st["sticker"]
            ow = st.get("outline", (None, 0))[1]
            sm = Image.new("L", size, 0)
            T.draw_tracked(T._pd(sm, st), xy[0] + sx * px, xy[1] + sy * px, text, font, track, 255,
                           stroke_width=max(1, int(round(ow * px))) if ow else 0, stroke_fill=255)
            out = Image.alpha_composite(out, tint(sm, sc))
        if st.get("outline"):
            oc, ow = st["outline"]
            om = Image.new("L", size, 0)
            T.draw_tracked(T._pd(om, st), xy[0], xy[1], text, font, track, 255, stroke_width=max(1, int(round(ow * px))), stroke_fill=255)
            out = Image.alpha_composite(out, tint(om, oc))
        if st.get("shadow"):
            dx, dy, blur, alpha = st["shadow"]
            sh = Image.new("L", size, 0); sh.paste(mask, (int(round(dx * px)), int(round(dy * px))))
            out = Image.alpha_composite(out, tint(_blur(sh, blur * px).point(lambda v: int(v * alpha)), "#000000"))
    fill = color or (st["fill"] if isinstance(st["fill"], str) else "#ffffff")
    return Image.alpha_composite(out, tint(mask, fill))


def _panel(img, rect, th, u, key):
    """Paint the theme's panel (a lower-third card / a callout flag) into `img` at rect. Returns (title colour, sub colour, effects):
    dark-on-paper panels give explicit ink colours and no text effects; dark/colourful panels let the style's own look (glow, outline) work."""
    x0, y0, x1, y1 = rect
    d = ImageDraw.Draw(img)
    shape, acc = th.shape, _c(th.accent)
    r = max(2, th.radius * u)
    if shape == "flat" and th.name != "minimal":                 # corporate: white card, soft shadow, accent bar on the left
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([x0, y0 + 0.006 * u, x1, y1 + 0.006 * u], radius=r, fill=(0, 0, 0, 115))
        img.alpha_composite(_blur(sh, 0.009 * u))
        d.rounded_rectangle(rect, radius=r, fill=acc)
        d.rounded_rectangle([x0 + 0.0075 * u, y0, x1, y1], radius=r, fill=_c(th.paper, 246), corners=(False, True, True, False))
        if th.stroke:
            d.rounded_rectangle([x0 + 0.0075 * u, y0, x1, y1], radius=r, outline=_c(th.accent2), width=max(1, int(th.stroke * u)), corners=(False, True, True, False))
        return _c(th.ink), _c(th.muted), False
    if shape == "flat":                                          # minimal: no card; a short accent hairline above the text (caller draws it)
        return None, None, True
    if shape == "rule":                                          # academic: paper card between two thin rules
        d.rounded_rectangle(rect, radius=r, fill=_c(th.paper, 240))
        rw = max(2, int(0.0025 * u)); ins = 0.009 * u
        d.rectangle([x0 + ins, y0 + ins, x1 - ins, y0 + ins + rw], fill=acc)
        d.rectangle([x0 + ins, y1 - ins - rw, x1 - ins, y1 - ins], fill=acc)
        return _c(th.ink), _c(th.muted), False
    if shape == "sketch":                                        # a hand-drawn paper note: offset shadow stroke, filled wobbly rect
        rg = sketch.rng(key, "panel")
        sk = max(3, 0.0035 * u)
        sketch.rect(d, x0 + 0.006 * u, y0 + 0.007 * u, x1 + 0.006 * u, y1 + 0.007 * u, (0, 0, 0, 70), sk * 1.3, rg, double=False)
        sketch.rect(d, x0, y0, x1, y1, _c(th.ink), sk, rg, fill=_c(th.paper, 248))
        return _c(th.ink), _c(th.muted), False
    if shape == "neon":                                          # dark glass with a glowing cyan border and two magenta corner brackets
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(glow).rounded_rectangle(rect, radius=r, outline=acc, width=max(3, int(0.004 * u)))
        img.alpha_composite(_blur(glow, 0.011 * u))
        d.rounded_rectangle(rect, radius=r, fill=(10, 15, 28, 232), outline=acc, width=max(2, int(0.002 * u)))
        L, w, m2 = 0.03 * u, max(3, int(0.0032 * u)), _c(th.accent2)
        for cx, cy, sx, sy in ((x0, y0, 1, 1), (x1, y1, -1, -1)):
            d.line([(cx, cy + sy * L), (cx, cy), (cx + sx * L, cy)], fill=m2, width=w, joint="curve")
        return None, None, True
    if shape == "bubble":                                        # playful: coloured bubble, thick ink outline, hard sticker shadow
        ink, ow = _c(th.ink), max(3, int(0.004 * u))
        d.rounded_rectangle([x0 + 0.008 * u, y0 + 0.01 * u, x1 + 0.008 * u, y1 + 0.01 * u], radius=r, fill=ink)
        d.rounded_rectangle(rect, radius=r, fill=_c(th.accent2), outline=ink, width=ow)
        return None, None, True
    if shape == "brutal":                                        # neobrutalism: yellow block, thick ink border, hard square offset shadow
        ink, ow, off = _c(th.ink), max(3, int(th.stroke * u)), 0.010 * u
        d.rectangle([x0 + off, y0 + off, x1 + off, y1 + off], fill=ink)
        d.rectangle(rect, fill=acc, outline=ink, width=ow)
        return _c(th.ink), _c(th.ink), False
    if shape == "cinema":                                        # no panel: a slim accent bar; the type carries it
        d.rectangle([x0, y0, x0 + max(3, int(0.0055 * u)), y1], fill=acc)
        return None, None, True
    if shape == "term":                                          # terminal: black square panel with a green line
        d.rectangle(rect, fill=_c(th.paper, 238), outline=acc, width=max(2, int(th.stroke * u)))
        return None, None, True
    if shape == "pixel":                                         # arcade: black block, double-thickness yellow border, blue block shadow
        b = max(3, int(0.0055 * u))
        d.rectangle([x0 + 1.5 * b, y0 + 1.5 * b, x1 + 1.5 * b, y1 + 1.5 * b], fill=_c(th.accent2))
        d.rectangle(rect, fill=_c(th.paper), outline=acc, width=2 * b)
        return None, None, True
    if shape == "riso":                                          # two inks: a pink block misregistered under a paper card with a blue outline
        off = 0.007 * u
        d.rectangle([x0 + off, y0 + off, x1 + off, y1 + off], fill=_c(th.accent, 235))
        d.rectangle(rect, fill=_c(th.paper, 248), outline=_c(th.accent2), width=max(2, int(th.stroke * u)))
        return None, None, True
    if shape == "frost":                                         # tinted frosted glass: soft shadow, translucent fill, luminous border and top highlight
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([x0, y0 + 0.008 * u, x1, y1 + 0.008 * u], radius=r, fill=(0, 0, 40, 95))
        img.alpha_composite(_blur(sh, 0.012 * u))
        lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(lay)
        ld.rounded_rectangle(rect, radius=r, fill=_c(th.paper, 150), outline=(255, 255, 255, 150), width=max(2, int(th.stroke * u)))
        ld.line([(x0 + r, y0 + 2), (x1 - r, y0 + 2)], fill=(255, 255, 255, 170), width=max(1, int(0.0012 * u)))
        img.alpha_composite(lay)
        return None, None, True
    raise ValueError(f"no panel painter for shape '{shape}'")


# ------------------------------------------------------------------------------------------------ lower third
def lower_third(W, H, title, subtitle, align, strict, th):
    from .graphics import _fit
    title = T.clean(title, th.title_style)
    subtitle = T.clean(subtitle, th.caption_style) if subtitle else ""
    if "\n" in title or "\n" in subtitle:
        raise ValueError("lower third title and subtitle must be single lines")
    if len(title) > 60 or len(subtitle) > 80:
        raise ValueError("lower third: title max 60 characters, subtitle max 80")
    u = H * S
    max_w = 0.78 * W * S
    ft = _fit(title, th.title_style, 0.046 * u, max_w)
    fs = _fit(subtitle, th.caption_style, 0.0185 * u, max_w) if subtitle else None
    if ft is None or (subtitle and fs is None):
        if strict:
            raise ValueError("lower third text is too long to fit; shorten the title or subtitle")
        ft = ft or (T.make_font(th.title_style, 0.046 * u * T.MIN_SHRINK), 0)
        fs = fs or (T.make_font(th.caption_style, 0.0185 * u * T.MIN_SHRINK), 0)
    (tf, ttr), sub = ft, (fs if subtitle else None)
    sub_txt = subtitle.upper() if T.STYLES[th.caption_style]["upper"] else subtitle
    tw = T.text_width(title.upper() if T.STYLES[th.title_style]["upper"] else title, tf, ttr)
    sw = T.text_width(sub_txt, sub[0], sub[1]) if sub else 0
    thh, shh = sum(tf.getmetrics()), (sum(sub[0].getmetrics()) if sub else 0)
    pad, gapv = 0.022 * u, (0.012 * u if sub else 0)
    bar = 0.0075 * u if (th.shape == "flat" and th.name != "minimal") else 0
    ph, pw = thh + gapv + shh + 2 * pad, min(0.92 * W * S, max(tw, sw) + 2 * pad + 0.016 * W * S + bar)
    margin = 0.06 * W * S
    x0 = margin if align == "left" else W * S - margin - pw
    y1 = H * S - 0.09 * u
    y0 = y1 - ph
    m = 0.05 * u                                                  # room around the panel for shadows, glow, overshoot
    ox, oy = int((x0 - m) // S), int((y0 - m) // S)
    cw, ch = int(pw + 2 * m) + S * 2, int(ph + 2 * m) + S * 2
    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    rx0, ry0 = x0 - ox * S, y0 - oy * S
    tcol, scol, eff = _panel(canvas, (rx0, ry0, rx0 + pw, ry0 + ph), th, u, f"lt|{title}|{subtitle}")
    tx, ty = rx0 + pad + bar, ry0 + pad
    if th.shape == "sketch":                                      # marker highlighter under the title
        hl = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        sketch.highlighter(hl, tx - 0.004 * u, ty + thh * 0.52, tx + tw + 0.006 * u, ty + thh * 0.96, _c("#ffe45c", 165), sketch.rng(title, "hl"))
        canvas = Image.alpha_composite(canvas, hl)
    if th.name == "minimal":                                      # short accent hairline above the text
        ImageDraw.Draw(canvas).rectangle([tx, ty - 0.012 * u, tx + min(tw, 0.12 * W * S), ty - 0.012 * u + max(2, 0.0035 * u)], fill=_c(th.accent))
    canvas = Image.alpha_composite(canvas, _text_layer(canvas.size, (tx, ty), title.upper() if T.STYLES[th.title_style]["upper"] else title,
                                                       th.title_style, tf, ttr, tcol, eff))
    if sub:
        canvas = Image.alpha_composite(canvas, _text_layer(canvas.size, (tx, ty + thh + gapv), sub_txt, th.caption_style, sub[0], sub[1],
                                                           scol or (_c("#e8e8e8") if th.name == "minimal" else None), eff))
    canvas = canvas.resize((cw // S, ch // S), Image.LANCZOS)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if th.name in ("minimal", "cinema"):                          # a soft scrim so white text reads on bright footage
        sc = Image.linear_gradient("L").resize((W, int(0.3 * H))).point(lambda v: int(v * 0.52))
        scrim = Image.new("RGBA", (W, int(0.3 * H)), (0, 0, 0, 255)); scrim.putalpha(sc)
        img.paste(scrim, (0, H - int(0.3 * H)))
    img.alpha_composite(canvas, (max(0, ox), max(0, oy)), (max(0, -ox), max(0, -oy))) if ox < 0 or oy < 0 else img.alpha_composite(canvas, (ox, oy))
    return img


# ------------------------------------------------------------------------------------------------ callout
def callout(W, H, title, subtitle, side, strict, th):
    from .graphics import CALLOUT_SIDES, CALLOUT_SUB_MAX, CALLOUT_TITLE_MAX, _fit
    if side not in CALLOUT_SIDES[1:]:
        raise ValueError(f"callout side must be one of {CALLOUT_SIDES[1:]} (or 'auto' in the editor)")
    title = T.clean(title, th.title_style)
    subtitle = T.clean(subtitle, th.caption_style) if subtitle else ""
    if "\n" in title or "\n" in subtitle:
        raise ValueError("callout title and subtitle must be single lines")
    if not title or len(title) > CALLOUT_TITLE_MAX or len(subtitle) > CALLOUT_SUB_MAX:
        raise ValueError(f"callout: title 1-{CALLOUT_TITLE_MAX} characters, subtitle max {CALLOUT_SUB_MAX}")
    u = H * S
    max_w = 0.30 * W * S
    ft = _fit(title, th.title_style, 0.030 * u, max_w)
    fs = _fit(subtitle, th.caption_style, 0.0135 * u, max_w) if subtitle else None
    if ft is None or (subtitle and fs is None):
        if strict:
            raise ValueError("callout text is too long to fit; shorten the title or subtitle")
        ft = ft or (T.make_font(th.title_style, 0.030 * u * T.MIN_SHRINK), 0)
        fs = fs or (T.make_font(th.caption_style, 0.0135 * u * T.MIN_SHRINK), 0)
    (tf, ttr), sub = ft, (fs if subtitle else None)
    up_t, up_s = T.STYLES[th.title_style]["upper"], T.STYLES[th.caption_style]["upper"]
    ttxt = title.upper() if up_t else title
    stxt = subtitle.upper() if (up_s and subtitle) else subtitle
    if th.shape == "neon":
        ttxt = f"[ {ttxt} ]"
    elif th.shape == "term":
        ttxt = f"> {ttxt}"
    tw = T.text_width(ttxt, tf, ttr)
    sw = T.text_width(stxt, sub[0], sub[1]) if sub else 0
    thh, shh = sum(tf.getmetrics()), (sum(sub[0].getmetrics()) if sub else 0)
    pad, gapv = 0.011 * u, (0.006 * u if sub else 0)
    R, stem, m = 0.0085 * u, 0.035 * u, 0.012 * u
    ph, pw = thh + gapv + shh + 2 * pad, max(tw, sw) + 2 * pad
    right, up = side[1] == "e", side[0] == "n"
    iw, ih = int(R + m + pw + m), int(ph + stem + 2 * R + 2 * m)
    ax = (R + m) if right else (iw - R - m)
    ay = (ih - R - m) if up else (R + m)
    canvas = Image.new("RGBA", (iw, ih), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    fx0 = ax if right else ax - pw
    fy0 = m if up else ih - ph - m
    shape, acc, ink, key = th.shape, _c(th.accent), _c(th.ink), f"co|{title}|{subtitle}|{side}"
    lw = max(2, int(0.002 * u))
    st_top, st_bot = (fy0 + ph, ay - R) if up else (ay + R, fy0)          # the staff joins the ring to the flag's near edge
    if shape == "sketch":
        rg = sketch.rng(key, "staff")
        sketch.line(d, (ax, min(fy0 + ph * (0.0 if up else 1.0), ay)), (ax, ay + (-R if up else R)), ink, 0.0035 * u, rg, double=False)
    elif shape == "neon":
        g = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(g).line([(ax, fy0 + (0 if up else ph)), (ax, ay)], fill=acc, width=max(3, int(0.004 * u)))
        canvas.alpha_composite(_blur(g, 0.008 * u))
        d.line([(ax, fy0 + (0 if up else ph)), (ax, ay)], fill=acc, width=lw)
    elif shape == "bubble":
        d.line([(ax, fy0 + (0 if up else ph)), (ax, ay)], fill=ink, width=max(4, int(0.0045 * u)))
    elif shape in ("brutal", "pixel", "cinema", "frost"):
        col = {"brutal": ink, "pixel": acc, "cinema": acc, "frost": (255, 255, 255, 210)}[shape]
        d.line([(ax, fy0 + (0 if up else ph)), (ax, ay)], fill=col, width=max(3, int(0.0045 * u)) if shape in ("brutal", "pixel") else lw + 1)
    elif shape == "term":                                         # dashed staff
        y_a, y_b = sorted((fy0 + (0 if up else ph), ay))
        seg = max(4, int(0.008 * u))
        for yy in range(int(y_a), int(y_b), seg * 2):
            d.line([(ax, yy), (ax, min(yy + seg, y_b))], fill=acc, width=lw)
    elif shape == "riso":
        off = 0.004 * u
        d.line([(ax + off, fy0 + (0 if up else ph)), (ax + off, ay)], fill=_c(th.accent, 235), width=max(3, int(0.004 * u)))
        d.line([(ax, fy0 + (0 if up else ph)), (ax, ay)], fill=_c(th.accent2), width=lw)
    elif th.name == "minimal":
        d.line([(ax, fy0 + (0 if up else ph)), (ax, ay)], fill=(0, 0, 0, 120), width=int(0.0045 * u))       # dark halo so the white hairline reads on bright ground
        d.line([(ax, fy0 + (0 if up else ph)), (ax, ay)], fill=_c("#ffffff"), width=int(0.0026 * u))
    else:
        d.line([(ax, fy0 + (0 if up else ph)), (ax, ay)], fill=acc, width=lw if shape != "rule" else max(1, lw - 1))
    # marker on the pinned point
    if shape == "sketch":
        sketch.circle(d, ax, ay, R * 2.1, _c(th.accent), 0.0055 * u, sketch.rng(key, "ring"))
    elif shape == "neon":
        g = Image.new("RGBA", canvas.size, (0, 0, 0, 0)); gd = ImageDraw.Draw(g)
        gd.ellipse([ax - R, ay - R, ax + R, ay + R], outline=acc, width=max(3, int(0.004 * u)))
        canvas.alpha_composite(_blur(g, 0.007 * u))
        d.ellipse([ax - R, ay - R, ax + R, ay + R], outline=acc, width=lw)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            d.line([(ax + dx * R * 1.15, ay + dy * R * 1.15), (ax + dx * R * 1.7, ay + dy * R * 1.7)], fill=acc, width=lw)
        d.ellipse([ax - R * 0.25, ay - R * 0.25, ax + R * 0.25, ay + R * 0.25], fill=_c(th.accent2))
    elif shape == "bubble":
        d.ellipse([ax - R, ay - R, ax + R, ay + R], fill=acc, outline=ink, width=max(3, int(0.004 * u)))
    elif shape == "rule":
        d.ellipse([ax - R, ay - R, ax + R, ay + R], outline=acc, width=lw)
        d.ellipse([ax - R * 0.3, ay - R * 0.3, ax + R * 0.3, ay + R * 0.3], fill=acc)
    elif shape == "brutal":
        d.rectangle([ax - R, ay - R, ax + R, ay + R], fill=acc, outline=ink, width=max(3, int(0.004 * u)))
    elif shape == "pixel":
        b = max(3, int(0.0055 * u))
        d.rectangle([ax - R, ay - R, ax + R, ay + R], fill=_c(th.paper), outline=acc, width=b)
        d.rectangle([ax - b, ay - b, ax + b, ay + b], fill=_c(th.accent2))
    elif shape == "term":                                         # square brackets around the point
        L2 = R * 1.5
        for sx_, sy_ in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            d.line([(ax + sx_ * L2, ay + sy_ * (L2 - R * 0.9)), (ax + sx_ * L2, ay + sy_ * L2), (ax + sx_ * (L2 - R * 0.9), ay + sy_ * L2)], fill=acc, width=lw + 1)
        d.rectangle([ax - R * 0.28, ay - R * 0.28, ax + R * 0.28, ay + R * 0.28], fill=_c(th.accent2))
    elif shape == "riso":
        off = 0.004 * u
        d.ellipse([ax - R + off, ay - R + off, ax + R + off, ay + R + off], fill=_c(th.accent, 235))
        d.ellipse([ax - R, ay - R, ax + R, ay + R], outline=_c(th.accent2), width=max(2, int(0.003 * u)))
    elif shape == "frost":
        g = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(g).ellipse([ax - R, ay - R, ax + R, ay + R], outline=acc, width=max(3, int(0.004 * u)))
        canvas.alpha_composite(_blur(g, 0.006 * u))
        d.ellipse([ax - R, ay - R, ax + R, ay + R], fill=(255, 255, 255, 70), outline=(255, 255, 255, 235), width=lw + 1)
    elif shape == "cinema":
        d.ellipse([ax - R, ay - R, ax + R, ay + R], outline=acc, width=lw + 1)
        d.ellipse([ax - R * 0.3, ay - R * 0.3, ax + R * 0.3, ay + R * 0.3], fill=_c("#ffffff"))
    elif th.name == "minimal":
        d.ellipse([ax - R * 0.95, ay - R * 0.95, ax + R * 0.95, ay + R * 0.95], fill=(0, 0, 0, 110))
        d.ellipse([ax - R * 0.75, ay - R * 0.75, ax + R * 0.75, ay + R * 0.75], fill=acc, outline=_c("#ffffff"), width=max(2, int(0.003 * u)))
    else:                                                          # corporate: accent dot with a white ring and a soft shadow
        sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).ellipse([ax - R, ay - R + 0.004 * u, ax + R, ay + R + 0.004 * u], fill=(0, 0, 0, 120))
        canvas.alpha_composite(_blur(sh, 0.005 * u))
        d.ellipse([ax - R, ay - R, ax + R, ay + R], fill=_c("#ffffff"))
        d.ellipse([ax - R * 0.62, ay - R * 0.62, ax + R * 0.62, ay + R * 0.62], fill=acc)
    # flag
    if th.name in ("minimal", "cinema"):                           # no card in the template, but a faint dark plate keeps white text legible
        d.rounded_rectangle([fx0, fy0, fx0 + pw, fy0 + ph], radius=0.004 * u, fill=(0, 0, 0, 128))
        tcol, scol, eff = None, None, True
    else:
        tcol, scol, eff = _panel(canvas, (fx0, fy0, fx0 + pw, fy0 + ph), th, u, key)
    tx, ty = fx0 + pad, fy0 + pad
    canvas = Image.alpha_composite(canvas, _text_layer(canvas.size, (tx, ty), ttxt, th.title_style, tf, ttr, tcol, eff))
    if sub:
        canvas = Image.alpha_composite(canvas, _text_layer(canvas.size, (tx, ty + thh + gapv), stxt, th.caption_style, sub[0], sub[1],
                                                           scol or (_c("#e8e8e8") if th.name == "minimal" else None), eff))
    ow, oh = max(2, int(round(iw / S))), max(2, int(round(ih / S)))
    return canvas.resize((ow, oh), Image.LANCZOS), ax / S, ay / S


# ------------------------------------------------------------------------------------------------ frame
def frame(W, H, amount, th):
    """Frame decoration in the theme's language. Drawn full-size at 2x supersampling like the luxury frame."""
    ss = 2
    w, h = W * ss, H * ss
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    mn = min(w, h)
    ins = amount * w
    x0, y0, x1, y1 = ins, ins, w - ins, h - ins
    acc = _c(th.accent)
    if th.shape == "sketch":
        rg = sketch.rng("frame", W, H)
        sk = 0.0042 * h
        sketch.rect(d, x0 + sk * 0.7, y0 + sk * 0.7, x1 + sk * 0.7, y1 + sk * 0.7, (0, 0, 0, 110), sk * 1.1, rg, double=False)   # dark under-stroke
        sketch.rect(d, x0, y0, x1, y1, (255, 255, 255, 240), sk, rg)
        for cx, cy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            sketch.star(d, cx, cy, 0.016 * h, _c(th.accent2), sk * 0.8, rg)
    elif th.shape == "neon":
        L, t = 0.075 * h, max(3, int(0.003 * h))
        g = Image.new("RGBA", (w, h), (0, 0, 0, 0)); gd = ImageDraw.Draw(g)
        for cx, cy, sx, sy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
            for dr in (d, gd):
                dr.line([(cx, cy + sy * L), (cx, cy), (cx + sx * L, cy)], fill=acc, width=t * (2 if dr is gd else 1), joint="curve")
            d.ellipse([cx + sx * 0.012 * h - 0.004 * h, cy + sy * 0.012 * h - 0.004 * h, cx + sx * 0.012 * h + 0.004 * h, cy + sy * 0.012 * h + 0.004 * h], fill=_c(th.accent2))
        for cx, cy, dx, dy in ((w / 2, y0, 1, 0), (w / 2, y1, 1, 0), (x0, h / 2, 0, 1), (x1, h / 2, 0, 1)):
            gd.line([(cx - dx * L * 0.35, cy - dy * L * 0.35), (cx + dx * L * 0.35, cy + dy * L * 0.35)], fill=acc, width=t * 2)
            d.line([(cx - dx * L * 0.35, cy - dy * L * 0.35), (cx + dx * L * 0.35, cy + dy * L * 0.35)], fill=acc, width=t)
        img = Image.alpha_composite(_blur(g, 0.006 * h), img)
    elif th.shape == "bubble":
        ink = _c(th.ink)
        r = 0.05 * mn
        d.rounded_rectangle([x0, y0, x1, y1], radius=r, outline=ink, width=int(0.011 * h))
        d.rounded_rectangle([x0 + 0.0085 * h, y0 + 0.0085 * h, x1 - 0.0085 * h, y1 - 0.0085 * h], radius=r * 0.85, outline=(255, 255, 255, 255), width=int(0.006 * h))
        for (cx, cy), col in zip(((x0, y0), (x1, y0), (x1, y1), (x0, y1)), (th.accent, "#ffd93d", "#6bcb77", th.accent2)):
            rr = 0.016 * h
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=_c(col), outline=ink, width=int(0.004 * h))
    elif th.shape == "rule":
        cream = _c(th.paper, 238)
        d.rectangle([x0, y0, x1, y1], outline=cream, width=max(2, int(0.0026 * mn)))
        g_ = 0.008 * mn
        d.rectangle([x0 + g_, y0 + g_, x1 - g_, y1 - g_], outline=cream[:3] + (170,), width=max(1, int(0.0013 * mn)))
        sq = 0.006 * mn
        for cx, cy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            d.rectangle([cx - sq, cy - sq, cx + sq, cy + sq], fill=cream)
    elif th.shape == "brutal":                                     # thick ink border with a hard offset copy in the second colour
        ink, wd, off = _c(th.ink), max(4, int(0.011 * h)), 0.012 * h
        d.rectangle([x0 + off, y0 + off, x1 + off, y1 + off], outline=acc, width=wd)
        d.rectangle([x0, y0, x1, y1], outline=ink, width=wd)
    elif th.shape == "cinema":                                     # corner ticks and a hairline
        L, t = 0.05 * h, max(2, int(0.0028 * h))
        d.rectangle([x0, y0, x1, y1], outline=(255, 255, 255, 70), width=max(1, int(0.001 * h)))
        for cx, cy, sx, sy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
            d.line([(cx, cy + sy * L), (cx, cy), (cx + sx * L, cy)], fill=(255, 255, 255, 235), width=t)
    elif th.shape == "term":                                       # green corner brackets plus tick marks, no glow
        L, t = 0.06 * h, max(2, int(0.0028 * h))
        for cx, cy, sx, sy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
            d.line([(cx, cy + sy * L), (cx, cy), (cx + sx * L, cy)], fill=acc, width=t)
        for k in range(1, 8):
            tick = 0.012 * h * (2 if k == 4 else 1)
            d.line([(x0 + (x1 - x0) * k / 8, y0), (x0 + (x1 - x0) * k / 8, y0 + tick)], fill=acc, width=max(1, t // 2))
    elif th.shape == "pixel":                                      # a dotted border of square blocks
        b = max(4, int(0.011 * h))
        for xx in range(int(x0), int(x1) - b, b * 2):
            d.rectangle([xx, y0, xx + b, y0 + b], fill=acc); d.rectangle([xx, y1 - b, xx + b, y1], fill=acc)
        for yy in range(int(y0), int(y1) - b, b * 2):
            d.rectangle([x0, yy, x0 + b, yy + b], fill=acc); d.rectangle([x1 - b, yy, x1, yy + b], fill=acc)
        for cx, cy in ((x0, y0), (x1 - b, y0), (x0, y1 - b), (x1 - b, y1 - b)):
            d.rectangle([cx, cy, cx + b, cy + b], fill=_c(th.accent2))
    elif th.shape == "riso":                                       # two misregistered ink outlines
        wd, off = max(3, int(0.004 * h)), 0.006 * h
        d.rectangle([x0 + off, y0 + off, x1 + off, y1 + off], outline=_c(th.accent, 225), width=wd)
        d.rectangle([x0, y0, x1, y1], outline=_c(th.accent2, 235), width=max(2, wd // 2))
    elif th.shape == "frost":                                      # luminous rounded border
        r = 0.04 * mn
        g = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(g).rounded_rectangle([x0, y0, x1, y1], radius=r, outline=acc, width=max(4, int(0.005 * h)))
        img = Image.alpha_composite(_blur(g, 0.006 * h), img)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([x0, y0, x1, y1], radius=r, outline=(255, 255, 255, 190), width=max(2, int(0.0022 * h)))
    elif th.name == "minimal":
        d.rectangle([x0, y0, x1, y1], outline=(255, 255, 255, 215), width=max(1, int(0.0012 * mn)))
    else:                                                          # corporate: four accent corner brackets
        L, t = 0.07 * h, max(3, int(0.0035 * h))
        for cx, cy, sx, sy in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
            d.line([(cx, cy + sy * L), (cx, cy), (cx + sx * L, cy)], fill=acc, width=t, joint="curve")
    return img.resize((W, H), Image.LANCZOS)
