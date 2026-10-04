"""Title / section / quote / list / stat / outro cards in the project's template.

A card is a full-frame picture (procedural background + the theme's type) turned by ffmpeg into a short mp4 with a silent audio
track, so the editor treats it as any other source: add it with add_clip, dissolve into it with crossfade. No engine changes.
Everything is drawn with PIL (no numpy, no external images) and is deterministic: same card -> same pixels."""
import hashlib, os, random, subprocess

from PIL import Image, ImageDraw, ImageFilter

import sketch
import textrender as T
import themed
import themes

LAYOUTS = ("title", "section", "quote", "list", "stat", "outro")
MAX_ITEMS, MAX_ITEM_CHARS = 5, 60
LEFT = {"corporate", "tech", "minimal"}                    # templates whose cards are left-aligned; the others centre everything
c_ = themed._c

# per template: background kind, plain-text colours, whether the style's own effects (glow/outline/sticker) are the look
CARD = {
    "luxury":    dict(title="gold", sub="#cdbb94", accent="#d9b25a", fx=False),
    "corporate": dict(title="#ffffff", sub="#bcd2f5", accent="#4da3ff", fx=False),
    "academic":  dict(title="#1e2a3a", sub="#5a6472", accent="#7a1f2b", fx=False),
    "sketch":    dict(title="#222222", sub="#555555", accent="#e4572e", fx=False),
    "tech":      dict(title=None, sub=None, accent="#00e5ff", fx=True),
    "minimal":   dict(title="#111111", sub="#777777", accent="#ff5a36", fx=False),
    "playful":   dict(title=None, sub=None, accent="#ff6b6b", fx=True),
}


# ------------------------------------------------------------------------------------------------ backgrounds
def _vgrad(W, H, top, bottom):
    m = Image.linear_gradient("L").resize((W, H))
    return Image.composite(Image.new("RGB", (W, H), bottom), Image.new("RGB", (W, H), top), m)


def _radial(W, H, cx, cy, rad, color, alpha):
    g = Image.radial_gradient("L").resize((int(rad * 2), int(rad * 2)))          # white centre -> black edge: invert for a soft spot
    spot = Image.new("RGBA", g.size, color + (255,)); spot.putalpha(g.point(lambda v: int((255 - v) * alpha)))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0)); layer.paste(spot, (int(cx - rad), int(cy - rad)), spot)
    return layer


def background(W, H, th):
    kind = th.card_bg
    rg = random.Random(f"bg|{th.name}|{W}x{H}")
    if kind == "gradient":                                                        # corporate: navy to deep blue, a soft light band
        img = _vgrad(W, H, themes.rgb("#0b2545"), themes.rgb("#13315c")).convert("RGBA")
        img.alpha_composite(_radial(W, H, W * 0.85, H * 0.15, H * 0.9, themes.rgb("#1f6feb"), 0.35))
        d = ImageDraw.Draw(img)
        d.rectangle([0, int(H * 0.94), W, H], fill=c_(th.accent))
        return img
    if kind == "paper":                                                           # academic / minimal: paper with a light vignette and speckle
        base = Image.new("RGBA", (W, H), c_(th.paper))
        if th.name != "minimal":
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
    if kind == "dots":                                                            # sketch: paper, dot grid, a few doodles in the corners
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
    if kind == "dark_grid":                                                       # tech: dark, faint grid, cyan and magenta glows, corner brackets
        img = Image.new("RGBA", (W, H), c_(th.paper))
        img.alpha_composite(_radial(W, H, W * 0.15, H * 0.2, H * 0.9, themes.rgb(th.accent), 0.28))
        img.alpha_composite(_radial(W, H, W * 0.9, H * 0.9, H * 0.9, themes.rgb(th.accent2), 0.22))
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
    if kind == "confetti":                                                        # playful: warm paper with scattered confetti and a thick border
        img = Image.new("RGBA", (W, H), c_(th.paper))
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        cols = (th.accent, "#ffd93d", "#6bcb77", th.accent2)
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
    # marble (luxury): near-black, a warm centre glow, faint golden veins and the gold keyline
    img = Image.new("RGBA", (W, H), (8, 8, 11, 255))
    img.alpha_composite(_radial(W, H, W / 2, H * 0.45, max(W, H) * 0.7, (70, 52, 22), 0.5))
    veins = Image.new("RGBA", (W, H), (0, 0, 0, 0)); vd = ImageDraw.Draw(veins)
    for _ in range(7):
        x, y = rg.randrange(W), rg.randrange(H)
        pts = []
        for i in range(24):
            x += rg.uniform(-0.04, 0.06) * W; y += rg.uniform(-0.05, 0.05) * H
            pts.append((x, y))
        vd.line(pts, fill=(217, 178, 90, rg.randrange(28, 60)), width=max(1, int(0.002 * H)), joint="curve")
    img.alpha_composite(veins.filter(ImageFilter.GaussianBlur(0.0025 * H)))
    import graphics
    img.alpha_composite(graphics.frame(W, H, 0.035))
    return img


# ------------------------------------------------------------------------------------------------ text helpers
def _block(text, style, px, max_w, max_lines, strict=True):
    """Largest font <= px (down to 55%) in which `text` wraps into <= max_lines lines of max_w. -> (font, track, lines)."""
    st = T.STYLES[style]
    if st["upper"]:
        text = text.upper()
    cur = px
    while True:
        f = T.make_font(style, cur)
        track = st["tracking"] * f.size
        lines = T._wrap(text, f, max_w, track, 0)
        if len(lines) <= max_lines:
            return f, track, lines
        if cur * 0.93 < px * T.MIN_SHRINK:
            if strict:
                raise ValueError(f"'{text[:40]}…' does not fit in {max_lines} line(s) at this size; shorten it")
            return f, track, lines[:max_lines]
        cur *= 0.93


def _gold(size, xy, text, font, track):
    mask = Image.new("L", size, 0)
    T.draw_tracked(ImageDraw.Draw(mask), xy[0], xy[1], text, font, track, 255)
    top, hgt = int(xy[1]), max(1, int(sum(font.getmetrics())))
    g = Image.new("RGBA", size, (0, 0, 0, 0)); g.paste(T.gradient(size[0], hgt, T.GOLD), (0, top)); g.putalpha(mask)
    return g


def _line(canvas, th, xy, text, style, font, track, role, align, W):
    """Draw one line (left edge at xy[0] for left-aligned, centred on xy[0] otherwise). role: 'title' | 'sub' | 'accent'."""
    cp = CARD[th.name]
    w = T.text_width(text, font, track)
    x = xy[0] - (w / 2 if align == "center" else 0)
    if th.name == "luxury" and role == "title":
        layer = _gold(canvas.size, (x, xy[1]), text, font, track)
    else:
        col = cp["accent"] if role == "accent" else cp["title" if role == "title" else "sub"]
        layer = themed._text_layer(canvas.size, (x, xy[1]), text, style, font, track, c_(col) if col else None, cp["fx"])
    canvas.alpha_composite(layer)
    return w


def _heights(font, n):
    return (sum(font.getmetrics())) * 1.1 * n


def _decor_rule(canvas, th, x, y, w, align, H):
    """The template's signature divider between title and subtitle (x = centre for centred cards, left edge otherwise)."""
    ov = Image.new("RGBA", canvas.size, (0, 0, 0, 0))                          # translucent strokes on their own layer (ImageDraw replaces alpha)
    d = ImageDraw.Draw(ov)
    acc = c_(CARD[th.name]["accent"])
    t = max(2, int(0.004 * H))
    x0 = x - w / 2 if align == "center" else x
    if th.name == "luxury":
        d.line([(x0, y), (x0 + w, y)], fill=acc, width=max(2, int(0.0025 * H)))
        r = 0.011 * H
        cx = x0 + w / 2
        d.polygon([(cx, y - r), (cx + r, y), (cx, y + r), (cx - r, y)], fill=acc)
    elif th.name == "academic":
        d.line([(x0, y - t), (x0 + w, y - t)], fill=acc, width=max(1, t // 2))
        d.line([(x0, y + t), (x0 + w, y + t)], fill=acc, width=max(1, t // 2))
    elif th.name == "sketch":
        sketch.scribble_underline(d, x0, x0 + w, y, acc, max(3, 0.006 * H), sketch.rng("rule", x, y))
    elif th.name == "playful":
        for i, col in enumerate((th.accent, "#ffd93d", "#6bcb77", th.accent2)):
            cx = x0 + w * (i + 0.5) / 4
            d.ellipse([cx - 0.012 * H, y - 0.012 * H, cx + 0.012 * H, y + 0.012 * H], fill=c_(col), outline=c_(th.ink), width=max(2, int(0.003 * H)))
    elif th.name == "tech":
        d.line([(x0, y), (x0 + w, y)], fill=acc, width=t)
        d.ellipse([x0 + w - 0.01 * H, y - 0.01 * H, x0 + w + 0.01 * H, y + 0.01 * H], fill=c_(th.accent2))
    else:                                                                          # corporate / minimal: a short bar
        d.rectangle([x0, y - t, x0 + min(w, 0.14 * H * 2), y + t], fill=acc)
    canvas.alpha_composite(ov)


# ------------------------------------------------------------------------------------------------ the layouts
def render_card(layout, W, H, th, title="", subtitle="", items=(), number="", author="", strict=True):
    """Full-frame card as an RGBA PIL image. Raises ValueError (with a message to show verbatim) if text does not fit."""
    if layout not in LAYOUTS:
        raise ValueError(f"layout must be one of {LAYOUTS}")
    title, subtitle, author = (T.clean(title, th.title_style) if title else ""), (T.clean(subtitle, th.caption_style) if subtitle else ""), (T.clean(author, th.caption_style) if author else "")
    if not title and layout != "stat":
        raise ValueError("a card needs a title")
    canvas = background(W, H, th)
    align = "left" if th.name in LEFT else "center"
    ml = 0.10 * W                                                                  # left margin for left-aligned cards
    cx = ml if align == "left" else W / 2                                          # anchor x
    maxw = (0.80 * W) if align == "center" else (0.78 * W)
    ts, ss = th.title_style, th.caption_style

    def put(lines, font, track, y, role, style):
        for ln in lines:
            _line(canvas, th, (cx, y), ln, style, font, track, role, align, W)
            y += sum(font.getmetrics()) * 1.1
        return y

    if layout in ("title", "outro"):
        tf, ttr, tl = _block(title, ts, 0.115 * H, maxw, 3, strict)
        sf, sstr, sl = _block(subtitle, ss, 0.045 * H, 0.7 * W if align == "center" else maxw, 2, strict) if subtitle else (None, 0, [])
        gap = 0.05 * H
        total = _heights(tf, len(tl)) + (gap + _heights(sf, len(sl)) if sl else 0)
        y = (H - total) / 2 - 0.02 * H
        if th.name in ("corporate", "tech"):                                       # a vertical accent bar to the left of the block
            ImageDraw.Draw(canvas).rectangle([ml - 0.03 * W, y, ml - 0.03 * W + max(5, 0.006 * H), y + total], fill=c_(CARD[th.name]["accent"]))
        y = put(tl, tf, ttr, y, "title", ts)
        if sl:
            wmax = max(T.text_width(l, tf, ttr) for l in tl)
            _decor_rule(canvas, th, cx, y + gap * 0.45, min(wmax, 0.34 * W), align, H)
            put(sl, sf, sstr, y + gap, "sub", ss)
    elif layout == "section":
        nf, ntr, nl = _block(number or "01", ts, 0.30 * H, 0.5 * W, 1, strict)
        tf, ttr, tl = _block(title, ts, 0.09 * H, maxw, 2, strict)
        sf, sstr, sl = _block(subtitle, ss, 0.04 * H, 0.7 * W, 2, strict) if subtitle else (None, 0, [])
        total = _heights(nf, 1) + 0.02 * H + _heights(tf, len(tl)) + (0.03 * H + _heights(sf, len(sl)) if sl else 0)
        y = (H - total) / 2
        y = put(nl, nf, ntr, y, "accent", ts) + 0.02 * H
        y = put(tl, tf, ttr, y, "title", ts)
        if sl:
            put(sl, sf, sstr, y + 0.03 * H, "sub", ss)
    elif layout == "quote":
        qf = T.make_font(ts, 0.22 * H)
        tf, ttr, tl = _block(title, th.subtitle_style if th.subtitle_style in T.STYLES else ts, 0.062 * H, 0.74 * W if align == "center" else 0.7 * W, 5, strict)
        af, astr, al = _block(author, ss, 0.035 * H, 0.6 * W, 1, strict) if author else (None, 0, [])
        total = 0.15 * H + _heights(tf, len(tl)) + (0.05 * H + _heights(af, 1) if al else 0)
        y = (H - total) / 2
        _line(canvas, th, (cx, y - 0.05 * H), "“", ts, qf, 0, "accent", align, W)
        y = put(tl, tf, ttr, y + 0.15 * H, "title", th.subtitle_style if th.subtitle_style in T.STYLES else ts)
        if al:
            put([("— " + author)], af, astr, y + 0.05 * H, "sub", ss)
    elif layout == "list":
        its = [T.clean(i, th.caption_style) for i in items]
        if not 1 <= len(its) <= MAX_ITEMS or any(len(i) > MAX_ITEM_CHARS or not i for i in its):
            raise ValueError(f"a list card needs 1-{MAX_ITEMS} items of at most {MAX_ITEM_CHARS} characters")
        tf, ttr, tl = _block(title, ts, 0.085 * H, maxw, 2, strict)
        rows = []
        for it in its:
            f, tr, ls = _block(it, ss, 0.052 * H, 0.66 * W, 1, strict)
            rows.append((f, tr, ls[0]))
        gap = 0.032 * H
        total = _heights(tf, len(tl)) + 0.05 * H + sum(_heights(r[0], 1) + gap for r in rows)
        y = (H - total) / 2
        y = put(tl, tf, ttr, y, "title", ts) + 0.05 * H
        left = (W * 0.5 - 0.33 * W) if align == "center" else ml
        d = ImageDraw.Draw(canvas)
        acc = c_(CARD[th.name]["accent"])
        for f, tr, ln in rows:
            hh = sum(f.getmetrics())
            rr = 0.011 * H
            d.ellipse([left, y + hh * 0.5 - rr, left + 2 * rr, y + hh * 0.5 + rr], fill=acc)
            _line(canvas, th, (left + 0.045 * H + 2 * rr, y), ln, ss, f, tr, "sub" if th.name not in ("luxury", "corporate") else "title" if th.name == "corporate" else "sub", "left", W)
            y += hh * 1.1 + gap
    else:                                                                          # stat: a big figure and its label
        fig = T.clean(number if number else title, th.title_style)
        label = subtitle if subtitle else (title if number else "")
        if not fig or len(fig) > 12:
            raise ValueError("a stat card needs a short figure (number) of at most 12 characters")
        nf, ntr, nl = _block(fig, ts, 0.30 * H, 0.8 * W, 1, strict)
        lf, lstr, ll = _block(label, ss, 0.052 * H, 0.7 * W, 2, strict) if label else (None, 0, [])
        total = _heights(nf, 1) + (0.01 * H + _heights(lf, len(ll)) if ll else 0)
        y = (H - total) / 2
        y = put(nl, nf, ntr, y, "accent" if th.name not in ("luxury", "tech", "playful") else "title", ts)
        if ll:
            put(ll, lf, lstr, y - 0.03 * H, "sub", ss)
    return canvas


# ------------------------------------------------------------------------------------------------ png + mp4
def card_png(cache_dir, layout, W, H, th, **kw):
    key = repr((layout, W, H, th.name, th.accent, sorted(kw.items())))
    out = os.path.join(cache_dir, f"card_{hashlib.sha1(('v1|' + key).encode()).hexdigest()[:16]}.png")
    if not os.path.exists(out):
        img = render_card(layout, W, H, th, **kw)
        os.makedirs(cache_dir, exist_ok=True)
        tmp = out + f".{os.getpid()}.tmp"
        img.convert("RGB").save(tmp, format="PNG")
        os.replace(tmp, out)
    return out


def card_video(png, out, W, H, fps, dur_s, push=False):
    """PNG -> mp4 (H.264 + silent AAC) of dur_s seconds. push=True adds a slow 6% zoom-in on a 2x supersampled copy."""
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    tmp = out + f".{os.getpid()}.tmp.mp4"
    n = max(1, int(round(dur_s * fps)))
    vf = (f"scale={W * 2}:{H * 2}:flags=lanczos,zoompan=z='min(zoom+{0.06 / n:.6f},1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={fps}"
          if push else f"scale={W}:{H}:flags=lanczos")
    cmd = ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", str(fps), "-i", png, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
           "-vf", vf + ",format=yuv420p", "-frames:v", str(n), "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-c:a", "aac", "-b:a", "96k",
           "-shortest", "-movflags", "+faststart", tmp]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode or not os.path.exists(tmp):
        raise RuntimeError("ffmpeg could not make the card video: " + r.stderr.strip()[-300:])
    os.replace(tmp, out)
    return out
