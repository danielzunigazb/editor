"""The shape language of a template: how its panels, labels (callouts), frames, icon plates and card tiles are drawn.

A theme names a shape (theme.json -> shape.name); the shape is a plugin (plugins/shapes/*.py) that subclasses `Shape` and overrides only what
differs from the defaults below. The generic drivers here (lower_third, callout, frame) lay out the text and the geometry once for every shape
and call the shape's hooks for the parts that look different. Per-template choices that are not about drawing (a soft scrim under the lower
third, a dark plate behind callout text) are theme options read from the Theme, never names checked in code.

Each overlay is drawn on a small supersampled canvas (never a full 4K one) and shrunk, so it costs little and the strokes stay smooth.
Geometry contract: callout() returns (image, ax, ay) with (ax, ay) = where the ring is inside the image, so the engine places any shape's
callout the same way."""
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFilter

from . import registry, themes
from .render import text as T

SS = 3                                   # supersampling of the local canvases
CALLOUT_TITLE_MAX, CALLOUT_SUB_MAX = 40, 60
CALLOUT_SIDES = ("auto", "ne", "nw", "se", "sw")      # where the flag sits relative to the pinned point


def _c(hex_, a=255):
    return themes.rgb(hex_) + (a,)


def _blur(layer, radius):
    return layer.filter(ImageFilter.GaussianBlur(max(0.5, radius)))


def fit(line, style, px, max_w, track_em=None):
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


def text_layer(size, xy, text, style, font, track, color=None, effects=True):
    """One line of text, top-left at xy, painted like the text renderer paints `style` (glow, sticker, outline, shadow, fill).
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


def frame_canvas(W, H, amount, ss=2):
    """Full-size supersampled canvas for a frame decoration and its inset rectangle."""
    w, h = W * ss, H * ss
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ins = amount * w
    return SimpleNamespace(img=img, d=ImageDraw.Draw(img), w=w, h=h, mn=min(w, h), x0=ins, y0=ins, x1=w - ins, y1=h - ins)


def corners(x0, y0, x1, y1):
    return ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1))


class Shape:
    """Defaults = the plain business look (accent dot with a white ring, accent staff, corner brackets, round plate). Override what differs."""
    name = ""
    card_tile_colors = None            # (title, sub) RGBA override for text on bento tiles; None = what panel() returns

    # ---- panels ------------------------------------------------------------------------------------------------
    def panel(self, img, rect, th, u, key):
        """Paint a lower-third card / callout flag into img at rect. Returns (title colour, sub colour, effects): dark-on-paper panels give
        explicit ink colours and no text effects; dark/colourful panels let the style's own look (glow, outline) work."""
        raise NotImplementedError(f"shape '{self.name}' has no panel painter")

    def tile(self, layer, rect, th, H, key):
        """A bento tile on a card (a transparent layer the size of the card)."""
        res = self.panel(layer, rect, th, H, key)
        return self.card_tile_colors(th) if self.card_tile_colors else res

    # ---- lower third hooks --------------------------------------------------------------------------------------
    def lt_bar(self, u):
        """Width of a side bar the panel draws inside its left edge (the text starts after it)."""
        return 0

    def lt_decorate(self, canvas, th, tx, ty, tw, thh, u, W, title):
        """Extra marks around the lower-third title (a highlighter, a hairline...). Returns the canvas."""
        return canvas

    def sub_color(self, th):
        """Subtitle colour when the panel does not set one (None = the caption style's own)."""
        return None

    # ---- callout hooks ---------------------------------------------------------------------------------------------
    def callout_title(self, text):
        return text

    def staff(self, d, canvas, c):
        d.line([(c.ax, c.fy0 + (0 if c.up else c.ph)), (c.ax, c.ay)], fill=c.acc, width=c.lw)

    def marker(self, d, canvas, c):
        ax, ay, R, u, acc = c.ax, c.ay, c.R, c.u, c.acc
        sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).ellipse([ax - R, ay - R + 0.004 * u, ax + R, ay + R + 0.004 * u], fill=(0, 0, 0, 120))
        canvas.alpha_composite(_blur(sh, 0.005 * u))
        d.ellipse([ax - R, ay - R, ax + R, ay + R], fill=_c("#ffffff"))
        d.ellipse([ax - R * 0.62, ay - R * 0.62, ax + R * 0.62, ay + R * 0.62], fill=acc)

    # ---- frame -----------------------------------------------------------------------------------------------------
    def frame(self, W, H, amount, th):
        f = frame_canvas(W, H, amount)
        L, t, acc = 0.07 * f.h, max(3, int(0.0035 * f.h)), _c(th.accent)
        for cx, cy, sx, sy in corners(f.x0, f.y0, f.x1, f.y1):
            f.d.line([(cx, cy + sy * L), (cx, cy), (cx + sx * L, cy)], fill=acc, width=t, joint="curve")
        return f.img.resize((W, H), Image.LANCZOS)

    def letterbox_color(self, th):
        return themes.rgb(th.accent)

    # ---- icon plate ------------------------------------------------------------------------------------------------
    def plate(self, img, d, box, size, ss, fill, outline, ow, th):
        d.ellipse(box, fill=fill, outline=outline, width=max(1, int(size * ss * ow)) if outline else 0)

    # ---- whole components (a shape may replace the generic drivers entirely) ---------------------------------------
    def lower_third(self, W, H, title, subtitle, align, strict, th):
        return lower_third(self, W, H, title, subtitle, align, strict, th)

    def callout(self, W, H, title, subtitle, side, strict, th):
        return callout(self, W, H, title, subtitle, side, strict, th)


def of(th):
    """The shape plugin of a theme."""
    return registry.get("shape", th.shape)


def register(cls):
    """Class decorator: register one instance of a Shape subclass under its `name`."""
    registry.register("shape", cls.name, cls(), origin=cls.__module__)
    return cls


# ------------------------------------------------------------------------------------------------ generic drivers
def lower_third(shape, W, H, title, subtitle, align, strict, th):
    S = SS
    title = T.clean(title, th.title_style)
    subtitle = T.clean(subtitle, th.caption_style) if subtitle else ""
    if "\n" in title or "\n" in subtitle:
        raise ValueError("lower third title and subtitle must be single lines")
    if len(title) > 60 or len(subtitle) > 80:
        raise ValueError("lower third: title max 60 characters, subtitle max 80")
    u = H * S
    max_w = 0.78 * W * S
    ft = fit(title, th.title_style, 0.046 * u, max_w)
    fs = fit(subtitle, th.caption_style, 0.0185 * u, max_w) if subtitle else None
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
    bar = shape.lt_bar(u)
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
    tcol, scol, eff = shape.panel(canvas, (rx0, ry0, rx0 + pw, ry0 + ph), th, u, f"lt|{title}|{subtitle}")
    tx, ty = rx0 + pad + bar, ry0 + pad
    canvas = shape.lt_decorate(canvas, th, tx, ty, tw, thh, u, W, title)
    canvas = Image.alpha_composite(canvas, text_layer(canvas.size, (tx, ty), title.upper() if T.STYLES[th.title_style]["upper"] else title,
                                                      th.title_style, tf, ttr, tcol, eff))
    if sub:
        canvas = Image.alpha_composite(canvas, text_layer(canvas.size, (tx, ty + thh + gapv), sub_txt, th.caption_style, sub[0], sub[1],
                                                          scol or shape.sub_color(th), eff))
    canvas = canvas.resize((cw // S, ch // S), Image.LANCZOS)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if th.opt("lower_third", "scrim"):                            # a soft scrim so white text reads on bright footage
        sc = Image.linear_gradient("L").resize((W, int(0.3 * H))).point(lambda v: int(v * 0.52))
        scrim = Image.new("RGBA", (W, int(0.3 * H)), (0, 0, 0, 255)); scrim.putalpha(sc)
        img.paste(scrim, (0, H - int(0.3 * H)))
    img.alpha_composite(canvas, (max(0, ox), max(0, oy)), (max(0, -ox), max(0, -oy))) if ox < 0 or oy < 0 else img.alpha_composite(canvas, (ox, oy))
    return img


def callout(shape, W, H, title, subtitle, side, strict, th):
    S = SS
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
    ft = fit(title, th.title_style, 0.030 * u, max_w)
    fs = fit(subtitle, th.caption_style, 0.0135 * u, max_w) if subtitle else None
    if ft is None or (subtitle and fs is None):
        if strict:
            raise ValueError("callout text is too long to fit; shorten the title or subtitle")
        ft = ft or (T.make_font(th.title_style, 0.030 * u * T.MIN_SHRINK), 0)
        fs = fs or (T.make_font(th.caption_style, 0.0135 * u * T.MIN_SHRINK), 0)
    (tf, ttr), sub = ft, (fs if subtitle else None)
    up_t, up_s = T.STYLES[th.title_style]["upper"], T.STYLES[th.caption_style]["upper"]
    ttxt = shape.callout_title(title.upper() if up_t else title)
    stxt = subtitle.upper() if (up_s and subtitle) else subtitle
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
    key = f"co|{title}|{subtitle}|{side}"
    c = SimpleNamespace(ax=ax, ay=ay, R=R, u=u, lw=max(2, int(0.002 * u)), fy0=fy0, ph=ph, up=up, key=key, th=th, acc=_c(th.accent), ink=_c(th.ink))
    shape.staff(d, canvas, c)
    shape.marker(d, canvas, c)
    if th.opt("callout", "flag") == "plate":                       # no card in the template, but a faint dark plate keeps light text legible
        d.rounded_rectangle([fx0, fy0, fx0 + pw, fy0 + ph], radius=0.004 * u, fill=(0, 0, 0, 128))
        tcol, scol, eff = None, None, True
    else:
        tcol, scol, eff = shape.panel(canvas, (fx0, fy0, fx0 + pw, fy0 + ph), th, u, key)
    tx, ty = fx0 + pad, fy0 + pad
    canvas = Image.alpha_composite(canvas, text_layer(canvas.size, (tx, ty), ttxt, th.title_style, tf, ttr, tcol, eff))
    if sub:
        canvas = Image.alpha_composite(canvas, text_layer(canvas.size, (tx, ty + thh + gapv), stxt, th.caption_style, sub[0], sub[1],
                                                          scol or shape.sub_color(th), eff))
    ow, oh = max(2, int(round(iw / S))), max(2, int(round(ih / S)))
    return canvas.resize((ow, oh), Image.LANCZOS), ax / S, ay / S
